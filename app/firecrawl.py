"""Optional Firecrawl-backed crawler.

Firecrawl (https://www.firecrawl.dev) renders pages, follows same-site links and
returns clean markdown/HTML. It is useful for JavaScript-heavy or bot-protected
public tender sites. BidGPT still indexes the returned pages itself, so questions
are answered only from the per-site index - Firecrawl is used at crawl time and
is never contacted when a visitor asks a question.

Enable it with an API key:

    FIRECRAWL_API_KEY=fc-...
    CRAWLER_BACKEND=auto        # auto | local | firecrawl

In `auto` mode Firecrawl is used when a key is present, except for private or
loopback hosts (which Firecrawl cannot reach and which the local crawler serves
for tests and the local demo).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from .config import Settings
from .crawler import CrawlError, CrawlResult
from .extract import extract_fields_from_text, parse_html
from .index import PageRecord
from .security import sanitize_text
from .urls import is_login_url, is_skippable_url, normalize_url, same_site
from urllib.parse import urlparse

log = logging.getLogger("bidgpt.firecrawl")


class FirecrawlError(RuntimeError):
    """A Firecrawl request failed or the crawl job did not finish."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _metadata_title(metadata: dict[str, Any]) -> str | None:
    title = metadata.get("title")
    if isinstance(title, list):
        title = next((item for item in title if item), None)
    if not isinstance(title, str):
        return None
    cleaned = " ".join(title.split())
    return cleaned[:300] or None


