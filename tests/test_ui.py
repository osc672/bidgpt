from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_setup_ui_widget_and_info(tmp_path):
    app = create_app(
        Settings(
            data_dir=tmp_path,
            llm_enabled=False,
            chat_rate_limit_per_ip=100,
            chat_rate_limit_per_site=100,
        )
    )
    with TestClient(app) as client:
        # Visiting root / (like browser opening run.py URL) returns the HTML UI
        home = client.get("/")
        assert home.status_code == 200
        assert "text/html" in home.headers["content-type"]
        assert "BidGPT" in home.text
        assert "widget.js" in home.text

        # Explicit JSON request returns service info
        api_home = client.get("/", headers={"accept": "application/json"})
        assert api_home.status_code == 200
        assert api_home.json()["service"] == "BidGPT"
        assert api_home.json()["widget"] == "/widget.js"

        # /api/sites returns registered sites
        sites_res = client.get("/api/sites")
        assert sites_res.status_code == 200
        assert isinstance(sites_res.json(), list)

        # /search returns dedicated search page with Find Tenders button
        search_res = client.get("/search")
        assert search_res.status_code == 200
        assert "text/html" in search_res.headers["content-type"]
        assert "Find Tenders" in search_res.text
        assert "Click Find Tenders to search for tenders" in search_res.text

        demo = client.get("/demo")
        assert demo.status_code == 404

        widget = client.get("/widget.js")
        assert widget.status_code == 200
        assert "application/javascript" in widget.headers["content-type"]
        # The widget must survive being embedded in <head> before <body> exists.
        assert "DOMContentLoaded" in widget.text
        assert "bidgpt-card-header" in widget.text
        assert "bidgpt-metrics-row" in widget.text
        assert "bidgpt-share-icons" in widget.text
        assert "bidgpt-view-btn" in widget.text
        assert "Tender Info:" in widget.text
        assert "Closing Date:" in widget.text
        assert "EMD:" in widget.text
        assert "Exemption:" in widget.text
        assert "ECV:" in widget.text

        info = client.get("/info").json()
        assert info["service"] == "BidGPT"
        assert "POST /sites" in info["endpoints"]


def test_chat_hospital_and_reference_search(tmp_path):
    from app.index import PageRecord, build_index
    from app.registry import Registry

    settings = Settings(
        data_dir=tmp_path,
        llm_enabled=False,
        chat_rate_limit_per_ip=100,
        chat_rate_limit_per_site=100,
    )
    registry = Registry(settings.registry_path)
    record = registry.create("https://bidalert.in", "bidalert.in")
    site_key = record.site_key

    page = PageRecord(
        url="https://bidalert.in/tender/bamah23382222",
        title="Comprehensive Up gradation, Replacement and Strengthening of Sub-Main Electrical Distribution System at UGPG Hostel in KEM Hospital",
        text="Tender for electrical distribution system at UGPG Hostel KEM Hospital Mumbai.",
        tender_type="Electrical Works",
        location="Mumbai, Maharashtra",
        state="Maharashtra",
        tender_id="BAMAH23382222",
        emd="₹ 423,300",
        emd_exemption="No",
        ecv="₹ 35,870,303",
        bid_by_date="18-Nov-2026 04:00 PM",
        budget="₹ 35,870,303",
        timeline="12 months",
        products="Sub-Main Electrical Distribution System",
        department="Municipal Corporation of Greater Mumbai",
        crawled_at="2026-10-30T00:00:00+00:00",
        content_hash="h1",
    )
    meta = build_index(settings.site_dir(site_key), [page])
    registry.mark_ready(
        site_key,
        pages_found=1,
        active_index=meta["version"],
        errors=[],
        interval_seconds=3600,
    )

    app = create_app(settings)
    with TestClient(app) as client:
        headers = {"Origin": "https://bidalert.in"}
        # 1. Searching by "hospital" keyword returns the KEM Hospital tender without requiring location
        resp = client.post(
            "/chat",
            json={"site_key": site_key, "message": "hospital", "history": []},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        res_hospital = resp.json()
        assert len(res_hospital["tenders"]) == 1
        assert "hospital" in res_hospital["answer"].lower()
        card = res_hospital["tenders"][0]
        assert card["tender_id"] == "BAMAH23382222"
        assert card["state"] == "Maharashtra"
        assert card["emd"] == "₹ 423,300"
        assert card["emd_exemption"] == "No"
        assert card["ecv"] == "₹ 35,870,303"
        assert card["bid_by_date"] == "18-Nov-2026 04:00 PM"
        assert "KEM Hospital" in card["title"]

        # 2. Searching by tender reference ID BAMAH23382222 directly
        res_id = client.post(
            "/chat",
            json={"site_key": site_key, "message": "BAMAH23382222", "history": []},
            headers=headers,
        ).json()
        assert len(res_id["tenders"]) == 1
        assert res_id["tenders"][0]["tender_id"] == "BAMAH23382222"

        # 3. Searching by "hospital electrical"
        res_combo = client.post(
            "/chat",
            json={"site_key": site_key, "message": "hospital electrical", "history": []},
            headers=headers,
        ).json()
        assert len(res_combo["tenders"]) == 1
        assert res_combo["tenders"][0]["tender_id"] == "BAMAH23382222"

