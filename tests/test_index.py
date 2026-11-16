from __future__ import annotations

from app.index import IndexManager, PageRecord, build_index


def _records() -> list[PageRecord]:
    return [
        PageRecord(
            url="https://demo.test/guntur-electrical",
            title="Supply and Installation of LED Street Lights",
            text="Guntur electrical tender for LED street lights.",
            tender_type="Electrical Works",
            location="Guntur, Andhra Pradesh",
            budget="Rs. 12,50,000",
            timeline="90 days",
            products="LED street light fittings and poles",
            department="Guntur Municipal Corporation",
            bid_by_date="2026-10-31",
            crawled_at="2026-10-30T00:00:00+00:00",
            content_hash="a",
        ),
        PageRecord(
            url="https://demo.test/hyderabad-civil",
            title="Hyderabad Road Widening Tender",
            text="Hyderabad civil works for road widening.",
            tender_type="Civil Works",
            location="Hyderabad, Telangana",
            budget="Rs. 2.4 crore",
            timeline="18 months",
            products="Road widening and drainage",
            department="Hyderabad Metropolitan Development Authority",
            bid_by_date="2026-11-15",
            crawled_at="2026-10-30T00:00:00+00:00",
            content_hash="b",
        ),
        PageRecord(
            url="https://demo.test/guntur-housekeeping",
            title="Guntur Housekeeping Services Tender",
            text="Guntur housekeeping and sanitation services.",
            tender_type="Housekeeping Services",
            location="Guntur, Andhra Pradesh",
            budget="INR 8,40,000",
            timeline="12 months",
            products="Housekeeping and sanitation services",
            department="Andhra Pradesh Health Department",
            bid_by_date="2026-11-05",
            crawled_at="2026-10-30T00:00:00+00:00",
            content_hash="c",
        ),
    ]


def test_location_search_is_strict_and_spelling_tolerant(tmp_path):
    manager = IndexManager(tmp_path)
    build_index(tmp_path / "sites" / "sk_test", _records())
    index = manager.get("sk_test")
    assert index is not None

    guntur = index.search(location="Guntur")
    assert len(guntur) == 2
    assert {hit.page.location for hit in guntur} == {"Guntur, Andhra Pradesh"}

    variant = index.search(location="Guntoor")
    assert len(variant) == 2
    assert all("Guntur" in (hit.page.location or "") for hit in variant)

    unknown = index.search(location="Kurnool")
    assert unknown == []

    civil = index.search(location="Guntur", tender_type="Civil Works")
    assert civil == []

    led = index.search(location="Guntur", keyword="LED")
    assert len(led) == 1
    assert "LED" in (led[0].page.title or "")


def test_index_swap_keeps_old_until_new_is_ready(tmp_path):
    manager = IndexManager(tmp_path)
    site_dir = tmp_path / "sites" / "sk_swap"
    first = build_index(site_dir, _records()[:2])
    old = manager.get("sk_swap")
    assert old is not None and old.page_count == 2
    assert len(old.search(location="Guntur")) == 1

    second = build_index(site_dir, _records())
    assert second["version"] != first["version"]
    # Cached index still serves the old generation until invalidated.
    assert old.page_count == 2
    manager.invalidate("sk_swap")
    new = manager.get("sk_swap")
    assert new is not None and new.page_count == 3
    assert len(new.search(location="Guntur")) == 2


