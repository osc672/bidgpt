"""Polite same-domain crawler with Playwright enhancement and PDF support."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
import urllib.robotparser
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader

from .config import Settings
from .extract import parse_html, parse_pdf_text
from .index import PageRecord
from .security import sanitize_text
from .urls import (
    is_login_url,
    is_skippable_url,
    host_is_private,
    normalize_url,
    same_site,
)

log = logging.getLogger("bidgpt.crawler")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True)
class CrawlError:
    url: str
    error: str

    def public(self) -> dict[str, str]:
        return {"url": self.url, "error": self.error}


@dataclass(slots=True)
class CrawlResult:
    start_url: str
    pages: list[PageRecord]
    errors: list[CrawlError]
    fetched: int
    discovered: int
    robots_blocked: int
    duration_seconds: float

    @property
    def ok(self) -> bool:
        return bool(self.pages)


class Crawler:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._browser = None
        self._playwright = None
        self._context = None
        self._delay = max(0.0, settings.request_delay_seconds)
        self._last_request = 0.0

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
        base = urlparse(normalized)
        base_host = base.hostname

        pages: list[PageRecord] = []
        errors: list[CrawlError] = []
        robots_blocked = 0
        fetched = 0
        seen: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(normalized, 0)])

        timeout = httpx.Timeout(self.settings.request_timeout_seconds)
        headers = {
            "User-Agent": self.settings.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/pdf,text/plain;q=0.8,*/*;q=0.5",
            "Accept-Language": "en-IN,en;q=0.9",
        }
        async with httpx.AsyncClient(
            headers=headers, timeout=timeout, follow_redirects=False, verify=True
        ) as client:
            robots = await self._load_robots(client, normalized, base_host or "")
            self._delay = max(self.settings.request_delay_seconds, robots.delay)
            try:
                while queue and fetched < self.settings.max_pages:
                    url, depth = queue.popleft()
                    if url in seen:
                        continue
                    seen.add(url)
                    if not same_site(urlparse(url).hostname, base_host):
                        continue
                    if is_skippable_url(url):
                        continue
                    if is_login_url(url):
                        self._record_error(errors, url, "skipped: login-protected URL")
                        continue
                    if not self._robots_allowed(robots.parser, url):
                        robots_blocked += 1
                        continue

                    fetched += 1
                    try:
                        response = await self._get(client, url, base_host or "")
                    except Exception as exc:  # one bad URL must not stop the crawl
                        self._record_error(
                            errors, url, f"fetch failed: {type(exc).__name__}: {exc}"
                        )
                        continue

                    if response is None:
                        self._record_error(
                            errors, url, "fetch failed: too many redirects or blocked redirect"
                        )
                        continue
                    if response.status_code >= 400:
                        self._record_error(errors, url, f"HTTP {response.status_code}")
                        continue
                    content_type = response.headers.get("content-type", "").split(";")[0].lower()
                    final_url = normalize_url(str(response.url)) or url
                    is_pdf = (
                        content_type == "application/pdf"
                        or urlparse(final_url).path.lower().endswith(".pdf")
                    )
                    size_limit = (
                        self.settings.max_pdf_bytes
                        if is_pdf
                        else self.settings.max_response_bytes
                    )
                    if len(response.content) > size_limit:
                        self._record_error(
                            errors,
                            url,
                            "PDF larger than MAX_PDF_BYTES"
                            if is_pdf
                            else "response larger than MAX_RESPONSE_BYTES",
                        )
                        continue

                    try:
                        extracted = await self._process_response(
                            client, response, final_url, content_type, depth, queue, seen, base_host or ""
                        )
                    except Exception as exc:
                        self._record_error(
                            errors, url, f"parse failed: {type(exc).__name__}: {exc}"
                        )
                        continue
                    if extracted:
                        pages.extend(extracted)
                        if progress is not None:
                            try:
                                progress(len(pages))
                            except Exception:  # progress reporting must not break crawl
                                log.exception("crawl progress callback failed")
            finally:
                await self._close_browser()

        return CrawlResult(
            start_url=normalized,
            pages=pages,
            errors=errors,
            fetched=fetched,
            discovered=len(seen),
            robots_blocked=robots_blocked,
            duration_seconds=round(time.monotonic() - started, 3),
        )

    async def _process_response(
        self,
        client: httpx.AsyncClient,
        response: httpx.Response,
        final_url: str,
        content_type: str,
        depth: int,
        queue: deque[tuple[str, int]],
        seen: set[str],
        base_host: str,
    ) -> list[PageRecord]:
        path = urlparse(final_url).path.lower()
        if content_type == "application/pdf" or path.endswith(".pdf"):
            if len(response.content) > self.settings.max_pdf_bytes:
                raise ValueError("PDF larger than MAX_PDF_BYTES")
            text = self._pdf_text(response.content)
            if not text.strip():
                return []
            fields = parse_pdf_text(text, final_url)
            title = fields.title or (text.strip().splitlines()[0] if text.strip() else None)
            return [
                PageRecord(
                    url=final_url,
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
                    content_hash=hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest(),
                    tender_id=fields.tender_id,
                    emd=fields.emd,
                    emd_exemption=fields.emd_exemption,
                    ecv=fields.ecv,
                    state=fields.state,
                )
            ]

        if content_type and not (
            "html" in content_type
            or content_type in {"text/plain", "application/xhtml+xml", ""}
        ):
            return []

        html_text = response.text
        parsed = parse_html(html_text, final_url)
        if self._looks_login_protected(
            html_text, final_url, parsed.title, parsed.text
        ):
            raise ValueError(
                "login-protected page skipped (BidGPT indexes public pages only)"
            )
        if self._looks_js_shell(html_text, parsed.text) and self.settings.playwright_enabled:
            rendered = await self._render(final_url, base_host)
            if rendered:
                rendered_parsed = parse_html(rendered, final_url)
                if len(rendered_parsed.text) > len(parsed.text):
                    parsed = rendered_parsed

        if depth < self.settings.crawl_depth:
            tender_links: list[str] = []
            other_links: list[str] = []
            for link in parsed.links:
                candidate = normalize_url(link, final_url)
                if not candidate or candidate in seen:
                    continue
                if not same_site(urlparse(candidate).hostname, base_host):
                    continue
                if is_skippable_url(candidate) or is_login_url(candidate):
                    continue
                if not self.settings.allow_private_urls and host_is_private(
                    urlparse(candidate).hostname or ""
                ):
                    continue
                cand_lower = candidate.lower()
                if any(k in cand_lower for k in ("tender", "bid", "detail", "notice", "nit", "view")):
                    tender_links.append(candidate)
                else:
                    other_links.append(candidate)

            for cand in reversed(tender_links):
                queue.appendleft((cand, depth + 1))
            for cand in other_links:
                queue.append((cand, depth + 1))

        # 1. Skip empty listing pages (pages that explicitly say "no tenders found" and have 0 cards)
        if parsed.is_empty_listing and not parsed.cards:
            log.info("Skipping empty listing page: %s", final_url)
            return []

        # 2. Skip mixed-state listing pages — these contain tenders from many states and
        #    cannot be attributed to any single state. They are still crawled for link discovery.
        if not parsed.cards and self._is_mixed_state_listing(parsed.text):
            log.info("Skipping mixed-state listing page (link discovery only): %s", final_url)
            return []

        # 2. If the page contains discrete tender cards (e.g. state or category listing), index each tender card individually
        if parsed.cards:
            records: list[PageRecord] = []
            for card in parsed.cards:
                records.append(
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
                        content_hash=hashlib.sha256(card.text.encode("utf-8", "ignore")).hexdigest(),
                        tender_id=card.fields.tender_id,
                        emd=card.fields.emd,
                        emd_exemption=card.fields.emd_exemption,
                        ecv=card.fields.ecv,
                        state=card.fields.state,
                    )
                )
            return records

        if not parsed.text.strip() and not parsed.title:
            return []

        fields = parsed.fields
        return [
            PageRecord(
                url=final_url,
                title=fields.title or parsed.title,
                text=parsed.text,
                tender_type=fields.tender_type,
                location=fields.location,
                budget=fields.budget,
                timeline=fields.timeline,
                products=fields.products,
                department=fields.department,
                bid_by_date=fields.bid_by_date,
                crawled_at=_utcnow(),
                content_hash=hashlib.sha256(parsed.text.encode("utf-8", "ignore")).hexdigest(),
                tender_id=fields.tender_id,
                emd=fields.emd,
                emd_exemption=fields.emd_exemption,
                ecv=fields.ecv,
                state=fields.state,
            )
        ]

    async def _get(
        self, client: httpx.AsyncClient, url: str, base_host: str
    ) -> httpx.Response | None:
        current = url
        for _ in range(6):
            await self._rate_limit()
            response = await client.get(current)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    return response
                current = normalize_url(
                    urljoin(current, location), preserve_trailing_slash=True
                )
                if not current:
                    return None
                if not same_site(urlparse(current).hostname, base_host):
                    return None
                if not self.settings.allow_private_urls and host_is_private(
                    urlparse(current).hostname or ""
                ):
                    return None
                continue
            return response
        return None

    async def _rate_limit(self) -> None:
        if self._delay <= 0:
            return
        now = time.monotonic()
        wait = self._delay - (now - self._last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request = time.monotonic()

    @staticmethod
    def _record_error(
        errors: list[CrawlError], url: str, message: str
    ) -> None:
        """Keep a per-URL error for the status API and the service log."""
        errors.append(CrawlError(url, message))
        log.warning("crawl error url=%s: %s", url, message)

    @staticmethod
    def _is_mixed_state_listing(text: str) -> bool:
        """Return True if the page text contains 3+ distinct Indian states.

        Such pages are multi-state listing/search-result pages that cannot be
        attributed to a single state and must NOT be indexed as individual
        tender records — they exist only for link discovery.
        """
        from .geo import CANONICAL_STATES
        text_lower = text.lower()
        found_states: list[str] = []
        for state in CANONICAL_STATES:
            if state.lower() in text_lower:
                found_states.append(state)
                if len(found_states) >= 3:
                    return True
        return False

    # ------------------------------------------------------------------ robots
    async def _load_robots(self, client, start_url: str, base_host: str):
        parsed = urlparse(start_url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

        class Robots:
            parser: urllib.robotparser.RobotFileParser
            delay: float = 0.0

        result = Robots()
        result.parser = urllib.robotparser.RobotFileParser()
        result.parser.set_url(robots_url)
        try:
            response = await client.get(robots_url)
            if response.status_code == 200:
                result.parser.parse(response.text.splitlines())
                crawl_delay = result.parser.crawl_delay(self.settings.user_agent)
                result.delay = min(5.0, float(crawl_delay or 0))
            else:
                result.parser.parse([])
        except Exception:
            # If robots.txt cannot be fetched, standard practice is to proceed.
            result.parser.parse([])
        return result

    def _robots_allowed(
        self, parser: urllib.robotparser.RobotFileParser, url: str
    ) -> bool:
        try:
            return parser.can_fetch(self.settings.user_agent, url)
        except Exception:
            return True

    # ------------------------------------------------------------------ PDF
    def _pdf_text(self, data: bytes) -> str:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                return ""
        chunks: list[str] = []
        for page in reader.pages[: self.settings.max_pdf_pages]:
            try:
                chunks.append(page.extract_text() or "")
            except Exception:
                continue
        return sanitize_text("\n".join(chunks), self.settings.max_text_chars)

    # ------------------------------------------------------------------ Playwright
    _LOGIN_TITLE_MARKERS = ("login", "log in", "sign in", "signin", "sign-in")

    def _looks_login_protected(
        self, html_text: str, url: str, title: str | None, text: str
    ) -> bool:
        """Return True only for pages that are actually a login screen.

        Many public tender sites embed a small login form in the header. Older
        versions of this check rejected any page containing a password field,
        which wrongly dropped those public pages from the index. Require an
        extra login signal: a login URL, a login page title, or a page with
        essentially no content besides the form.
        """
        if "password" not in html_text.lower():
            return False
        if not re.search(r"""type\s*=\s*["']password["']""", html_text, re.I):
            return False
        if is_login_url(url):
            return True
        lowered_title = (title or "").lower()
        if any(marker in lowered_title for marker in self._LOGIN_TITLE_MARKERS):
            return True
        return len(text.strip()) < 400

    def _looks_js_shell(self, html_text: str, text: str) -> bool:
        lower = text.lower()
        markers = ("loading", "please wait", "enable javascript", "javascript is required")
        if any(marker in lower for marker in markers):
            return True
        if len(text.strip()) < 400 and ("<script" in html_text.lower() or 'id="app"' in html_text.lower()):
            return True
        return False

    async def _render(self, url: str, base_host: str) -> str | None:
        # Match the test setup: use a repo-local browser cache when one exists
        # and the operator has not chosen a path explicitly. This keeps the
        # browser off a full system drive on constrained machines.
        local_browsers = Path(__file__).resolve().parent.parent / ".playwright"
        if local_browsers.is_dir() and not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(local_browsers)
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:
            log.warning("Playwright unavailable: %s", exc)
            return None
        try:
            if self._browser is None:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
                )
                self._context = await self._browser.new_context(
                    user_agent=self.settings.user_agent,
                    viewport={"width": 1366, "height": 900},
                    java_script_enabled=True,
                    timezone_id="Asia/Kolkata",
                    locale="en-IN",
                )
            assert self._context is not None
            page = await self._context.new_page()

            async def route_handler(route):
                request_url = route.request.url
                if request_url.startswith(("data:", "blob:", "about:")):
                    await route.continue_()
                    return
                req_parsed = urlparse(request_url)
                if not same_site(req_parsed.hostname, base_host):
                    await route.abort()
                    return
                if not self.settings.allow_private_urls and host_is_private(
                    req_parsed.hostname or ""
                ):
                    await route.abort()
                    return
                await route.continue_()

            await page.route("**/*", route_handler)
            try:
                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=int(self.settings.request_timeout_seconds * 1000),
                )
                try:
                    await page.wait_for_load_state("networkidle", timeout=4_000)
                except Exception:
                    pass
                await page.wait_for_timeout(self.settings.playwright_wait_ms)
                return await page.content()
            finally:
                await page.close()
        except Exception as exc:
            log.warning("Playwright render failed for %s: %s", url, exc)
            return None

    async def _close_browser(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
            self._context = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
