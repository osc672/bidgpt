"""Comprehensive geographical definitions and normalizations for Indian states and cities.

Maps spelling variations, closed-compound words (e.g. 'madhyapradesh', 'maharasthra'),
abbreviations, and city-to-state relationships so search queries reliably find all
relevant tenders across official listing pages and detail pages.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Any


def normalize(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _fuzzy_token(query: str, candidate: str) -> float:
    if not query or not candidate:
        return 0.0
    if query == candidate:
        return 1.0
    return difflib.SequenceMatcher(None, query, candidate).ratio()


TYPE_SYNONYMS: dict[str, set[str]] = {
    "electrical": {
        "electrical",
        "electrification",
        "wiring",
        "lighting",
        "streetlight",
        "transformer",
        "transformers",
        "substation",
        "substations",
        "switchgear",
        "transmission line",
        "power supply",
    },
    "civil": {"civil", "construction", "building", "road", "bridge", "drainage", "water"},
    "housekeeping": {"housekeeping", "cleaning", "sanitation", "janitorial"},
    "security": {"security", "guard", "guards", "surveillance"},
    "manpower": {"manpower", "staffing", "labour", "labor"},
    "printing": {"printing", "print", "paper", "papers", "stationery"},
    "supply": {"supply", "supplies", "procurement", "purchase"},
    "it": {
        "software", "computer", "computers", "laptop", "desktop", "server",
        "lan", "wan", "cyber", "firewall", "cloud", "data center", "antivirus",
        "it services", "computer network", "telecom network", "networking",
    },
    "transport": {"transport", "vehicle", "vehicles", "logistics", "railway", "railways", "rail", "train", "trains"},
    "catering": {"catering", "food", "canteen"},
    "medical": {
        "medical", "medicine", "medicines", "health", "hospital", "pharma",
        "pharmaceutical", "bph", "dispensary", "drugs", "healthcare", "clinic",
    },
    "maintenance": {"maintenance", "repair", "repairs", "amc"},
    "horticulture": {"horticulture", "gardening", "garden"},
    "painting": {"painting", "paint"},
}

CANONICAL_STATES: list[str] = [
    "Andhra Pradesh",
    "Arunachal Pradesh",
    "Assam",
    "Bihar",
    "Chhattisgarh",
    "Goa",
    "Gujarat",
    "Haryana",
    "Himachal Pradesh",
    "Jharkhand",
    "Karnataka",
    "Kerala",
    "Madhya Pradesh",
    "Maharashtra",
    "Manipur",
    "Meghalaya",
    "Mizoram",
    "Nagaland",
    "Odisha",
    "Punjab",
    "Rajasthan",
    "Sikkim",
    "Tamil Nadu",
    "Telangana",
    "Tripura",
    "Uttar Pradesh",
    "Uttarakhand",
    "West Bengal",
    "Delhi",
    "Jammu and Kashmir",
    "Ladakh",
    "Chandigarh",
    "Puducherry",
]

# Aliases, abbreviations, common typos, and closed-compound forms
STATE_ALIASES: dict[str, str] = {
    # Andhra Pradesh
    "ap": "Andhra Pradesh",
    "andhra": "Andhra Pradesh",
    "andhrapradesh": "Andhra Pradesh",
    "andhra pradesh": "Andhra Pradesh",
    "andhra prdesh": "Andhra Pradesh",
    # Madhya Pradesh
    "mp": "Madhya Pradesh",
    "madhya": "Madhya Pradesh",
    "madhyapradesh": "Madhya Pradesh",
    "madhya pradesh": "Madhya Pradesh",
    "madhyaprdesh": "Madhya Pradesh",
    "madhyapradesh tenders": "Madhya Pradesh",
    # Maharashtra
    "mh": "Maharashtra",
    "maha": "Maharashtra",
    "maharashtra": "Maharashtra",
    "maharasthra": "Maharashtra",
    "maharastra": "Maharashtra",
    "maharastrra": "Maharashtra",
    "maharashtr": "Maharashtra",
    "mahatenders": "Maharashtra",
    "maharashtra tenders": "Maharashtra",
    "maharastra tenders": "Maharashtra",
    "maharastrra tenders": "Maharashtra",
    # Uttar Pradesh
    "up": "Uttar Pradesh",
    "uttar": "Uttar Pradesh",
    "uttarpradesh": "Uttar Pradesh",
    "uttar pradesh": "Uttar Pradesh",
    "uttapradesh": "Uttar Pradesh",
    # Himachal Pradesh
    "hp": "Himachal Pradesh",
    "himachal": "Himachal Pradesh",
    "himachalpradesh": "Himachal Pradesh",
    "himachal pradesh": "Himachal Pradesh",
    # West Bengal
    "wb": "West Bengal",
    "westbengal": "West Bengal",
    "west bengal": "West Bengal",
    "bengal": "West Bengal",
    # Tamil Nadu
    "tn": "Tamil Nadu",
    "tamil": "Tamil Nadu",
    "tamilnadu": "Tamil Nadu",
    "tamil nadu": "Tamil Nadu",
    # Telangana
    "ts": "Telangana",
    "tg": "Telangana",
    "telangana": "Telangana",
    "telengana": "Telangana",
    # Karnataka
    "ka": "Karnataka",
    "karnataka": "Karnataka",
    "karnatka": "Karnataka",
    # Gujarat
    "gj": "Gujarat",
    "gujarat": "Gujarat",
    "gujrat": "Gujarat",
    # Rajasthan
    "rj": "Rajasthan",
    "rajasthan": "Rajasthan",
    "rajsthan": "Rajasthan",
    # Odisha
    "od": "Odisha",
    "odisha": "Odisha",
    "orissa": "Odisha",
    # Kerala
    "kl": "Kerala",
    "kerala": "Kerala",
    # Punjab
    "pb": "Punjab",
    "punjab": "Punjab",
    # Haryana
    "hr": "Haryana",
    "haryana": "Haryana",
    # Bihar
    "br": "Bihar",
    "bihar": "Bihar",
    # Assam
    "as": "Assam",
    "assam": "Assam",
    # Jharkhand
    "jh": "Jharkhand",
    "jharkhand": "Jharkhand",
    # Chhattisgarh
    "cg": "Chhattisgarh",
    "chhattisgarh": "Chhattisgarh",
    "chattisgarh": "Chhattisgarh",
    # Uttarakhand
    "uk": "Uttarakhand",
    "ua": "Uttarakhand",
    "uttarakhand": "Uttarakhand",
    "uttaranchal": "Uttarakhand",
    # Goa
    "ga": "Goa",
    "goa": "Goa",
    # Delhi
    "dl": "Delhi",
    "delhi": "Delhi",
    "newdelhi": "Delhi",
    "new delhi": "Delhi",
    # Jammu and Kashmir
    "jk": "Jammu and Kashmir",
    "jammu": "Jammu and Kashmir",
    "kashmir": "Jammu and Kashmir",
    "jammu and kashmir": "Jammu and Kashmir",
    "jammu & kashmir": "Jammu and Kashmir",
    "jammuandkashmir": "Jammu and Kashmir",
    # Sikkim
    "sk": "Sikkim",
    "sikkim": "Sikkim",
    # Manipur
    "mn": "Manipur",
    "manipur": "Manipur",
    # Meghalaya
    "ml": "Meghalaya",
    "meghalaya": "Meghalaya",
    # Mizoram
    "mz": "Mizoram",
    "mizoram": "Mizoram",
    # Nagaland
    "nl": "Nagaland",
    "nagaland": "Nagaland",
    # Tripura
    "tr": "Tripura",
    "tripura": "Tripura",
    # Ladakh
    "la": "Ladakh",
    "ladakh": "Ladakh",
    # Puducherry
    "py": "Puducherry",
    "puducherry": "Puducherry",
    "pondicherry": "Puducherry",
    # Chandigarh
    "ch": "Chandigarh",
    "chandigarh": "Chandigarh",
}

# Major cities mapped to their respective State
CITY_TO_STATE: dict[str, str] = {
    # Andhra Pradesh
    "guntur": "Andhra Pradesh",
    "guntoor": "Andhra Pradesh",
    "vijayawada": "Andhra Pradesh",
    "visakhapatnam": "Andhra Pradesh",
    "vizag": "Andhra Pradesh",
    "tirupati": "Andhra Pradesh",
    "kurnool": "Andhra Pradesh",
    "nellore": "Andhra Pradesh",
    "rajahmundry": "Andhra Pradesh",
    "kakinada": "Andhra Pradesh",
    "anantapur": "Andhra Pradesh",
    "kadapa": "Andhra Pradesh",
    "eluru": "Andhra Pradesh",
    "ongole": "Andhra Pradesh",
    "tenali": "Andhra Pradesh",
    "mangalagiri": "Andhra Pradesh",
    "amaravati": "Andhra Pradesh",
    "chittoor": "Andhra Pradesh",
    "srikakulam": "Andhra Pradesh",
    "vizianagaram": "Andhra Pradesh",
    "machilipatnam": "Andhra Pradesh",
    "proddatur": "Andhra Pradesh",
    "nandyal": "Andhra Pradesh",

    # Maharashtra
    "mumbai": "Maharashtra",
    "pune": "Maharashtra",
    "nagpur": "Maharashtra",
    "nashik": "Maharashtra",
    "thane": "Maharashtra",
    "aurangabad": "Maharashtra",
    "solapur": "Maharashtra",
    "kolhapur": "Maharashtra",
    "amravati": "Maharashtra",
    "navi mumbai": "Maharashtra",
    "kalyan": "Maharashtra",
    "jalgaon": "Maharashtra",
    "akola": "Maharashtra",
    "nanded": "Maharashtra",
    "latur": "Maharashtra",
    "dhule": "Maharashtra",
    "ahmednagar": "Maharashtra",
    "chandrapur": "Maharashtra",
    "parbhani": "Maharashtra",
    "jalna": "Maharashtra",
    "satara": "Maharashtra",

    # Madhya Pradesh
    "bhopal": "Madhya Pradesh",
    "indore": "Madhya Pradesh",
    "gwalior": "Madhya Pradesh",
    "jabalpur": "Madhya Pradesh",
    "ujjain": "Madhya Pradesh",
    "sagar": "Madhya Pradesh",
    "dewas": "Madhya Pradesh",
    "satna": "Madhya Pradesh",
    "ratlam": "Madhya Pradesh",
    "rewa": "Madhya Pradesh",
    "katni": "Madhya Pradesh",
    "singrauli": "Madhya Pradesh",
    "burhanpur": "Madhya Pradesh",
    "khandwa": "Madhya Pradesh",
    "morena": "Madhya Pradesh",
    "chhindwara": "Madhya Pradesh",

    # Telangana
    "hyderabad": "Telangana",
    "warangal": "Telangana",
    "secunderabad": "Telangana",
    "karimnagar": "Telangana",
    "nizamabad": "Telangana",
    "khammam": "Telangana",
    "ramagundam": "Telangana",
    "mahabubnagar": "Telangana",
    "nalgonda": "Telangana",
    "adilabad": "Telangana",

    # Karnataka
    "bengaluru": "Karnataka",
    "bangalore": "Karnataka",
    "mysuru": "Karnataka",
    "mysore": "Karnataka",
    "hubballi": "Karnataka",
    "hubli": "Karnataka",
    "mangaluru": "Karnataka",
    "mangalore": "Karnataka",
    "belagavi": "Karnataka",
    "belgaum": "Karnataka",
    "gulbarga": "Karnataka",
    "kalaburagi": "Karnataka",
    "davanagere": "Karnataka",
    "ballari": "Karnataka",
    "bellary": "Karnataka",
    "vijayapura": "Karnataka",
    "shimoga": "Karnataka",
    "shivamogga": "Karnataka",
    "tumakuru": "Karnataka",
    "bidar": "Karnataka",

    # Tamil Nadu
    "chennai": "Tamil Nadu",
    "coimbatore": "Tamil Nadu",
    "madurai": "Tamil Nadu",
    "trichy": "Tamil Nadu",
    "tiruchirappalli": "Tamil Nadu",
    "salem": "Tamil Nadu",
    "tirunelveli": "Tamil Nadu",
    "tiruppur": "Tamil Nadu",
    "erode": "Tamil Nadu",
    "vellore": "Tamil Nadu",
    "thoothukudi": "Tamil Nadu",
    "dindigul": "Tamil Nadu",
    "thanjavur": "Tamil Nadu",
    "ranipet": "Tamil Nadu",
    "sivakasi": "Tamil Nadu",

    # Gujarat
    "ahmedabad": "Gujarat",
    "surat": "Gujarat",
    "vadodara": "Gujarat",
    "rajkot": "Gujarat",
    "bhavnagar": "Gujarat",
    "jamnagar": "Gujarat",
    "gandhinagar": "Gujarat",
    "junagadh": "Gujarat",
    "gandhidham": "Gujarat",
    "anand": "Gujarat",
    "navsari": "Gujarat",
    "morbi": "Gujarat",

    # Uttar Pradesh
    "lucknow": "Uttar Pradesh",
    "kanpur": "Uttar Pradesh",
    "varanasi": "Uttar Pradesh",
    "agra": "Uttar Pradesh",
    "noida": "Uttar Pradesh",
    "ghaziabad": "Uttar Pradesh",
    "prayagraj": "Uttar Pradesh",
    "allahabad": "Uttar Pradesh",
    "meerut": "Uttar Pradesh",
    "bareilly": "Uttar Pradesh",
    "aligarh": "Uttar Pradesh",
    "moradabad": "Uttar Pradesh",
    "saharanpur": "Uttar Pradesh",
    "gorakhpur": "Uttar Pradesh",
    "firozabad": "Uttar Pradesh",
    "jhansi": "Uttar Pradesh",
    "muzaffarnagar": "Uttar Pradesh",
    "mathura": "Uttar Pradesh",
    "ayodhya": "Uttar Pradesh",

    # West Bengal
    "kolkata": "West Bengal",
    "howrah": "West Bengal",
    "durgapur": "West Bengal",
    "asansol": "West Bengal",
    "siliguri": "West Bengal",
    "bardhaman": "West Bengal",
    "malda": "West Bengal",
    "kharagpur": "West Bengal",

    # Rajasthan
    "jaipur": "Rajasthan",
    "jodhpur": "Rajasthan",
    "kota": "Rajasthan",
    "bikaner": "Rajasthan",
    "ajmer": "Rajasthan",
    "udaipur": "Rajasthan",
    "bhilwara": "Rajasthan",
    "alwar": "Rajasthan",
    "sikar": "Rajasthan",

    # Kerala
    "kochi": "Kerala",
    "cochin": "Kerala",
    "thiruvananthapuram": "Kerala",
    "trivandrum": "Kerala",
    "kozhikode": "Kerala",
    "calicut": "Kerala",
    "thrissur": "Kerala",
    "kollam": "Kerala",
    "palakkad": "Kerala",
    "alappuzha": "Kerala",
    "kannur": "Kerala",
    "kottayam": "Kerala",

    # Bihar
    "patna": "Bihar",
    "gaya": "Bihar",
    "bhagalpur": "Bihar",
    "muzaffarpur": "Bihar",
    "purnia": "Bihar",
    "darbhanga": "Bihar",
    "bihar sharif": "Bihar",
    "nawada": "Bihar",
    "rajauli": "Bihar",
    "ara": "Bihar",
    "arrah": "Bihar",
    "bhojpur": "Bihar",
    "sheikhpura": "Bihar",
    "nalanda": "Bihar",
    "samastipur": "Bihar",
    "begusarai": "Bihar",
    "bettiah": "Bihar",
    "champaran": "Bihar",
    "west champaran": "Bihar",
    "east champaran": "Bihar",
    "sheohar": "Bihar",
    "vaishali": "Bihar",
    "hazipur": "Bihar",
    "hajipur": "Bihar",
    "katihar": "Bihar",
    "munger": "Bihar",
    "saharsa": "Bihar",
    "sasaram": "Bihar",
    "dehri": "Bihar",
    "siwan": "Bihar",
    "motihari": "Bihar",
    "buxar": "Bihar",
    "kishanganj": "Bihar",
    "sitamarhi": "Bihar",
    "jamui": "Bihar",
    "khagaria": "Bihar",
    "araria": "Bihar",
    "madhepura": "Bihar",
    "lakhisarai": "Bihar",
    "gopalganj": "Bihar",
    "supaul": "Bihar",

    # Odisha
    "bhubaneswar": "Odisha",
    "cuttack": "Odisha",
    "rourkela": "Odisha",
    "berhampur": "Odisha",
    "sambalpur": "Odisha",
    "puri": "Odisha",
    "balasore": "Odisha",

    # Punjab / Haryana / Chandigarh
    "chandigarh": "Chandigarh",
    "ludhiana": "Punjab",
    "amritsar": "Punjab",
    "jalandhar": "Punjab",
    "patiala": "Punjab",
    "bathinda": "Punjab",
    "gurugram": "Haryana",
    "gurgaon": "Haryana",
    "faridabad": "Haryana",
    "panipat": "Haryana",
    "ambala": "Haryana",
    "yamunanagar": "Haryana",
    "rohtak": "Haryana",
    "hisar": "Haryana",
    "karnal": "Haryana",

    # Delhi
    "delhi": "Delhi",
    "new delhi": "Delhi",

    # Assam / Northeast
    "guwahati": "Assam",
    "silchar": "Assam",
    "dibrugarh": "Assam",
    "jorhat": "Assam",
    "nagaon": "Assam",

    # Jharkhand
    "ranchi": "Jharkhand",
    "jamshedpur": "Jharkhand",
    "dhanbad": "Jharkhand",
    "bokaro": "Jharkhand",
    "deoghar": "Jharkhand",

    # Chhattisgarh
    "raipur": "Chhattisgarh",
    "bhilai": "Chhattisgarh",
    "bilaspur": "Chhattisgarh",
    "korba": "Chhattisgarh",

    # Uttarakhand
    "dehradun": "Uttarakhand",
    "haridwar": "Uttarakhand",
    "roorkee": "Uttarakhand",
    "haldwani": "Uttarakhand",

    # Goa
    "panaji": "Goa",
    "margao": "Goa",
    "vasco da gama": "Goa",
}

# Invert CITY_TO_STATE: State -> Set of city strings
STATE_TO_CITIES: dict[str, set[str]] = {}
for _c, _s in CITY_TO_STATE.items():
    STATE_TO_CITIES.setdefault(_s, set()).add(_c)


def resolve_state(value: str | None) -> str | None:
    """Resolve a raw string to its canonical state name, handling typos and closed-compounds."""
    if not value:
        return None
    raw = value.strip().lower()
    # Strip common filler words
    raw = re.sub(r"\b(tenders?|tendors?|procurement|bids?|state|govt|government)\b", "", raw).strip()
    if not raw:
        return None

    # 1. Exact alias match
    if raw in STATE_ALIASES:
        return STATE_ALIASES[raw]

    # 2. Closed-compound match (e.g. 'madhyapradesh' or 'andhrapradesh')
    no_space = raw.replace(" ", "").replace("-", "").replace("_", "")
    if no_space in STATE_ALIASES:
        return STATE_ALIASES[no_space]

    # 3. Canonical states exact match
    for st in CANONICAL_STATES:
        st_lower = st.lower()
        if raw == st_lower or no_space == st_lower.replace(" ", ""):
            return st

    # 4. Fuzzy match against canonical states (handles 'maharasthra', 'madhyaprdesh', etc.)
    # Only for tokens with length >= 4 to prevent short greetings or acronyms (e.g. 'hi') from false matching.
    if len(raw) >= 4:
        best_match: str | None = None
        best_score = 0.0
        for st in CANONICAL_STATES:
            st_clean = st.lower().replace(" ", "")
            score = max(
                _fuzzy_token(raw, st.lower()),
                _fuzzy_token(no_space, st_clean),
            )
            if score > best_score:
                best_score = score
                best_match = st

        if best_score >= 0.78:
            return best_match

    return None


def resolve_city(value: str | None) -> tuple[str, str | None] | None:
    """Resolve a city string to (TitleCasedCity, StateName)."""
    if not value:
        return None
    raw = value.strip().lower()
    if raw in CITY_TO_STATE:
        return (raw.title(), CITY_TO_STATE[raw])
    return None


KNOWN_CITIES: set[str] = set(CITY_TO_STATE.keys())

AUTHORITY_TO_STATE: dict[str, str] = {
    "building construction department": "Bihar",
    "south central railway": "Telangana",
    "east central railway": "Bihar",
    "east coast railway": "Odisha",
    "south coast railway": "Andhra Pradesh",
    "north central railway": "Uttar Pradesh",
    "north eastern railway": "Uttar Pradesh",
    "central railway": "Maharashtra",
    "western railway": "Maharashtra",
    "eastern railway": "West Bengal",
    "southern railway": "Tamil Nadu",
    "south western railway": "Karnataka",
    "rail wheel factory": "Karnataka",
    "rwf": "Karnataka",
    "swr": "Karnataka",
    "west central railway": "Madhya Pradesh",
    "north western railway": "Rajasthan",
    "northeast frontier railway": "Assam",
    "south eastern railway": "West Bengal",
    "south east central railway": "Chhattisgarh",
    "delhi metro": "Delhi",
    "dmrc": "Delhi",
    "bescom": "Karnataka",
    "gescom": "Karnataka",
    "kptcl": "Karnataka",
    "msedcl": "Maharashtra",
    "msetcl": "Maharashtra",
    "hvpnl": "Haryana",
    "gvmc": "Andhra Pradesh",
}