def test_index_bidalert_fields_and_search(tmp_path):
    record = PageRecord(
        url="https://bidalert.in/tenders/BAMAH23382225",
        title="SITC of Spare 10 MVA 100 3.6 kV Transformer with complete bay",
        text="SITC of Spare 10 MVA Transformer at Substation Pise. Closing Date: 26-Nov-2026 04:00 PM.",
        tender_type="Electrical Works",
        location="Maharashtra",
        budget="₹ 1,489,520",
        timeline="120 days",
        products="10 MVA Transformer",
        department="Maharashtra State Electricity Board",
        bid_by_date="2026-11-26 04:00 PM",
        crawled_at="2026-10-30T00:00:00+00:00",
        content_hash="d",
        tender_id="BAMAH23382225",
        emd="₹ 1,489,520",
        emd_exemption="No",
        ecv="Refer",
        state="Maharashtra",
    )
    site_dir = tmp_path / "sites" / "sk_bidalert"
    build_index(site_dir, [record])
    manager = IndexManager(tmp_path)
    index = manager.get("sk_bidalert")
    assert index is not None

    # Search by state as location
    hits = index.search(location="Maharashtra")
    assert len(hits) == 1
    hit = hits[0]
    public = hit.public()
    assert public["tender_id"] == "BAMAH23382225"
    assert public["emd"] == "₹ 1,489,520"
    assert public["emd_exemption"] == "No"
    assert public["ecv"] == "Refer"
    assert public["state"] == "Maharashtra"
    assert public["bid_by_date"] == "2026-11-26 04:00 PM"

    # Search by tender ID keyword
    by_id = index.search(keyword="BAMAH23382225")
    assert len(by_id) == 1
    assert by_id[0].page.tender_id == "BAMAH23382225"


def test_index_filters_empty_listing_pages(tmp_path):
    empty_page = PageRecord(
        url="https://bidalert.in/global-tender-city/achham-tenders",
        title="Find Government & Private Tenders Across India.",
        text="Achham Tenders. No tenders found on this page.",
        tender_type="Transport Services",
        location="Achham",
        budget=None,
        timeline=None,
        products=None,
        department=None,
        bid_by_date=None,
        crawled_at="2026-10-30T00:00:00+00:00",
        content_hash="empty_hash",
        tender_id=None,
        emd=None,
        emd_exemption=None,
        ecv=None,
        state=None,
    )
    valid_tender = PageRecord(
        url="https://bidalert.in/tenders/gem-2026-b-8193653/",
        title="Pipeline Maintenance Services",
        text="Services to conduct internal inspection. Closing: 07-Dec-2026.",
        tender_type="Services",
        location="Maharashtra",
        budget="50,000",
        timeline=None,
        products="Inspection",
        department="IOCL",
        bid_by_date="2026-12-07",
        crawled_at="2026-10-30T00:00:00+00:00",
        content_hash="valid_hash",
        tender_id="GEM/2026/B/8193653",
        emd=None,
        emd_exemption=None,
        ecv="50,000",
        state="Maharashtra",
    )
    site_dir = tmp_path / "sites" / "sk_filter_test"
    build_index(site_dir, [empty_page, valid_tender])
    manager = IndexManager(tmp_path)
    index = manager.get("sk_filter_test")
    assert index is not None

    # Searching for Achham should NOT return the empty listing page
    hits = index.search(location="Achham")
    assert hits == []

    # Searching for Maharashtra should return the valid tender
    mah_hits = index.search(location="Maharashtra")
    assert len(mah_hits) == 1
    assert mah_hits[0].page.tender_id == "GEM/2026/B/8193653"


def test_public_preserves_page_url(tmp_path):
    page = PageRecord(
        url="https://bidalert.in/tenders/gem-2026-b-8193653/",
        title="Pipeline Maintenance",
        text="Tender details",
        tender_type="Services",
        location="Maharashtra",
        budget=None,
        timeline=None,
        products=None,
        department=None,
        bid_by_date="2026-12-07",
        crawled_at="2026-10-30T00:00:00+00:00",
        content_hash="hash_1",
        tender_id="GEM/2026/B/8193653",
        emd=None,
        emd_exemption=None,
        ecv=None,
        state="Maharashtra",
    )
    site_dir = tmp_path / "sites" / "sk_url_test"
    build_index(site_dir, [page])
    manager = IndexManager(tmp_path)
    index = manager.get("sk_url_test")
    hits = index.search(location="Maharashtra")
    assert len(hits) == 1
    public_data = hits[0].public()
    assert public_data["url"] == "https://bidalert.in/tenders/gem-2026-b-8193653/"
    assert public_data["source"] == "https://bidalert.in/tenders/gem-2026-b-8193653/"


