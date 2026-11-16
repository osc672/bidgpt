"""HTML/PDF cleaning and heuristic tender-field extraction.

The extractor is deliberately label-first: tender pages almost always expose
human-readable labels ("Location:", "Estimated Cost:", ...). Regex heuristics
are only fallbacks, and missing values stay missing -- the chat layer renders
"not listed on the page" rather than guessing.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .security import sanitize_text

LABEL_ALIASES: dict[str, tuple[str, ...]] = {
    "title": (
        "tender title",
        "title",
        "name of work",
        "work name",
        "name of the work",
        "tender name",
        "tender info",
        "tender description",
        "subject",
    ),
    "tender_type": (
        "tender type",
        "type",
        "tender category",
        "category",
        "nature of work",
        "tender class",
        "class",
    ),
    "location": (
        "location",
        "place",
        "city",
        "district",
        "area",
        "work location",
        "location of work",
        "place of work",
        "tender location",
    ),
    "state": (
        "state",
        "state name",
        "state ut",
        "state and ut",
        "region",
    ),
    "tender_id": (
        "tender id",
        "tender no",
        "tender number",
        "tender ref",
        "tender ref no",
        "tender reference",
        "ref no",
        "ref number",
        "reference no",
        "reference number",
        "bid id",
        "bid no",
        "bid number",
        "nit no",
        "reference",
    ),
    "emd": (
        "emd",
        "earnest money deposit",
        "earnest money",
        "emd amount",
        "emd fee",
        "emd value",
    ),
    "emd_exemption": (
        "exemption",
        "emd exemption",
        "emd exemption allowed",
        "exemption allowed",
        "msme exemption",
    ),
    "ecv": (
        "ecv",
        "estimated contract value",
        "estimated cost value",
        "contract value",
        "estimated value of contract",
    ),
    "budget": (
        "budget",
        "estimated cost",
        "estimated value",
        "tender value",
        "contract value",
        "amount",
        "value",
        "cost",
        "estimated amount",
        "tender amount",
        "total value",
        "ecv",
        "estimated contract value",
    ),
    "timeline": (
        "timeline",
        "duration",
        "period",
        "completion period",
        "contract period",
        "time of completion",
        "work period",
        "delivery period",
        "project duration",
    ),
    "products": (
        "products",
        "product",
        "products scope",
        "scope",
        "scope of work",
        "work description",
        "description of work",
        "items",
        "item",
        "product category",
        "material",
        "materials",
        "description",
        "service description",
    ),
    "department": (
        "department",
        "organisation",
        "organization",
        "office",
        "authority",
        "tender inviting authority",
        "board",
        "corporation",
        "division",
        "circle",
        "office name",
        "department name",
    ),
    "bid_by_date": (
        "last date to bid",
        "bid due date",
        "last date",
        "due date",
        "submission deadline",
        "closing date",
        "bid end date",
        "last date of submission",
        "last date for submission",
        "bid submission end date",
        "tender closing date",
        "deadline",
        "last date and time for submission",
        "closing date and time",
        "bid closing date",
    ),
}

_LABEL_TO_FIELD = {
    alias: field for field, aliases in LABEL_ALIASES.items() for alias in aliases
}

_KNOWN_PLACES = {
    "guntur": "Guntur",
    "guntoor": "Guntur",
    "hyderabad": "Hyderabad",
    "vijayawada": "Vijayawada",
    "visakhapatnam": "Visakhapatnam",
    "vizag": "Visakhapatnam",
    "warangal": "Warangal",
    "khammam": "Khammam",
    "kurnool": "Kurnool",
    "nellore": "Nellore",
    "tirupati": "Tirupati",
    "rajahmundry": "Rajahmundry",
    "kakinada": "Kakinada",
    "anantapur": "Anantapur",
    "kadapa": "Kadapa",
    "ongole": "Ongole",
    "tenali": "Tenali",
    "mangalagiri": "Mangalagiri",
    "amaravati": "Amaravati",
    "delhi": "Delhi",
    "new delhi": "New Delhi",
    "mumbai": "Mumbai",
    "pune": "Pune",
    "chennai": "Chennai",
    "bengaluru": "Bengaluru",
    "bangalore": "Bengaluru",
    "kolkata": "Kolkata",
    "ahmedabad": "Ahmedabad",
    "jaipur": "Jaipur",
    "lucknow": "Lucknow",
    "bhopal": "Bhopal",
    "patna": "Patna",
    "ranchi": "Ranchi",
    "bhubaneswar": "Bhubaneswar",
    "guwahati": "Guwahati",
    "chandigarh": "Chandigarh",
    "kochi": "Kochi",
    "cochin": "Kochi",
    "thiruvananthapuram": "Thiruvananthapuram",
    "trivandrum": "Thiruvananthapuram",
    "coimbatore": "Coimbatore",
    "madurai": "Madurai",
    "nagpur": "Nagpur",
    "indore": "Indore",
    "surat": "Surat",
    "vadodara": "Vadodara",
    "noida": "Noida",
    "gurugram": "Gurugram",
    "gurgaon": "Gurugram",
    "faridabad": "Faridabad",
    "ghaziabad": "Ghaziabad",
    "mysuru": "Mysuru",
    "mysore": "Mysuru",
    "hubli": "Hubli",
    "belagavi": "Belagavi",
    "mangaluru": "Mangaluru",
    "mangalore": "Mangaluru",
    "tiruchirappalli": "Tiruchirappalli",
    "trichy": "Tiruchirappalli",
    "salem": "Salem",
    "vellore": "Vellore",
    "erode": "Erode",
    "tirunelveli": "Tirunelveli",
    "kollam": "Kollam",
    "thrissur": "Thrissur",
    "kozhikode": "Kozhikode",
    "kannur": "Kannur",
    "kottayam": "Kottayam",
    "palakkad": "Palakkad",
}

_TYPE_KEYWORDS = {
    "electrical": "Electrical Works",
    "wiring": "Electrical Works",
    "street light": "Electrical Works",
    "transformer": "Electrical Works",
    "substation": "Electrical Works",
    "switchgear": "Electrical Works",
    "civil": "Civil Works",
    "construction": "Construction Works",
    "road": "Road Works",
    "bridge": "Civil Works",
    "building": "Construction Works",
    "housekeeping": "Housekeeping Services",
    "sanitation": "Sanitation Services",
    "cleaning": "Housekeeping Services",
    "security": "Security Services",
    "guard": "Security Services",
    "manpower": "Manpower Services",
    "staffing": "Manpower Services",
    "printing": "Printing Services",
    "stationery": "Supply of Stationery",
    "supply": "Supply",
    "procurement": "Supply",
    "software": "IT Services",
    "it services": "IT Services",
    "computer": "IT Services",
    "network": "IT Services",
    "transport": "Transport Services",
    "vehicle": "Transport Services",
    "catering": "Catering Services",
    "food": "Catering Services",
    "medical": "Medical Supplies",
    "medicine": "Medical Supplies",
    "hospital": "Medical Services",
    "maintenance": "Maintenance Services",
    "repair": "Repair Works",
    "painting": "Painting Works",
    "water": "Water Works",
    "drainage": "Drainage Works",
    "horticulture": "Horticulture Services",
    "gardening": "Horticulture Services",
    "furniture": "Supply of Furniture",
}

STOP_LABELS = {"page", "home", "menu", "navigation", "share", "print", "download"}

_CURRENCY_RE = re.compile(
    r"((?:₹|rs\.?|inr)\s*)?"
    r"(\d{1,3}(?:[,.]\d{2,3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
    r"(\s*(?:lakh|lakhs|lac|lacs|crore|crores|cr))?",
    re.I,
)
_TIMELINE_RE = re.compile(
    r"\b\d{1,3}\s*(?:working\s+)?(?:days?|weeks?|months?|years?)\b", re.I
)
_ISO_DATE_RE = re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b")
_NUM_DATE_RE = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\b")
_TEXT_DATE_RE = re.compile(
    r"\b(\d{1,2})[\s\-]+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)[\s\-]+(\d{4})\b",
    re.I,
)
_TIME_SUFFIX_RE = re.compile(r"\b(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AaPp][Mm])?)\b")
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_TYPE_WORDS = ("tender", "tenders", "notice", "invitation", "bid", "bids", "rfp", "rfq")


@dataclass(slots=True)
class ExtractedFields:
    title: str | None = None
    tender_type: str | None = None
    location: str | None = None
    budget: str | None = None
    timeline: str | None = None
    products: str | None = None
    department: str | None = None
    bid_by_date: str | None = None
    tender_id: str | None = None
    emd: str | None = None
    emd_exemption: str | None = None
    ecv: str | None = None
    state: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {key: value for key, value in asdict(self).items()}

    def is_empty(self) -> bool:
        return not any(asdict(self).values())


@dataclass(slots=True)
class ExtractedCard:
    url: str
    title: str | None
    text: str
    fields: ExtractedFields


@dataclass(slots=True)
class ParsedPage:
    title: str | None
    text: str
    links: list[str]
    fields: ExtractedFields
    cards: list[ExtractedCard] = field(default_factory=list)
    is_empty_listing: bool = False


def normalize_label(label: str) -> str:
    value = label.lower().replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _canonical_field(label: str) -> str | None:
    normalized = normalize_label(label)
    if normalized in _LABEL_TO_FIELD:
        return _LABEL_TO_FIELD[normalized]
    # Accommodate labels such as "Tender Title (English)".
    for alias, field in _LABEL_TO_FIELD.items():
        if normalized.startswith(alias + " ") or normalized.endswith(" " + alias):
            return field
    return None


def clean_value(value: str | None, *, max_chars: int = 600) -> str | None:
    if value is None:
        return None
    text = sanitize_text(value, max_chars=max_chars)
    text = text.strip(" \t\n:-–—|")
    text = re.sub(r"\s+", " ", text).strip()
    if not text or text.lower() in {"n/a", "na", "none", "null", "-", "--", "not applicable"}:
        return None
    return text


def parse_date(value: str | None) -> str | None:
    """Return an ISO date (with optional time) when a date can be parsed unambiguously."""
    if not value:
        return None
    text = sanitize_text(value, max_chars=200)

    time_match = _TIME_SUFFIX_RE.search(text)
    time_suffix = f" {time_match.group(1).strip()}" if time_match else ""

    match = _ISO_DATE_RE.search(text)
    if match:
        year, month, day = (int(part) for part in match.groups())
        safe = _safe_date(year, month, day)
        return f"{safe}{time_suffix}" if safe else match.group(0)

    match = _NUM_DATE_RE.search(text)
    if match:
        first, second, year = match.groups()
        first_i, second_i = int(first), int(second)
        year_i = int(year)
        if year_i < 100:
            year_i += 2000
        # Indian tender portals are day-first. Flip only when the first value
        # cannot be a day (e.g. 10/31/2026).
        if first_i > 12 and second_i <= 12:
            day, month = first_i, second_i
        elif second_i > 12 and first_i <= 12:
            month, day = first_i, second_i
        else:
            day, month = first_i, second_i
        parsed = _safe_date(year_i, month, day)
        if parsed:
            return f"{parsed}{time_suffix}"

    match = _TEXT_DATE_RE.search(text)
    if match:
        day, month_name, year = match.groups()
        month = _MONTHS[month_name[:3].lower()]
        parsed = _safe_date(int(year), month, int(day))
        return f"{parsed}{time_suffix}" if parsed else match.group(0)

    return None


def _safe_date(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _extract_label_pairs(lines: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in lines:
        if ":" not in line:
            continue
        left, right = line.split(":", 1)
        field = _canonical_field(left)
        value = clean_value(right)
        if field and value and field not in found:
            found[field] = value
    # Dash-separated labels are accepted only when the label is known.
    for line in lines:
        if ":" in line or " - " not in line:
            continue
        left, right = line.split(" - ", 1)
        field = _canonical_field(left)
        value = clean_value(right)
        if field and value and field not in found:
            found[field] = value
    return found


def _table_data(soup: BeautifulSoup) -> tuple[list[str], list[dict[str, str]], list[str]]:
    """Extract table label/value lines and header-mapped records."""
    extra_lines: list[str] = []
    records: list[dict[str, str]] = []
    for table in soup.find_all("table"):
        pending_headers: list[str | None] | None = None
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            values = [clean_value(cell.get_text(" ", strip=True), max_chars=500) or "" for cell in cells]
            if not any(values):
                continue
            canonical = [_canonical_field(value) for value in values]
            cell_names = [cell.name for cell in cells]
            is_header = bool(cells) and all(name == "th" for name in cell_names)
            if is_header:
                pending_headers = canonical
                extra_lines.append(" | ".join(values))
                continue
            if len(values) >= 2 and canonical[0] and (
                cell_names[0] == "th" or not canonical[1]
            ):
                extra_lines.append(f"{values[0]}: {values[1]}")
                extra_lines.append(" | ".join(values))
                if pending_headers:
                    record: dict[str, str] = {}
                    for field, value in zip(pending_headers, values):
                        if field and value and field not in record:
                            record[field] = clean_value(value) or ""
                    if record:
                        records.append(record)
                continue
            extra_lines.append(" | ".join(values))
            if pending_headers:
                record = {}
                for field, value in zip(pending_headers, values):
                    if field and value and field not in record:
                        record[field] = clean_value(value) or ""
                if len(record) >= 2:
                    records.append(record)
    return extra_lines, records, []


def _infer_type(text: str) -> str | None:
    haystack = text.lower()
    matches: list[tuple[int, str]] = []
    for keyword, label in _TYPE_KEYWORDS.items():
        index = haystack.find(keyword)
        if index >= 0:
            matches.append((index, label))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0])
    return matches[0][1]


def _infer_location(text: str) -> str | None:
    haystack = text.lower()
    for alias, canonical in sorted(_KNOWN_PLACES.items(), key=lambda item: -len(item[0])):
        if re.search(rf"\b{re.escape(alias)}\b", haystack):
            return canonical
    return None


def _infer_budget(text: str) -> str | None:
    for match in _CURRENCY_RE.finditer(text):
        symbol = (match.group(1) or "").strip()
        number = match.group(2)
        unit = (match.group(3) or "").strip().lower()
        prefix = text[max(0, match.start() - 35) : match.start()].lower()
        if not symbol and not unit:
            if not any(word in prefix for word in ("cost", "value", "ecv", "budget", "amount", "price", "fee")):
                continue
            if len(number.replace(",", "").replace(".", "")) < 4:
                continue
        if any(word in prefix for word in ("date", "year", "page", "phone", "mobile", "pin", "code", "portal")):
            continue
        matched_str = match.group(0).strip()
        cleaned = clean_value(matched_str, max_chars=120)
        if cleaned and len(cleaned) >= 2:
            return cleaned
    return None


def _infer_timeline(text: str) -> str | None:
    match = _TIMELINE_RE.search(text)
    return clean_value(match.group(0), max_chars=80) if match else None


def _infer_bid_date(text: str) -> str | None:
    for pattern in (_ISO_DATE_RE, _NUM_DATE_RE, _TEXT_DATE_RE):
        match = pattern.search(text)
        if match:
            parsed = parse_date(match.group(0))
            if parsed:
                return parsed
    return None


def _infer_products(text: str) -> str | None:
    match = re.search(
        r"\b(?:supply|supplying|procurement|purchase|scope of work|work involves)\s+(?:of\s+)?([^.\n]{8,240})",
        text,
        re.I,
    )
    if match:
        return clean_value(match.group(0), max_chars=260)
    return None


def _infer_department(text: str) -> str | None:
    match = re.search(
        r"\b((?:Department of|Office of the|Office of|Directorate of|Municipal Corporation|"
        r"Development Authority|Public Works Department|PWD|Zilla Parishad|Panchayat Raj|"
        r"Health Department|Education Department|Revenue Department)[A-Za-z0-9 ,&.'-]{2,80})",
        text,
    )
    return clean_value(match.group(1), max_chars=160) if match else None


_KNOWN_STATES: dict[str, str] = {
    "andhra pradesh": "Andhra Pradesh",
    "arunachal pradesh": "Arunachal Pradesh",
    "assam": "Assam",
    "bihar": "Bihar",
    "chhattisgarh": "Chhattisgarh",
    "goa": "Goa",
    "gujarat": "Gujarat",
    "haryana": "Haryana",
    "himachal pradesh": "Himachal Pradesh",
    "jharkhand": "Jharkhand",
    "karnataka": "Karnataka",
    "kerala": "Kerala",
    "madhya pradesh": "Madhya Pradesh",
    "maharashtra": "Maharashtra",
    "manipur": "Manipur",
    "meghalaya": "Meghalaya",
    "mizoram": "Mizoram",
    "nagaland": "Nagaland",
    "odisha": "Odisha",
    "orissa": "Odisha",
    "punjab": "Punjab",
    "rajasthan": "Rajasthan",
    "sikkim": "Sikkim",
    "tamil nadu": "Tamil Nadu",
    "telangana": "Telangana",
    "tripura": "Tripura",
    "uttar pradesh": "Uttar Pradesh",
    "uttarakhand": "Uttarakhand",
    "west bengal": "West Bengal",
    "delhi": "Delhi",
    "jammu and kashmir": "Jammu and Kashmir",
    "ladakh": "Ladakh",
    "puducherry": "Puducherry",
    "pondicherry": "Puducherry",
    "chandigarh": "Chandigarh",
    "andaman and nicobar islands": "Andaman and Nicobar Islands",
    "andaman and nicobar": "Andaman and Nicobar Islands",
    "daman and diu": "Daman and Diu",
    "dadra and nagar haveli": "Dadra and Nagar Haveli",
    "dadra and nagar haveli and daman and diu": "Dadra and Nagar Haveli and Daman and Diu",
    "lakshadweep": "Lakshadweep",
}

_CITY_TO_STATE: dict[str, str] = {
    "mumbai": "Maharashtra",
    "pune": "Maharashtra",
    "nagpur": "Maharashtra",
    "nashik": "Maharashtra",
    "thane": "Maharashtra",
    "guntur": "Andhra Pradesh",
    "vijayawada": "Andhra Pradesh",
    "visakhapatnam": "Andhra Pradesh",
    "vizag": "Andhra Pradesh",
    "tirupati": "Andhra Pradesh",
    "hyderabad": "Telangana",
    "warangal": "Telangana",
    "bengaluru": "Karnataka",
    "bangalore": "Karnataka",
    "mysuru": "Karnataka",
    "chennai": "Tamil Nadu",
    "coimbatore": "Tamil Nadu",
    "kolkata": "West Bengal",
    "ahmedabad": "Gujarat",
    "surat": "Gujarat",
    "vadodara": "Gujarat",
    "jaipur": "Rajasthan",
    "lucknow": "Uttar Pradesh",
    "noida": "Uttar Pradesh",
    "bhopal": "Madhya Pradesh",
    "indore": "Madhya Pradesh",
    "patna": "Bihar",
    "ranchi": "Jharkhand",
    "bhubaneswar": "Odisha",
    "guwahati": "Assam",
    "chandigarh": "Chandigarh",
    "kochi": "Kerala",
    "thiruvananthapuram": "Kerala",
    "delhi": "Delhi",
    "new delhi": "Delhi",
}


def _infer_tender_id(text: str) -> str | None:
    # BidAlert format e.g. BAMAH23382225, BAMAH23382222
    match = re.search(r"\b(BA[A-Z]{2,4}\d{5,12})\b", text)
    if match:
        return match.group(1).strip()
    match = re.search(
        r"\b(?:Tender\s*(?:ID|Ref|No|Number)|Ref\s*(?:No|ID)?|NIT\s*No)[\s.:#-]+([A-Za-z0-9_\-\/]{4,40})\b",
        text,
        re.I,
    )
    if match:
        candidate = match.group(1).strip()
        if candidate.lower() not in {
            "not", "na", "none", "refer", "view", "like", "share", "follow",
            "download", "details", "active", "login", "home", "back", "next",
            "close", "apply", "submit", "tender", "tenders",
        }:
            if any(ch.isdigit() for ch in candidate) or "-" in candidate or "/" in candidate or "_" in candidate:
                return candidate
    return None


def _infer_emd(text: str) -> str | None:
    match = re.search(
        r"\bEMD\s*[:\-]\s*([₹\w,.\s]+?)(?=\s+(?:Exemption|Closing|ECV|Due|Status|\n|$))",
        text,
        re.I,
    )
    if match:
        cand = clean_value(match.group(1), max_chars=80)
        if cand:
            cand_lower = cand.lower()
            if any(ch.isdigit() for ch in cand) or any(k in cand_lower for k in ("refer", "doc", "nil", "exempt", "na", "n/a", "free", "zero")):
                return cand
    match = re.search(r"\bEMD\s*[:\-]\s*((?:₹|Rs\.?|INR)?\s*[\d,]+(?:\.\d{2})?)", text, re.I)
    if match:
        return clean_value(match.group(1), max_chars=80)
    return None


def _infer_emd_exemption(text: str) -> str | None:
    match = re.search(
        r"\b(?:EMD\s+)?Exemption\s*[:\-]\s*(Yes|No|Allowed|Not Allowed|Applicable|Not Applicable)\b",
        text,
        re.I,
    )
    if match:
        return match.group(1).capitalize()
    return None


def _infer_ecv(text: str) -> str | None:
    match = re.search(
        r"\bECV\s*[:\-]\s*([₹\w,.\s]+?)(?=\s+(?:EMD|Exemption|Closing|Due|Status|\n|$))",
        text,
        re.I,
    )
    if match:
        return clean_value(match.group(1), max_chars=80)
    return None


def _infer_state(text: str) -> str | None:
    match = re.search(r"\b([A-Za-z ]{3,30})\s+[Tt]enders\b", text)
    if match:
        cand = match.group(1).lower().strip()
        if cand in _KNOWN_STATES:
            return _KNOWN_STATES[cand]
    return None


def _clean_title(value: str | None) -> str | None:
    result = clean_value(value, max_chars=300)
    if not result:
        return None
    return result


# Patterns that indicate a generic site/listing title rather than an actual tender title
_GENERIC_TITLE_PATTERNS = [
    r"^all tenders",
    r"^tenders$",
    r"^all active tenders",
    r"find government",
    r"eprocurement",
    r"etendering",
    r"egp portal",
    r"e-procurement",
    r"e-tendering",
    r"government tenders across india",
    r"all tenders.*india",
    r"latest.*tenders.*india",
    r"tender portal",
    r"tender search",
    r"bids and tenders",
    r"public procurement",
]
_GENERIC_TITLE_RE = re.compile("|".join(_GENERIC_TITLE_PATTERNS), re.I)


def _is_generic_title(value: str | None) -> bool:
    """Return True if the title is a site-wide/listing-page title rather than an actual tender title."""
    if not value:
        return False
    return bool(_GENERIC_TITLE_RE.search(value))


def extract_fields(
    text: str,
    *,
    title_hint: str | None = None,
    pairs: dict[str, str] | None = None,
    records: list[dict[str, str]] | None = None,
    page_url: str | None = None,
) -> ExtractedFields:
    """Extract tender fields from cleaned text plus optional label/table data."""
    text = sanitize_text(text, max_chars=50_000)
    pairs = dict(pairs or {})
    records = records or []
    merged: dict[str, str] = {}
    for field, value in pairs.items():
        cleaned = clean_value(value)
        if field and cleaned and field not in merged:
            merged[field] = cleaned
    for record in records:
        for field, value in record.items():
            cleaned = clean_value(value)
            if field and cleaned and field not in merged:
                merged[field] = cleaned

    title = _clean_title(
        merged.get("title")
        or (records[0].get("title") if records else None)
        or title_hint
    )

    # A heading is a good title fallback when no explicit label exists.
    if not title:
        for line in text.splitlines()[:30]:
            if any(word in line.lower() for word in _TYPE_WORDS) and 4 <= len(line) <= 220:
                title = _clean_title(line)
                break

    tender_type = clean_value(merged.get("tender_type"), max_chars=120)
    if not tender_type:
        tender_type = _infer_type(f"{title or ''}\n{text}")

    budget = clean_value(merged.get("budget"), max_chars=160)
    if not budget:
        budget = _infer_budget(text)

    timeline = clean_value(merged.get("timeline"), max_chars=100)
    if not timeline:
        timeline = _infer_timeline(text)

    products = clean_value(merged.get("products"), max_chars=400)
    if not products:
        products = _infer_products(text)

    department = clean_value(merged.get("department"), max_chars=180)
    if not department:
        department = _infer_department(text)

    bid_by = parse_date(merged.get("bid_by_date"))
    if not bid_by and merged.get("bid_by_date"):
        bid_by = clean_value(merged.get("bid_by_date"), max_chars=120)
    if not bid_by:
        # Prefer a date near a deadline word over the first date on the page.
        deadline_match = re.search(
            r"(?:last date|due date|deadline|closing|submission)[^\n]{0,120}",
            text,
            re.I,
        )
        if deadline_match:
            bid_by = _infer_bid_date(deadline_match.group(0))
        if not bid_by:
            bid_by = _infer_bid_date(text)

    tender_id = clean_value(merged.get("tender_id"), max_chars=80)
    if not tender_id:
        tender_id = _infer_tender_id(text)

    emd = clean_value(merged.get("emd"), max_chars=80)
    if not emd:
        emd = _infer_emd(text)

    emd_exemption = clean_value(merged.get("emd_exemption"), max_chars=80)
    if not emd_exemption:
        emd_exemption = _infer_emd_exemption(text)

    ecv = clean_value(merged.get("ecv"), max_chars=80)
    if not ecv:
        ecv = _infer_ecv(text)
    if not ecv and budget:
        ecv = budget
    if not budget and ecv:
        budget = ecv

    # State: from merged labels, or from URL slug, or from title/text
    state = clean_value(merged.get("state"), max_chars=100)
    if not state and page_url:
        page_url_lower = page_url.lower()
        for s_alias, s_canon in _KNOWN_STATES.items():
            slug = s_alias.replace(" ", "-")
            if f"/{slug}-tenders" in page_url_lower or f"/{slug}" in page_url_lower:
                state = s_canon
                break
    if not state:
        state = _infer_state(f"{title or ''}\n{text[:5000]}")

    # Location: from merged labels, or from URL city slug, or inferred from text
    location = clean_value(merged.get("location"), max_chars=160)
    if not location and page_url:
        city_m = re.search(r"/([a-z0-9\-]+)-city(?:/|$)", page_url.lower())
        if city_m:
            cand_slug = city_m.group(1).replace("-", " ")
            location = _KNOWN_PLACES.get(cand_slug, cand_slug.title())

    if not location:
        cand_loc = _infer_location(f"{title or ''}\n{text[:5_000]}")
        if cand_loc:
            # Cross-state guard: if page state is known, don't allow footer links to assign a different state's city
            if state:
                city_st = _CITY_TO_STATE.get(cand_loc.lower())
                if not city_st or city_st == state:
                    location = cand_loc
            else:
                location = cand_loc

    if not location and state:
        location = state

    # If title is missing or generic (e.g. "All Tenders"), derive a specific title from location/state
    if not title or _is_generic_title(title):
        if location and state and location != state:
            title = f"{location} ({state}) Tenders"
        elif location:
            title = f"{location} Tenders"
        elif state:
            title = f"{state} Tenders"

    return ExtractedFields(
        title=title,
        tender_type=tender_type,
        location=location,
        budget=budget,
        timeline=timeline,
        products=products,
        department=department,
        bid_by_date=bid_by,
        tender_id=tender_id,
        emd=emd,
        emd_exemption=emd_exemption,
        ecv=ecv,
        state=state,
    )


def extract_fields_from_text(
    text: str, *, title_hint: str | None = None, page_url: str | None = None
) -> ExtractedFields:
    cleaned = sanitize_text(text, max_chars=50_000)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    pairs = _extract_label_pairs(lines)
    if title_hint is None:
        title_hint = lines[0] if lines else None
    return extract_fields(
        cleaned, title_hint=title_hint, pairs=pairs, records=[], page_url=page_url
    )


def parse_html(html_text: str, page_url: str) -> ParsedPage:
    soup = BeautifulSoup(html_text or "", "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "template", "nav", "footer", "aside"]):
        tag.decompose()

    # Decompose embedded widget containers and global navigation menus so their links/chips do not contaminate content
    for widget in soup.select(
        "#bidgpt-root, #bidgpt-widget-container, [class*='bidgpt'], [id*='bidgpt'], "
        ".site-header, .site-footer, .main-navigation, .main-menu"
    ):
        widget.decompose()

    links: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href")
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(page_url, href.strip())
        if absolute.startswith(("http://", "https://")):
            links.append(absolute)

    # Check for empty listing indicators
    raw_body_text = (soup.body or soup).get_text(" ", strip=True).lower()
    is_empty_listing = any(
        marker in raw_body_text
        for marker in (
            "no tenders found",
            "no tender found",
            "0 tenders found",
            "no records found",
            "no matching tenders",
            "no active tenders",
        )
    )

    # Extract individual tender cards on listing pages (e.g. Elementor / JetEngine / custom tender-card)
    cards: list[ExtractedCard] = []
    seen_card_urls: set[str] = set()

    view_links = [
        a for a in soup.find_all("a", href=True)
        if a.get_text().strip().lower() in ("view tender", "view details", "open tender")
    ]
    if not view_links:
        for tc in soup.find_all(class_=lambda c: c and any(k in str(c).lower() for k in ("tender-card", "tender_card"))):
            a_elem = tc.find("a", href=True)
            if a_elem:
                view_links.append(a_elem)

    for a in view_links:
        href = a.get("href", "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        tender_url = urljoin(page_url, href)
        parsed_path = urlparse(tender_url).path.rstrip("/")
        if parsed_path in ("", "/tenders", "/tender"):
            continue
        if tender_url == page_url or tender_url in seen_card_urls:
            continue

        card_el = a.find_parent(class_=lambda c: c and any(k in str(c).lower() for k in ("jet-listing-grid__item", "tender-card", "tender_card", "card", "item")))
        if card_el is None:
            card_el = a.parent.parent if a.parent and a.parent.parent else a.parent

        if card_el:
            card_text = card_el.get_text("\n", strip=True)
            card_fields = extract_fields_from_text(card_text, page_url=tender_url)
            seen_card_urls.add(tender_url)
            cards.append(ExtractedCard(
                url=tender_url,
                title=card_fields.title,
                text=card_text,
                fields=card_fields,
            ))

    if cards:
        is_empty_listing = False

    table_lines, records, _ = _table_data(soup)
    og_title = soup.find("meta", attrs={"property": "og:title"})
    title_hint = None
    if og_title and og_title.get("content"):
        candidate = _clean_title(og_title.get("content"))
        if candidate and not _is_generic_title(candidate):
            title_hint = candidate
    if not title_hint:
        h1 = soup.find("h1")
        if h1:
            candidate = _clean_title(h1.get_text(" ", strip=True))
            if candidate and not _is_generic_title(candidate):
                title_hint = candidate
    if not title_hint and soup.title:
        candidate = _clean_title(soup.title.get_text(" ", strip=True))
        if candidate and not _is_generic_title(candidate):
            title_hint = candidate

    body = soup.body or soup
    body_text = body.get_text("\n", strip=True)
    if table_lines:
        body_text = body_text + "\n" + "\n".join(table_lines)
    body_text = sanitize_text(body_text, max_chars=50_000)
    lines = [line.strip() for line in body_text.splitlines() if line.strip()]
    pairs = _extract_label_pairs(lines)

    fields = extract_fields(
        body_text,
        title_hint=title_hint,
        pairs=pairs,
        records=records,
        page_url=page_url,
    )
    if cards and not fields.tender_id:
        fields = cards[0].fields

    return ParsedPage(
        title=fields.title or title_hint,
        text=body_text,
        links=links,
        fields=fields,
        cards=cards,
        is_empty_listing=is_empty_listing,
    )


def parse_pdf_text(text: str, page_url: str) -> ExtractedFields:
    return extract_fields_from_text(text, title_hint=None, page_url=page_url)
