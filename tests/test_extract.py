from __future__ import annotations

from app.extract import extract_fields_from_text, parse_html, parse_pdf_text

from .fake_site import GUNTUR_HTML, GUNTUR_PDF_LINES


def test_field_extraction_from_html():
    parsed = parse_html(GUNTUR_HTML, "https://example.test/tenders/guntur")
    fields = parsed.fields

    assert fields.title == "Supply and Installation of LED Street Lights"
    assert fields.tender_type == "Electrical Works"
    assert fields.location == "Guntur, Andhra Pradesh"
    assert fields.budget == "Rs. 12,50,000"
    assert fields.timeline == "90 days"
    assert fields.products == "LED street light fittings, poles and installation"
    assert fields.department == "Guntur Municipal Corporation"
    assert fields.bid_by_date == "2026-10-31"


def test_field_extraction_from_plain_pdf_text():
    fields = parse_pdf_text("\n".join(GUNTUR_PDF_LINES), "https://example.test/a.pdf")
    assert fields.title == "Guntur Housekeeping Services Tender"
    assert fields.location == "Guntur, Andhra Pradesh"
    assert fields.budget == "INR 8,40,000"
    assert fields.timeline == "12 months"
    assert fields.bid_by_date == "2026-11-05"


def test_missing_fields_stay_missing():
    fields = extract_fields_from_text("Notice inviting tenders for housekeeping")
    assert fields.budget is None
    assert fields.location is None
    assert fields.bid_by_date is None


def test_bidalert_tender_card_extraction():
    html = """
    <div class="tender-card">
      <div class="header">Maharashtra Tenders BAMAH23382225</div>
      <div class="info">Tender Info: SITC of Spare 10 MVA 100 3.6 kV Transformer with complete bay and allied work at 100 kV Substation Pise</div>
      <div class="closing">Closing Date: 26-Nov-2026 04:00 PM</div>
      <div class="meta">
        <span>EMD: ₹ 1,489,520</span>
        <span>Exemption: No</span>
        <span>ECV: Refer</span>
      </div>
      <a href="/tenders/BAMAH23382225">View Tender</a>
    </div>
    """
    parsed = parse_html(html, "https://bidalert.in/tenders/BAMAH23382225")
    fields = parsed.fields

    assert fields.state == "Maharashtra"
    assert fields.location == "Maharashtra"
    assert fields.tender_id == "BAMAH23382225"
    assert "SITC of Spare 10 MVA" in (fields.title or "")
    assert fields.tender_type == "Electrical Works"
    assert fields.bid_by_date == "2026-11-26 04:00 PM"
    assert fields.emd == "₹ 1,489,520"
    assert fields.emd_exemption == "No"
    assert fields.ecv == "Refer"
    assert "https://bidalert.in/tenders/BAMAH23382225" in parsed.links


def test_bidalert_tender_with_numeric_ecv():
    html = """
    <div class="tender-card">
      <div class="header">Maharashtra Tenders BAMAH23382222</div>
      <div class="info">Tender Info: Comprehensive Up gradation, Replacement and Strengthening of Sub-Main Electrical Distribution System at UGPG Hostel in KEM Hospital</div>
      <div class="closing">Closing Date: 18-Nov-2026 04:00 PM</div>
      <div class="meta">
        <span>EMD: ₹ 423,300</span>
        <span>Exemption: No</span>
        <span>ECV: ₹ 35,870,303</span>
      </div>
      <a href="/tenders/BAMAH23382222">View Tender</a>
    </div>
    """
    parsed = parse_html(html, "https://bidalert.in/tenders/BAMAH23382222")
    fields = parsed.fields

    assert fields.state == "Maharashtra"
    assert fields.tender_id == "BAMAH23382222"
    assert fields.bid_by_date == "2026-11-18 04:00 PM"
    assert fields.emd == "₹ 423,300"
    assert fields.emd_exemption == "No"
    assert fields.ecv == "₹ 35,870,303"
    assert fields.budget == "₹ 35,870,303"


def test_empty_listing_page_detected():
    html = """
    <html>
      <body>
        <h1>Achham Tenders</h1>
        <div class="breadcrumb">Home > Global Tenders > Achham Tenders</div>
        <div class="filter-dropdowns">
          <select><option>Transport Services</option></select>
          <select><option>&lt;= 1,000</option></select>
        </div>
        <div class="no-tenders" style="color:red">No tenders found</div>
        <div id="bidgpt-root">
          <div class="chip">Search Guntur &amp; Andhra Pradesh Tenders</div>
        </div>
      </body>
    </html>
    """
    parsed = parse_html(html, "https://bidalert.in/global-tender-city/achham-tenders")
    assert parsed.is_empty_listing is True
    assert len(parsed.cards) == 0


def test_multi_card_listing_page_extraction():
    html = """
    <html>
      <body>
        <h1>Maharashtra Tenders</h1>
        <div class="jet-listing-grid__item">
          <div>MAHARASHTRA Tenders BABID23439990</div>
          <div>Tender Info: Transformer Replacement Project</div>
          <div>Closing Date: 12-Dec-2026 03:00 PM</div>
          <a href="/tenders/gem-2026-b-8193653/">View Tender</a>
        </div>
        <div class="jet-listing-grid__item">
          <div>MAHARASHTRA Tenders BABID23439991</div>
          <div>Tender Info: Solar Street Lighting Maintenance</div>
          <div>Closing Date: 20-Dec-2026 05:00 PM</div>
          <a href="/tenders/gem-2026-b-8113711/">View Tender</a>
        </div>
      </body>
    </html>
    """
    parsed = parse_html(html, "https://bidalert.in/maharashtra-tenders")
    assert parsed.is_empty_listing is False
    assert len(parsed.cards) == 2
    assert parsed.cards[0].url == "https://bidalert.in/tenders/gem-2026-b-8193653/"
    assert parsed.cards[0].fields.tender_id == "BABID23439990"
    assert "Transformer Replacement" in (parsed.cards[0].title or "")
    assert parsed.cards[1].url == "https://bidalert.in/tenders/gem-2026-b-8113711/"
    assert parsed.cards[1].fields.tender_id == "BABID23439991"
    assert "Solar Street Lighting" in (parsed.cards[1].title or "")