def test_keyword_relevance_rejects_unrelated_tenders_and_matches_only_requested_subject(tmp_path):
    railway = PageRecord(
        url="https://demo.test/railways",
        title="Railways Tenders",
        text="Central Railway procurement as per specifications. Newspaper advertisement published.",
        tender_type="Electrical Works",
        location="Mumbai, Maharashtra",
        budget="50,00,000",
        timeline="180 days",
        products="governor, Woodward For M/s. Cummins Diesel Engine",
        department="Central Railway",
        bid_by_date="2026-11-23",
        crawled_at="2026-10-30T00:00:00+00:00",
        content_hash="h1",
        tender_id="REF-RAIL",
        state="Maharashtra",
    )
    substation = PageRecord(
        url="https://demo.test/substation",
        title="Substation Tenders",
        text="Himachal Pradesh 33/11kv substation maintenance. Newspaper Ad available.",
        tender_type="Electrical Works",
        location="Himachal Pradesh",
        budget="20,00,000",
        timeline="90 days",
        products="Various Civil Maintenance Work At 33/11kv Substation",
        department="HPSEBL",
        bid_by_date="2026-11-11",
        crawled_at="2026-10-30T00:00:00+00:00",
        content_hash="h2",
        tender_id="REF-SUB",
        state="Himachal Pradesh",
    )
    paper_tender = PageRecord(
        url="https://demo.test/paper-supply",
        title="Supply of Copier Paper and Stationery",
        text="Procurement of A4 Copier Paper and office stationery reams.",
        tender_type="Supply",
        location="Delhi",
        budget="5,00,000",
        timeline="30 days",
        products="A4 Copier Paper 75 GSM, Bond Paper, Spiral Paper Binding",
        department="Education Department",
        bid_by_date="2026-12-01",
        crawled_at="2026-10-30T00:00:00+00:00",
        content_hash="h3",
        tender_id="REF-PAPER",
        state="Delhi",
    )

    site_dir = tmp_path / "sites" / "sk_relevance"
    build_index(site_dir, [railway, substation, paper_tender])
    manager = IndexManager(tmp_path)
    index = manager.get("sk_relevance")
    assert index is not None

    # 1. Searching for "papers" or "paper" MUST return ONLY the paper tender
    paper_hits = index.search(keyword="papers")
    assert len(paper_hits) == 1
    assert paper_hits[0].page.tender_id == "REF-PAPER"
    assert "Paper" in (paper_hits[0].page.title or "")

    paper_singular = index.search(keyword="paper")
    assert len(paper_singular) == 1
    assert paper_singular[0].page.tender_id == "REF-PAPER"

    # Neither railway nor substation should EVER match "papers"
    matched_ids = {h.page.tender_id for h in paper_hits}
    assert "REF-RAIL" not in matched_ids
    assert "REF-SUB" not in matched_ids

    # 2. Searching for "substation" must return ONLY the substation tender
    sub_hits = index.search(keyword="substation")
    assert len(sub_hits) == 1
    assert sub_hits[0].page.tender_id == "REF-SUB"

    # 3. Searching for "railway" must return ONLY the railway tender
    rail_hits = index.search(keyword="railway")
    assert len(rail_hits) == 1
    assert rail_hits[0].page.tender_id == "REF-RAIL"

    # 4. Searching for an unindexed commodity (e.g. "cement") must return 0 hits,
    # NOT random false positives
    cement_hits = index.search(keyword="cement")
    assert cement_hits == []


