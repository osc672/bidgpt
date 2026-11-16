from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.crawler import Crawler
from app.urls import normalize_url

from .fake_site import FakeSite


@pytest.mark.asyncio
async def test_crawler_three_pages_js_and_pdf(tmp_path):
    site = FakeSite()
    try:
        settings = Settings(
            data_dir=tmp_path,
            allow_private_urls=True,
            playwright_enabled=True,
            request_delay_seconds=0.0,
            max_pages=20,
            crawl_depth=2,
        )
        result = await Crawler(settings).crawl(site.url)
    finally:
        site.stop()

    assert result.fetched == 3, result.errors
    assert len(result.pages) == 3, result.errors
    assert result.errors == []
    assert result.robots_blocked == 0

    by_url = {page.url: page for page in result.pages}
    assert site.url + "/" in by_url or site.url in by_url
    assert any(url.endswith("/hyderabad-civil.html") for url in by_url)
    assert any(url.endswith("/guntur-housekeeping.pdf") for url in by_url)

    js_page = next(page for url, page in by_url.items() if url.endswith("hyderabad-civil.html"))
    assert "Hyderabad Road Widening" in (js_page.title or "")
    assert js_page.location == "Hyderabad, Telangana"
    assert js_page.tender_type == "Civil Works"
    assert js_page.budget and "2.4" in js_page.budget
    assert "Road widening" in (js_page.products or "")

    pdf_page = next(page for url, page in by_url.items() if url.endswith(".pdf"))
    assert "Housekeeping" in (pdf_page.title or "")
    assert pdf_page.location and "Guntur" in pdf_page.location
    assert pdf_page.bid_by_date == "2026-11-05"
    assert "sanitation" in (pdf_page.products or "").lower()

    home = next(page for url, page in by_url.items() if url.rstrip("/") == site.url)
    assert home.tender_type == "Electrical Works"
    assert home.bid_by_date == "2026-10-31"
    assert home.budget and "12,50,000" in home.budget


def test_password_field_in_header_is_not_a_login_page(tmp_path):
    crawler = Crawler(Settings(data_dir=tmp_path))
    public_html = (
        "<html><head><title>Home - Latest Tenders</title></head><body>"
        "<header><form><input type='password' name='pwd'></form></header>"
        "<main><p>Public tender listings for Andhra Pradesh and Telangana.</p></main>"
        "</body></html>"
    )
    assert crawler._looks_login_protected(
        public_html,
        "https://bidalert.in/",
        "Home - Latest Tenders",
        "Public tender listings. " * 40,
    ) is False

    login_html = (
        "<html><head><title>Sign in</title></head><body>"
        "<form><input type='password' name='pwd'></form></body></html>"
    )
    assert crawler._looks_login_protected(
        login_html, "https://example.com/account/login", "Sign in", ""
    ) is True
    # A short page with only a login form is treated as protected even when the
    # title and URL do not contain a login marker.
    assert crawler._looks_login_protected(
        login_html, "https://example.com/", "Welcome", "Sign in"
    ) is True


@pytest.mark.asyncio
async def test_crawler_skips_empty_listing_page(tmp_path):
    from collections import deque
    crawler = Crawler(Settings(data_dir=tmp_path))
    empty_html = """
    <html>
      <body>
        <h1>Achham Tenders</h1>
        <div style="color:red">No tenders found</div>
      </body>
    </html>
    """
    resp = httpx.Response(200, text=empty_html, request=httpx.Request("GET", "https://bidalert.in/achham-tenders"))
    records = await crawler._process_response(
        client=None,  # type: ignore
        response=resp,
        final_url="https://bidalert.in/achham-tenders",
        content_type="text/html",
        depth=0,
        queue=deque(),
        seen=set(),
        base_host="bidalert.in",
    )
    assert records == []
