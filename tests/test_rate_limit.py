from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from app.index import PageRecord, build_index
from app.main import create_app


def _page() -> PageRecord:
    return PageRecord(
        url="https://example.com/guntur-tender",
        title="Guntur Electrical Tender",
        text="Guntur electrical works.",
        tender_type="Electrical Works",
        location="Guntur, Andhra Pradesh",
        budget="Rs. 5,00,000",
        timeline="60 days",
        products="Electrical fittings",
        department="Example Department",
        bid_by_date="2026-11-30",
        crawled_at="2026-10-30T00:00:00+00:00",
        content_hash="x",
    )


def test_chat_rate_limit_per_ip(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        llm_enabled=False,
        chat_rate_limit_per_ip=2,
        chat_rate_limit_per_site=100,
        recrawl_interval_seconds=3600,
    )
    app = create_app(settings)
    registry = app.state.registry
    site = registry.create("https://example.com", "example.com")
    active = build_index(settings.site_dir(site.site_key), [_page()])
    registry.mark_ready(
        site.site_key,
        pages_found=1,
        active_index=active["file"],
        errors=[],
        interval_seconds=3600,
    )

    with TestClient(app) as client:
        headers = {"Origin": "https://example.com"}
        for _ in range(2):
            response = client.post(
                "/chat",
                json={"site_key": site.site_key, "message": "Guntur tenders"},
                headers=headers,
            )
            assert response.status_code == 200, response.text
        limited = client.post(
            "/chat",
            json={"site_key": site.site_key, "message": "Guntur tenders"},
            headers=headers,
        )
        assert limited.status_code == 429
        assert "Retry-After" in limited.headers
