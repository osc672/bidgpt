from __future__ import annotations

import socket
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.index import PageRecord, SearchIndex
from app.main import create_app

from .fake_site import FakeSite


def _wait_for_status(client: TestClient, site_key: str, predicate, timeout: float = 60.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        response = client.get(f"/sites/{site_key}/status")
        assert response.status_code == 200, response.text
        last = response.json()
        if predicate(last):
            return last
        if last.get("status") == "failed" and not last.get("has_index"):
            pytest.fail(f"crawl failed: {last}")
        time.sleep(0.25)
    pytest.fail(f"timed out waiting for crawl status; last={last}")


def _ready(status: dict) -> bool:
    return status.get("status") == "ready" and status.get("has_index")


def _make_app(tmp_path):
    return create_app(
        Settings(
            data_dir=tmp_path,
            allow_private_urls=True,
            playwright_enabled=True,
            request_delay_seconds=0.0,
            max_pages=20,
            crawl_depth=2,
            recrawl_interval_seconds=3600,
            llm_enabled=False,
            llm_api_key="",
            chat_rate_limit_per_ip=100,
            chat_rate_limit_per_site=100,
        )
    )


def test_phase_two_answers_from_index_with_network_disconnected(tmp_path, monkeypatch):
    site = FakeSite()
    original_site_url = site.url
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            response = client.post("/sites", json={"url": site.url})
            assert response.status_code == 201, response.text
            payload = response.json()
            site_key = payload["site_key"]
            assert payload["status"] == "queued"
            assert payload["widget_snippet"].startswith("<script")

            status = _wait_for_status(client, site_key, _ready)
            assert status["pages_found"] == 3
            assert status["page_count"] == 3
            assert status["last_crawled_at"]

            # The website is now completely offline, and the LLM is disabled.
            # Only the per-site index can answer the question.
            site.stop()

            def _no_network(*args, **kwargs):
                raise OSError("network disabled for phase-2 test")

            monkeypatch.setattr(socket, "create_connection", _no_network)

            allowed_origin = original_site_url
            chat = client.post(
                "/chat",
                json={"site_key": site_key, "message": "Guntur tenders", "history": []},
                headers={"Origin": allowed_origin},
            )
            assert chat.status_code == 200, chat.text
            assert chat.headers.get("access-control-allow-origin") == allowed_origin
            data = chat.json()
            assert data["answer"].startswith("I found 2 tenders")
            assert len(data["tenders"]) == 2
            assert len(data["sources"]) == 2
            assert all("Guntur" in tender["location"] for tender in data["tenders"])
            assert all(tender["url"].startswith(original_site_url) for tender in data["tenders"])
            dates = {tender["bid_by_date"] for tender in data["tenders"]}
            assert dates == {"2026-10-31", "2026-11-05"}
            budgets = {tender["budget"] for tender in data["tenders"]}
            assert any("12,50,000" in value for value in budgets)

            missing_type = client.post(
                "/chat", json={"site_key": site_key, "message": "Guntur", "history": []}
            ).json()
            assert "Which type of tender" in missing_type["answer"]

            missing_location = client.post(
                "/chat",
                json={"site_key": site_key, "message": "electrical tenders", "history": []},
            ).json()
            assert "Which location" in missing_location["answer"]

            greeting = client.post(
                "/chat", json={"site_key": site_key, "message": "Hi", "history": []}
            ).json()
            assert "Which type of tender and which location" in greeting["answer"]

            unknown = client.post(
                "/chat",
                json={"site_key": site_key, "message": "Kurnool tenders", "history": []},
            ).json()
            assert unknown["tenders"] == []
            assert "No tenders matching" in unknown["answer"]

            unindexed_followup = client.post(
                "/chat",
                json={
                    "site_key": site_key,
                    "message": "vijayawada",
                    "history": [
                        {"role": "user", "content": "medical tendors"},
                        {"role": "assistant", "content": "Which location are you looking for?"},
                    ],
                },
            ).json()
            assert unindexed_followup["missing"] == []
            assert "Vijayawada" in unindexed_followup["answer"]

            blocked = client.post(
                "/chat",
                json={"site_key": site_key, "message": "Guntur tenders", "history": []},
                headers={"Origin": "https://evil.example"},
            )
            assert blocked.status_code == 403

            # The bundled demo is served from the API origin, so same-origin
            # widget calls must be accepted without extra configuration.
            same_origin = client.post(
                "/chat",
                json={"site_key": site_key, "message": "Guntur tenders", "history": []},
                headers={"Origin": "http://testserver"},
            )
            assert same_origin.status_code == 200, same_origin.text

            preflight = client.options(
                "/chat",
                headers={
                    "Origin": allowed_origin,
                    "Access-Control-Request-Method": "POST",
                },
            )
            assert preflight.status_code == 204
            assert preflight.headers.get("access-control-allow-origin") == allowed_origin

            evil_preflight = client.options(
                "/chat",
                headers={
                    "Origin": "https://evil.example",
                    "Access-Control-Request-Method": "POST",
                },
            )
            assert evil_preflight.status_code == 403

            claude = client.get("/tools/claude").json()
            openai = client.get("/tools/openai").json()
            assert claude["tools"][0]["name"] == "search_pages"
            assert openai["tools"][0]["function"]["name"] == "search_pages"
    finally:
        site.stop()


def test_recrawl_swaps_in_new_page_only_after_crawl(tmp_path):
    site = FakeSite()
    app = _make_app(tmp_path)
    try:
        with TestClient(app) as client:
            response = client.post("/sites", json={"url": site.url})
            assert response.status_code == 201, response.text
            site_key = response.json()["site_key"]
            first = _wait_for_status(client, site_key, _ready)
            first_version = first["index"]["version"]
            assert first["page_count"] == 3

            site.add_security_page()

            # Before the re-crawl the old index is still live and does not know
            # about the new page.
            before = client.post(
                "/chat",
                json={
                    "site_key": site_key,
                    "message": "Guntur security tenders",
                    "history": [],
                },
            ).json()
            assert before["tenders"] == []

            recrawl = client.post(f"/sites/{site_key}/recrawl")
            assert recrawl.status_code == 202, recrawl.text
            assert recrawl.json()["status"] == "queued"

            second = _wait_for_status(
                client,
                site_key,
                lambda status: _ready(status)
                and status["page_count"] == 4
                and status["index"]["version"] != first_version,
            )
            assert second["pages_found"] == 4

            after = client.post(
                "/chat",
                json={
                    "site_key": site_key,
                    "message": "Guntur security tenders",
                    "history": [],
                },
            ).json()
            assert len(after["tenders"]) == 1
            assert after["tenders"][0]["title"] == "Security Guards for District Offices"
            assert after["tenders"][0]["url"].endswith("/guntur-security.html")
            assert after["tenders"][0]["bid_by_date"] == "2026-12-20"

            all_guntur = client.post(
                "/chat",
                json={"site_key": site_key, "message": "Guntur tenders", "history": []},
            ).json()
            assert len(all_guntur["tenders"]) == 3
            assert any(
                tender["url"].endswith("/guntur-security.html")
                for tender in all_guntur["tenders"]
            )

            # Topic switch: user previously talked about electrical, now asks for paper tenders.
            # It must NOT return electrical tenders or leak old type!
            paper_query = client.post(
                "/chat",
                json={
                    "site_key": site_key,
                    "message": "papers tendor",
                    "history": [
                        {"role": "user", "content": "electrical tenders"},
                        {"role": "assistant", "content": "Which location are you looking for?"},
                    ],
                },
            ).json()
            # FakeSite has no paper tenders, so 0 tenders must be returned.
            assert paper_query["tenders"] == []
            assert "No tenders matching 'papers'" in paper_query["answer"]

            # Location isolation: user asks for AP railway tenders with Maharashtra in history.
            # Must NOT bleed Maharashtra into AP or Guntur!
            ap_query = client.post(
                "/chat",
                json={
                    "site_key": site_key,
                    "message": "ap railway tenders",
                    "history": [
                        {"role": "user", "content": "maharashtra tenders"},
                        {"role": "assistant", "content": "I found 1 tender in Maharashtra"},
                    ],
                },
            ).json()
            assert ap_query["intent"]["location"] == "Andhra Pradesh"
            assert "Maharashtra" not in ap_query["answer"]

            guntur_query = client.post(
                "/chat",
                json={
                    "site_key": site_key,
                    "message": "guntur railway tenders",
                    "history": [
                        {"role": "user", "content": "maharashtra tenders"},
                        {"role": "assistant", "content": "I found 1 tender in Maharashtra"},
                    ],
                },
            ).json()
            assert guntur_query["intent"]["location"] == "Guntur"
            assert "Maharashtra" not in guntur_query["answer"]

            # Multi-turn: user gives location first (vijayawada), then replies with type (railway).
            # Location MUST be inherited and strictly isolated, returning 0 tenders from other states!
            vja_flow_turn1 = client.post(
                "/chat",
                json={"site_key": site_key, "message": "vijayawada", "history": []},
            ).json()
            assert vja_flow_turn1["intent"]["location"] == "Vijayawada"
            assert "Which type of tender" in vja_flow_turn1["answer"]

            vja_flow_turn2 = client.post(
                "/chat",
                json={
                    "site_key": site_key,
                    "message": "railway",
                    "history": [
                        {"role": "user", "content": "vijayawada"},
                        {"role": "assistant", "content": "Which type of tender are you looking for?"},
                    ],
                },
            ).json()
            assert vja_flow_turn2["intent"]["location"] == "Vijayawada"
            assert vja_flow_turn2["intent"]["keyword"] == "railway"
            assert vja_flow_turn2["tenders"] == []
            assert "in Vijayawada" in vja_flow_turn2["answer"]
            assert "Maharashtra" not in vja_flow_turn2["answer"]

            # Reverse multi-turn: user gives keyword first, then location second
            rev_turn2 = client.post(
                "/chat",
                json={
                    "site_key": site_key,
                    "message": "vijayawada",
                    "history": [
                        {"role": "user", "content": "railway"},
                        {"role": "assistant", "content": "Which location are you looking for?"},
                    ],
                },
            ).json()
            assert rev_turn2["intent"]["location"] == "Vijayawada"
            assert rev_turn2["tenders"] == []
            assert "in Vijayawada" in rev_turn2["answer"]
    finally:
        site.stop()


def _make_test_rec(url, title, text, tender_type, location, state, dept):
    return PageRecord(
        url=url,
        title=title,
        text=text,
        tender_type=tender_type,
        location=location,
        state=state,
        department=dept,
        budget="Refer Documents",
        timeline="30 days",
        products=title,
        bid_by_date="2026-11-20",
        crawled_at="2026-11-02T00:00:00Z",
        content_hash="abc123",
    )


def test_electrical_excludes_bank_and_petroleum(tmp_path):
    pages = [
        _make_test_rec(
            url="https://bidassist.com/all-tenders/state-bank-of-india/active",
            title="Tenders of State Bank Of India",
            text="State Bank Of India - SBI Tender - Maha Tender\nElectrical and Electronics\nLatur, Maharashtra\nClosing Date\n15 Nov 2026\nDownload",
            tender_type="Electrical Works",
            location="Latur, Maharashtra",
            state="Maharashtra",
            dept="State Bank Of India",
        ),
        _make_test_rec(
            url="https://bidassist.com/all-tenders/hindustan-petroleum-corporation-limited/active",
            title="HPCL E-Tender",
            text="Hindustan Petroleum Corporation Limited - HPCL Tender - Maha Tender\nElectrical wiring\nMumbai, Maharashtra\nClosing Date\n20 Nov 2026\nDownload",
            tender_type="Electrical Works",
            location="Mumbai, Maharashtra",
            state="Maharashtra",
            dept="Hindustan Petroleum Corporation Limited",
        ),
        _make_test_rec(
            url="https://bidassist.com/all-tenders/maharashtra-state-electricity-distribution-company-limited/active",
            title="MSEDCL Tenders",
            text="Maharashtra State Electricity Distribution Company Limited - Maha Tender\nElectrical and Electronics\nKolhapur, Maharashtra\nClosing Date\n25 Nov 2026\nDownload",
            tender_type="Electrical Works",
            location="Kolhapur, Maharashtra",
            state="Maharashtra",
            dept="Maharashtra State Electricity Distribution Company Limited",
        ),
        _make_test_rec(
            url="https://bidassist.com/all-tenders/central-railway/active",
            title="Central Railway Tenders",
            text="Central Railway - Maha Tender\nElectrical Trd Portion Of Work In Connection With Development Of Stabling Yard\nSolapur, Maharashtra\nClosing Date\n22 Nov 2026\nDownload\nCentral Railway - Maha Tender\nComprehensive Repair of CCB Brake System In 3-phase Electric Locos\nMumbai, Maharashtra\nClosing Date\n23 Nov 2026\nDownload",
            tender_type="Railway",
            location="Solapur, Maharashtra",
            state="Maharashtra",
            dept="Central Railway",
        ),
    ]

    index = SearchIndex(tmp_path / "index.sqlite", pages)
    hits = index.search(location="Maharashtra", tender_type="Electrical Works", limit=10)
    titles = [h.page.title for h in hits]

    # Verify MSEDCL and Central Railway TRD are found
    assert any("MSEDCL" in t or "Maharashtra State Electricity" in t for t in titles)
    assert any("Electrical Trd" in t for t in titles)

    # Verify Bank, HPCL, and Brake Repair are EXCLUDED
    for t in titles:
        t_low = t.lower()
        assert "state bank of india" not in t_low, f"Bank leaked into electrical: {t}"
        assert "hindustan petroleum" not in t_low, f"Petrol leaked into electrical: {t}"
        assert "brake system" not in t_low, f"Brake system leaked into electrical: {t}"


def test_karnataka_railway_unique_urls(tmp_path: Path):
    text_data = (
        "Rail Wheel Factory - RWF Tender - Karnataka Tender\n"
        "Bengaluru Urban, Karnataka\n"
        "M.s. Angle 50 X 50 X 6mm\n"
        "Closing Date\n5 Nov 2026\nDownload\n"
        "South Western Railway - SWR Tender - Karnataka Tender\n"
        "Mandya, Karnataka\n"
        "Snap Gauge (go & No Go) For Axle Box Pivot Bush To Icf Drawing No. J&t/g- 1038/01.\n"
        "Closing Date\n16 Nov 2026\nDownload\n"
        "South Western Railway - SWR Tender - Karnataka Tender\n"
        "Mandya, Karnataka\n"
        "Snap Gauge (go & No Go) For Bush (dia 70) To Icf Drawing No. J&t/g- 1035/01.\n"
        "Closing Date\n16 Nov 2026\nDownload\n"
        "South Western Railway - SWR Tender - Karnataka Tender\n"
        "Mandya, Karnataka\n"
        "Set Of Air Spring Height Measuring Gauge As Per Rdso Sk No. Asg 1/2019 For Go & No Go Gauge\n"
        "Closing Date\n12 Nov 2026\nDownload\n"
        "Rail Wheel Factory - RWF Tender - Karnataka Tender\n"
        "Bengaluru Urban, Karnataka\n"
        "Liquid Paraffin 100 Ml Bottle\n"
        "Closing Date\n11 Nov 2026\nDownload\n"
        "South Western Railway - SWR Tender - Karnataka Tender\n"
        "Mandya, Karnataka\n"
        "Plug Gauge ( Go & No Go ) For Bush To Icf Drawing No. J&t/g-1036/01.\n"
        "Closing Date\n12 Nov 2026\nDownload"
    )
    rec = _make_test_rec(
        url="https://bidassist.com/karnataka-tenders/active",
        title="Karnataka Tenders: Eprocurement Karnataka 2026",
        text=text_data,
        tender_type="Tenders",
        location="Karnataka",
        state="Karnataka",
        dept="Government of Karnataka",
    )
    index = SearchIndex(tmp_path / "index.sqlite", [rec])
    hits = index.search(location="Karnataka", tender_type="Railway", limit=10)
    assert len(hits) == 6

    urls = [h.page.url for h in hits]
    # Verify all 6 URLs are unique
    assert len(set(urls)) == 6, f"Duplicate URLs found: {urls}"

    # Verify no URLs point to bare authority directory pages that ignore keyword search
    for u in urls:
        assert "rail-wheel-factory/active" not in u
        assert "south-western-railway/active" not in u
        assert "detail-" in u

    # Verify distinct URLs for each tender
    rwf_urls = [u for u in urls if "rail-wheel-factory" in u]
    swr_urls = [u for u in urls if "south-western-railway" in u]
    assert len(rwf_urls) == 2
    assert len(swr_urls) == 4
    assert rwf_urls[0] != rwf_urls[1]
    assert len(set(swr_urls)) == 4

    # Verify distinct tender_id / drawing references were extracted
    tender_ids = [h.page.tender_id for h in hits]
    assert len(set(tender_ids)) == 6
    assert any("1038/01" in str(tid) for tid in tender_ids)
    assert any("1035/01" in str(tid) for tid in tender_ids)