class FirecrawlCrawler:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if not settings.firecrawl_api_key.strip():
            raise FirecrawlError(
                "FIRECRAWL_API_KEY is not set. Add it to .env or use "
                "CRAWLER_BACKEND=local."
            )
        self.settings = settings
        self._transport = transport

    async def crawl(
        self,
        start_url: str,
        *,
        progress: Callable[[int], None] | None = None,
    ) -> CrawlResult:
        started = time.monotonic()
        normalized = normalize_url(start_url)
        if not normalized:
            raise ValueError("Invalid crawl URL")
        base_host = urlparse(normalized).hostname or ""

        payload = {
            "url": normalized,
            "limit": self.settings.max_pages,
            "maxDiscoveryDepth": self.settings.crawl_depth,
            "sitemap": "include",
            "ignoreQueryParameters": True,
            "scrapeOptions": {
                "formats": ["markdown", "html"],
                "onlyMainContent": True,
                "blockAds": True,
                "removeBase64Images": True,
                "timeout": int(self.settings.request_timeout_seconds * 1000),
            },
        }
        headers = {
            "Authorization": f"Bearer {self.settings.firecrawl_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        timeout = httpx.Timeout(
            max(30.0, self.settings.firecrawl_timeout_seconds),
            connect=20.0,
        )

        documents: list[dict[str, Any]] = []
        async with httpx.AsyncClient(
            base_url=self.settings.firecrawl_api_url,
            headers=headers,
            timeout=timeout,
            transport=self._transport,
        ) as client:
            response = await client.post("/crawl", json=payload)
            job = self._json(response, "start crawl")
            job_id = job.get("id")
            if not job_id:
                raise FirecrawlError("Firecrawl did not return a crawl job id")

            next_url: str = f"/crawl/{job_id}"
            status = "scraping"
            deadline = time.monotonic() + self.settings.firecrawl_timeout_seconds
            while True:
                if time.monotonic() > deadline:
                    if documents:
                        log.warning(
                            "Firecrawl crawl reached timeout (%ds) with %d pages collected; proceeding with collected pages",
                            int(self.settings.firecrawl_timeout_seconds),
                            len(documents),
                        )
                        break
                    raise FirecrawlError(
                        "Firecrawl crawl timed out after "
                        f"{int(self.settings.firecrawl_timeout_seconds)} seconds"
                    )
                if self.settings.firecrawl_poll_seconds > 0:
                    await asyncio.sleep(self.settings.firecrawl_poll_seconds)
                response = await client.get(next_url)
                body = self._json(response, "poll crawl")
                status = str(body.get("status") or status)
                documents.extend(
                    item for item in (body.get("data") or []) if isinstance(item, dict)
                )
                if progress is not None:
                    try:
                        progress(len(documents))
                    except Exception:  # progress reporting must not break a crawl
                        log.exception("crawl progress callback failed")
                if status in {"failed", "cancelled"}:
                    raise FirecrawlError(
                        str(body.get("error") or f"Firecrawl crawl {status}")
                    )
                if status == "completed":
                    break
                # `next` is a pagination cursor for large result sets. When it
                # is absent the job is merely still running, so keep polling the
                # job status URL instead of treating that as completion.
                next_url = body.get("next") or f"/crawl/{job_id}"

        pages, errors = self._to_pages(documents, base_host)
        if status not in {"completed"} and not pages:
            raise FirecrawlError(f"Firecrawl crawl ended with status {status}")

        return CrawlResult(
            start_url=normalized,
            pages=pages,
            errors=errors,
            fetched=len(documents),
            discovered=len(documents),
            robots_blocked=0,
            duration_seconds=round(time.monotonic() - started, 3),
        )

    def _to_pages(
        self, documents: list[dict[str, Any]], base_host: str
    ) -> tuple[list[PageRecord], list[CrawlError]]:
        pages: list[PageRecord] = []
        errors: list[CrawlError] = []
        seen: set[str] = set()

        for document in documents:
            metadata = document.get("metadata") or {}
            if not isinstance(metadata, dict):
                metadata = {}
            raw_url = metadata.get("url") or metadata.get("sourceURL") or ""
            url = normalize_url(str(raw_url))
            if not url:
                errors.append(CrawlError(str(raw_url) or "unknown", "missing page URL"))
                continue
            if not same_site(urlparse(url).hostname, base_host):
                errors.append(CrawlError(url, "skipped: different site"))
                continue
            if is_skippable_url(url) or is_login_url(url):
                errors.append(CrawlError(url, "skipped: login-protected URL"))
                continue
            if url in seen:
                continue
            seen.add(url)

            error = metadata.get("error")
            if error:
                errors.append(CrawlError(url, f"Firecrawl: {error}"))
                continue

            html = document.get("html") or ""
            markdown = document.get("markdown") or ""
            title_hint = _metadata_title(metadata)
            if html:
                parsed = parse_html(html, url)
                if parsed.is_empty_listing and not parsed.cards:
                    errors.append(CrawlError(url, "skipped: empty listing page"))
                    continue
                if parsed.cards:
                    for card in parsed.cards:
                        pages.append(
                            PageRecord(
                                url=card.url,
                                title=sanitize_text(card.title, 300) if card.title else None,
                                text=card.text,
                                tender_type=card.fields.tender_type,
                                location=card.fields.location,
                                budget=card.fields.budget,
                                timeline=card.fields.timeline,
                                products=card.fields.products,
                                department=card.fields.department,
                                bid_by_date=card.fields.bid_by_date,
                                crawled_at=_utcnow(),
                                content_hash=hashlib.sha256(
                                    card.text.encode("utf-8", "ignore")
                                ).hexdigest(),
                                tender_id=card.fields.tender_id,
                                emd=card.fields.emd,
                                emd_exemption=card.fields.emd_exemption,
                                ecv=card.fields.ecv,
                                state=card.fields.state,
                            )
                        )
                    continue
                title = parsed.title or title_hint
                text = parsed.text
                fields = parsed.fields
            else:
                text = sanitize_text(markdown, self.settings.max_text_chars)
                title = title_hint
                fields = extract_fields_from_text(
                    text,
                    title_hint=title_hint,
                    page_url=url,
                )

            if not text.strip() and not title:
                errors.append(CrawlError(url, "skipped: no readable content"))
                continue

            pages.append(
                PageRecord(
                    url=url,
                    title=sanitize_text(title, 300) if title else None,
                    text=sanitize_text(text, self.settings.max_text_chars),
                    tender_type=fields.tender_type,
                    location=fields.location,
                    budget=fields.budget,
                    timeline=fields.timeline,
                    products=fields.products,
                    department=fields.department,
                    bid_by_date=fields.bid_by_date,
                    crawled_at=_utcnow(),
                    content_hash=hashlib.sha256(
                        text.encode("utf-8", "ignore")
                    ).hexdigest(),
                    tender_id=fields.tender_id,
                    emd=fields.emd,
                    emd_exemption=fields.emd_exemption,
                    ecv=fields.ecv,
                    state=fields.state,
                )
            )
        return pages, errors

    @staticmethod
    def _json(response: httpx.Response, action: str) -> dict[str, Any]:
        if response.status_code >= 400:
            detail = sanitize_text(response.text, 300) or response.reason_phrase
            raise FirecrawlError(
                f"Firecrawl could not {action} (HTTP {response.status_code}): {detail}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise FirecrawlError(f"Firecrawl returned invalid JSON for {action}") from exc
        if not isinstance(body, dict):
            raise FirecrawlError(f"Firecrawl returned an unexpected response for {action}")
        return body
