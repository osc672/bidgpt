from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.crawl import build_crawler
from app.crawler import Crawler
from app.firecrawl import FirecrawlCrawler, FirecrawlError

HTML_PAGE = """<!doctype html>
<html><head><title>Guntur Housekeeping Tender</title></head><body>
<div>Tender Type: Housekeeping Services</div>
<div>Location: Guntur, Andhra Pradesh</div>
<div>Budget: INR 8,40,000</div>
<div>Timeline: 12 months</div>
<div>Products/Scope: Housekeeping and sanitation services</div>
<div>Department: Andhra Pradesh Health Department</div>
<div>Last Date to Bid: 05-11-2026</div>
</body></html>
"""

MARKDOWN_PAGE = """# Hyderabad Road Widening Tender
Tender Type: Civil Works
Location: Hyderabad, Telangana
Budget: Rs. 2.4 crore
Timeline: 18 months
Last Date to Bid: 15 November 2026
"""


def _settings(tmp_path, **overrides):
    values = dict(
        data_dir=tmp_path,
        firecrawl_api_key="fc-test",
        firecrawl_api_url="https://api.firecrawl.dev/v2",
        firecrawl_poll_seconds=0.0,
        max_pages=7,
        crawl_depth=1,
        request_delay_seconds=0.0,
    )
    values.update(overrides)
    return Settings(**values)


@pytest.mark.asyncio
async def test_firecrawl_crawl_converts_html_and_markdown(tmp_path):
    requests: list[httpx.Request] = []
    polls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST" and request.url.path == "/v2/crawl":
            payload = json.loads(request.content)
            assert payload["url"] == "https://tenders.example.com/"
            assert payload["limit"] == 7
            assert payload["maxDiscoveryDepth"] == 1
            assert "markdown" in payload["scrapeOptions"]["formats"]
            assert request.headers["authorization"] == "Bearer fc-test"
            return httpx.Response(200, json={"success": True, "id": "job-1"})
        if request.url.path == "/v2/crawl/job-1":
            polls["count"] += 1
            if polls["count"] == 1:
                return httpx.Response(
                    200,
                    json={
                        "status": "scraping",
                        "data": [
                            {
                                "html": HTML_PAGE,
                                "metadata": {
                                    "url": "https://tenders.example.com/guntur-housekeeping",
                                    "title": "Guntur Housekeeping Tender",
                                },
                            }
                        ],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "data": [
                        {
                            "markdown": MARKDOWN_PAGE,
                            "metadata": {
                                "url": "https://tenders.example.com/hyderabad-civil",
                                "title": ["Hyderabad Road Widening Tender"],
                            },
                        }
                    ],
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    crawler = FirecrawlCrawler(
        _settings(tmp_path), transport=httpx.MockTransport(handler)
    )
    progress: list[int] = []
    result = await crawler.crawl(
        "https://tenders.example.com/", progress=progress.append
    )

    assert result.ok
    assert result.fetched == 2
    assert len(result.pages) == 2
    assert progress
    assert polls["count"] == 2
    by_url = {page.url: page for page in result.pages}

    guntur = by_url["https://tenders.example.com/guntur-housekeeping"]
    assert guntur.title == "Guntur Housekeeping Tender"
    assert guntur.tender_type == "Housekeeping Services"
    assert guntur.location and "Guntur" in guntur.location
    assert guntur.bid_by_date == "2026-11-05"
    assert guntur.budget and "8,40,000" in guntur.budget

    hyderabad = by_url["https://tenders.example.com/hyderabad-civil"]
    assert hyderabad.title == "Hyderabad Road Widening Tender"
    assert hyderabad.tender_type == "Civil Works"
    assert hyderabad.location and "Hyderabad" in hyderabad.location
    assert hyderabad.bid_by_date == "2026-11-15"


@pytest.mark.asyncio
async def test_firecrawl_failed_job_raises(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"success": True, "id": "job-2"})
        return httpx.Response(200, json={"status": "failed", "error": "blocked"})

    crawler = FirecrawlCrawler(
        _settings(tmp_path), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(FirecrawlError, match="blocked"):
        await crawler.crawl("https://tenders.example.com/")


def test_firecrawl_api_error_includes_status(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid key"})

    crawler = FirecrawlCrawler(
        _settings(tmp_path), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(FirecrawlError, match="HTTP 401"):
        import asyncio

        asyncio.run(crawler.crawl("https://tenders.example.com/"))


def test_backend_selection_prefers_local_for_private_hosts(tmp_path):
    public = build_crawler(_settings(tmp_path), "https://tenders.example.com/")
    assert isinstance(public, FirecrawlCrawler)

    private = build_crawler(_settings(tmp_path), "http://127.0.0.1:9000/")
    assert isinstance(private, Crawler)

    no_key = build_crawler(
        _settings(tmp_path, firecrawl_api_key=""), "https://tenders.example.com/"
    )
    assert isinstance(no_key, Crawler)

    forced = _settings(tmp_path, crawler_backend="firecrawl")
    assert isinstance(
        build_crawler(forced, "https://tenders.example.com/"), FirecrawlCrawler
    )

    with pytest.raises(FirecrawlError, match="FIRECRAWL_API_KEY"):
        FirecrawlCrawler(_settings(tmp_path, firecrawl_api_key=""))