def test_maharashtra_search_excludes_hyderabad_and_other_states(tmp_path):
    """Regression: 'maharastra tenders' must NOT return Mumbai+Hyderabad mixed results.
    Only tenders explicitly in Maharashtra should appear."""
    mumbai_tender = PageRecord(
        url="https://bidassist.com/tenders/mumbai-tenders",
        title="Mumbai Tenders",
        text="Tenders of Central Railway.",
        tender_type="Transport",
        location="Mumbai",
        budget="Refer Doc",
        timeline=None,
        products=None,
        department="Central Railway",
        bid_by_date="2026-11-06",
        crawled_at="2026-10-30T00:00:00+00:00",
        content_hash="mum1",
        state="Maharashtra",
    )
    hyderabad_tender = PageRecord(
        url="https://bidassist.com/tenders/hyderabad-tenders",
        title="Hyderabad Tenders",
        text="All Tenders.",
        tender_type="All",
        location="Hyderabad",
        budget="INR 788.1",
        timeline=None,
        products=None,
        department="Various",
        bid_by_date="2026-11-02",
        crawled_at="2026-10-30T00:00:00+00:00",
        content_hash="hyd1",
        state="Telangana",
    )
    pune_tender = PageRecord(
        url="https://bidassist.com/tenders/pune-civil-tenders",
        title="Pune Civil Works Tenders",
        text="Pune civil construction projects Maharashtra.",
        tender_type="Civil Works",
        location="Pune, Maharashtra",
        budget="Rs 10,00,000",
        timeline="6 months",
        products="Construction",
        department="PMC",
        bid_by_date="2026-12-01",
        crawled_at="2026-10-30T00:00:00+00:00",
        content_hash="pun1",
        state="Maharashtra",
    )

    site_dir = tmp_path / "sites" / "sk_maha_test"
    build_index(site_dir, [mumbai_tender, hyderabad_tender, pune_tender])
    manager = IndexManager(tmp_path)
    index = manager.get("sk_maha_test")
    assert index is not None

    # Searching "maharastra" (typo) → should only return Maharashtra tenders
    hits = index.search(location="maharastra")
    hit_states = {h.page.state for h in hits}
    hit_locations = {h.page.location for h in hits}

    # Hyderabad (Telangana) must NEVER appear
    assert "Telangana" not in hit_states, f"Hyderabad/Telangana leaked into Maharashtra results: {hit_states}"
    assert "Hyderabad" not in hit_locations, f"Hyderabad location leaked: {hit_locations}"

    # Maharashtra tenders must appear
    assert len(hits) >= 1
    assert all(h.page.state == "Maharashtra" for h in hits), (
        f"Non-Maharashtra results returned: {[(h.page.location, h.page.state) for h in hits]}"
    )


def test_city_specific_search_does_not_return_other_cities(tmp_path):
    """Regression: 'guntur railway tenders' must NOT return results from other cities.
    The user explicitly asked for Guntur, not Vijayawada or Hyderabad."""
    guntur_railway = PageRecord(
        url="https://demo.test/guntur-railway",
        title="Guntur Railway Tenders",
        text="Railway procurement for Guntur division.",
        tender_type="Transport",
        location="Guntur, Andhra Pradesh",
        budget="Rs 50,00,000",
        timeline="12 months",
        products="Railway materials",
        department="South Central Railway - Guntur",
        bid_by_date="2026-12-15",
        crawled_at="2026-10-30T00:00:00+00:00",
        content_hash="grail",
        state="Andhra Pradesh",
    )
    vijayawada_railway = PageRecord(
        url="https://demo.test/vijayawada-railway",
        title="Vijayawada Railway Tenders",
        text="Railway procurement for Vijayawada division.",
        tender_type="Transport",
        location="Vijayawada, Andhra Pradesh",
        budget="Rs 75,00,000",
        timeline="12 months",
        products="Railway materials",
        department="South Central Railway - Vijayawada",
        bid_by_date="2026-12-20",
        crawled_at="2026-10-30T00:00:00+00:00",
        content_hash="vrail",
        state="Andhra Pradesh",
    )
    hyderabad_railway = PageRecord(
        url="https://demo.test/hyderabad-railway",
        title="Hyderabad Railway Tenders",
        text="Railway procurement for Hyderabad division.",
        tender_type="Transport",
        location="Hyderabad, Telangana",
        budget="Rs 1,00,00,000",
        timeline="18 months",
        products="Railway materials",
        department="South Central Railway - Hyderabad",
        bid_by_date="2026-12-25",
        crawled_at="2026-10-30T00:00:00+00:00",
        content_hash="hrail",
        state="Telangana",
    )

    site_dir = tmp_path / "sites" / "sk_city_test"
    build_index(site_dir, [guntur_railway, vijayawada_railway, hyderabad_railway])
    manager = IndexManager(tmp_path)
    index = manager.get("sk_city_test")
    assert index is not None

    # Search for Guntur railway specifically — must ONLY return Guntur
    hits = index.search(location="Guntur", tender_type="Transport")
    assert len(hits) >= 1, "Should find at least the Guntur railway tender"
    for hit in hits:
        loc = (hit.page.location or "").lower()
        assert "guntur" in loc, (
            f"Non-Guntur result leaked in: {hit.page.location} ({hit.page.url})"
        )
        assert "vijayawada" not in loc, f"Vijayawada leaked into Guntur results"
        assert "hyderabad" not in loc, f"Hyderabad leaked into Guntur results"


def test_multistate_listing_pages_excluded_from_specific_city_and_state_searches(tmp_path):
    """Multi-state directory/listing pages must not leak into state or city searches."""
    pune_tender = PageRecord(
        url="https://bidassist.com/maharashtra-tenders/pune-city/active",
        title="Pune (Maharashtra) Tenders",
        text="Pune municipal road repair works in Maharashtra.",
        tender_type="Civil Works",
        location="Pune",
        budget="Rs 10,00,000",
        timeline="6 months",
        products="Road works",
        department="Pune Municipal Corporation",
        bid_by_date="2026-12-10",
        crawled_at="2026-11-01T00:00:00+00:00",
        content_hash="pune1",
        state="Maharashtra",
    )
    mixed_listing = PageRecord(
        url="https://bidassist.com/all-tenders/active",
        title="All Tenders",
        text="Tenders across India: Chennai, Tamil Nadu; Varanasi, Uttar Pradesh; Dharwad, Karnataka; Pune, Maharashtra; Hyderabad, Telangana.",
        tender_type=None,
        location=None,
        budget=None,
        timeline=None,
        products=None,
        department=None,
        bid_by_date=None,
        crawled_at="2026-11-01T00:00:00+00:00",
        content_hash="mixed1",
        state=None,
    )

    site_dir = tmp_path / "sites" / "sk_multistate_test"
    build_index(site_dir, [pune_tender, mixed_listing])
    manager = IndexManager(tmp_path)
    index = manager.get("sk_multistate_test")
    assert index is not None

    # State search for Maharashtra
    state_hits = index.search(location="Maharashtra")
    assert len(state_hits) == 1
    assert state_hits[0].page.url == pune_tender.url

    # City search for Pune
    city_hits = index.search(location="Pune")
    assert len(city_hits) == 1
    assert city_hits[0].page.url == pune_tender.url


def test_regional_listing_page_matches_type_and_keyword_in_text(tmp_path):
    """Regression: 'madhya pradesh railway tenders' must match Madhya Pradesh listing pages containing railway tenders."""
    mp_listing = PageRecord(
        url="https://bidassist.com/madhya-pradesh-tenders/active",
        title="Madhya Pradesh Tenders",
        text="West Central Railway - WCR Tender. Hard Sal Wooden Packing. Closing Date 13 Nov 2026. Western Railway - WR Tender.",
        tender_type=None,
        location="Madhya Pradesh",
        budget=None,
        timeline=None,
        products=None,
        department=None,
        bid_by_date=None,
        crawled_at="2026-11-01T00:00:00+00:00",
        content_hash="mp1",
        state="Madhya Pradesh",
    )
    assam_listing = PageRecord(
        url="https://bidassist.com/assam-tenders/active",
        title="Assam Tenders",
        text="Assam civil construction and educational supply tenders.",
        tender_type=None,
        location="Assam",
        budget=None,
        timeline=None,
        products=None,
        department=None,
        bid_by_date=None,
        crawled_at="2026-11-01T00:00:00+00:00",
        content_hash="as1",
        state="Assam",
    )

    site_dir = tmp_path / "sites" / "sk_mp_rail"
    build_index(site_dir, [mp_listing, assam_listing])
    manager = IndexManager(tmp_path)
    index = manager.get("sk_mp_rail")
    assert index is not None

    # Searching for railway in Madhya Pradesh by keyword
    hits_kw = index.search(location="Madhya Pradesh", keyword="railway")
    assert len(hits_kw) == 1
    assert hits_kw[0].page.url == mp_listing.url

    # Searching by type Transport (synonym of railway)
    hits_type = index.search(location="Madhya Pradesh", tender_type="Transport")
    assert len(hits_type) == 1
    assert hits_type[0].page.url == mp_listing.url

    # Searching for railway in Assam must return 0
    hits_assam = index.search(location="Assam", keyword="railway")
    assert hits_assam == []


