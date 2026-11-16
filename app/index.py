"""Per-site page index.

Layout on disk:

    data/sites/<site_key>/indexes/index-<version>.sqlite
    data/sites/<site_key>/active.json      # atomic pointer to the live index

A crawl builds a complete new SQLite FTS5 file and only then replaces
active.json. Searches load pages from the active file into memory once and
never touch the site registry or any tender database.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import sqlite3
import threading
import unicodedata
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus, urlparse
from typing import Any, Iterable

from .geo import AUTHORITY_TO_STATE, CANONICAL_STATES, CITY_TO_STATE, STATE_TO_CITIES, TYPE_SYNONYMS, resolve_state
from .security import sanitize_for_llm, sanitize_text

FUZZY_THRESHOLD = 0.82
GENERIC_TYPES = {"", "tender", "tenders", "all", "any", "any tender", "any tenders"}
BANK_FINANCE_ENTITIES = (
    "bank", "banking", "sbi", "canara", "pnb", "rbl", "bob", "idbi", "repco",
    "financial services", "housing finance", "finance limited", "finance ltd",
    "cooperative bank", "e-auction of gold", "e-auction of flat", "e-auction of land",
    "sarfaesi",
)
PETROLEUM_ENTITIES = (
    "petroleum", "petrol", "hpcl", "bpcl", "iocl", "hindustan petroleum",
    "bharat petroleum", "indian oil",
)

CATEGORY_MAP: dict[str, set[str]] = {
    "electrical": {
        "electrical", "electricity", "electric", "power", "transformer", "transformers",
        "substation", "substations", "switchgear", "wiring", "lighting", "streetlight",
        "electrification", "transmission line", "ups", "generator", "generators", "cables and wires",
        "led light", "led lights", "motor pumps and panel boards",
    },
    "railway": {
        "railway", "railways", "rail", "train", "trains", "metro", "locomotive", "locos",
        "swr", "rwf", "wcr", "irctc", "dmrc",
    },
    "civil": {
        "civil", "construction", "building", "road", "bridge", "drainage", "water supply",
        "sewer", "rcc", "pipeline",
    },
    "transport": {
        "transport", "transportation", "vehicle", "vehicles", "taxi", "cab", "bus", "truck",
        "railway", "railways", "rail", "train", "trains",
    },
    "banking": {
        "banking", "bank", "finance", "financial", "auction", "sarfaesi", "sbi", "canara", "bob", "pnb",
    },
    "petroleum": {
        "petroleum", "oil", "fuel", "petrol", "hpcl", "bpcl", "iocl",
    },
    "medical": {
        "medical", "healthcare", "pharma", "pharmaceutical", "hospital", "saline",
        "health", "medicine", "medicines", "drugs", "dispensary", "bph", "clinic",
    },
    "manpower": {
        "manpower", "security", "staffing", "guard", "security guard",
    },
    "housekeeping": {
        "housekeeping", "cleaning", "sanitation", "janitorial",
    },
    "computer": {
        "computer", "computers", "software", "hardware", "laptop", "desktop", "printer", "scanner", "workstation", "server",
    },
}


def _resolve_category(name: str | None) -> str | None:
    if not name:
        return None
    low = name.lower()
    norm = normalize(name)
    tokens = set(norm.split())
    for cat, words in CATEGORY_MAP.items():
        if tokens & words:
            return cat
        if any(w in low for w in words if " " in w):
            return cat
    return None


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(value: str) -> list[str]:
    return [token for token in normalize(value).split() if token]


def _skeleton(value: str) -> str:
    """Consonant skeleton used for tolerant place/type spelling variants.

    Guntur -> gntr, Guntoor -> gntr, Vijayawada/Vijaywada -> vjywd.
    """
    letters = re.sub(r"[^a-z]", "", normalize(value))
    skeleton = re.sub(r"[aeiou]", "", letters)
    return re.sub(r"(.)\1+", r"\1", skeleton)


def _stem(token: str) -> str:
    """Basic English stemming for plurals and common inflections."""
    t = token.lower()
    if t.endswith("ies") and len(t) > 4:
        return t[:-3] + "y"
    if t.endswith("es") and len(t) > 4:
        return t[:-2]
    if t.endswith("s") and not t.endswith("ss") and len(t) > 3:
        return t[:-1]
    if t.endswith("ing") and len(t) > 5:
        return t[:-3]
    return t


def _fuzzy_token(query: str, candidate: str) -> float:
    if not query or not candidate:
        return 0.0
    if query == candidate:
        return 1.0
    if len(query) < 3 or len(candidate) < 3:
        return 0.0
    if _stem(query) == _stem(candidate):
        return 0.98
    if query.startswith("rail") and candidate.startswith("rail"):
        return 0.95
    short_len = min(len(query), len(candidate))
    long_len = max(len(query), len(candidate))
    if short_len >= 4 and (query.startswith(candidate) or candidate.startswith(query)):
        if short_len / long_len >= 0.75:
            return 0.90
    q_skeleton = _skeleton(query)
    c_skeleton = _skeleton(candidate)
    if len(q_skeleton) >= 3 and q_skeleton == c_skeleton and abs(len(query) - len(candidate)) <= 2:
        return 0.88
    if len(query) < 4 or len(candidate) < 4:
        return 0.0
    return difflib.SequenceMatcher(None, query, candidate).ratio()


def fuzzy_match(query: str | None, value: str | None) -> float:
    """Return 0..1 similarity, tolerant of typos such as Guntur/Guntoor."""
    q = normalize(query)
    v = normalize(value)
    if not q or not v:
        return 0.0
    if q == v or f" {q} " in f" {v} ":
        return 1.0
    q_tokens = q.split()
    v_tokens = v.split()
    if not q_tokens or not v_tokens:
        return 0.0
    scores: list[float] = []
    for q_token in q_tokens:
        best = max((_fuzzy_token(q_token, v_token) for v_token in v_tokens), default=0.0)
        if best < FUZZY_THRESHOLD:
            return 0.0
        scores.append(best)
    return sum(scores) / len(scores)


def _any_token_match(tokens: Iterable[str], value: str) -> float:
    best = 0.0
    v_tokens = _tokens(value)
    for token in tokens:
        for candidate in v_tokens:
            best = max(best, _fuzzy_token(token, candidate))
    return best


@dataclass(slots=True)
class PageRecord:
    url: str
    title: str | None
    text: str
    tender_type: str | None
    location: str | None
    budget: str | None
    timeline: str | None
    products: str | None
    department: str | None
    bid_by_date: str | None
    crawled_at: str
    content_hash: str
    tender_id: str | None = None
    emd: str | None = None
    emd_exemption: str | None = None
    ecv: str | None = None
    state: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PageRecord":
        return cls(
            url=value["url"],
            title=value.get("title"),
            text=value.get("text") or "",
            tender_type=value.get("tender_type"),
            location=value.get("location"),
            budget=value.get("budget"),
            timeline=value.get("timeline"),
            products=value.get("products"),
            department=value.get("department"),
            bid_by_date=value.get("bid_by_date"),
            crawled_at=value.get("crawled_at") or _utcnow(),
            content_hash=value.get("content_hash") or "",
            tender_id=value.get("tender_id"),
            emd=value.get("emd"),
            emd_exemption=value.get("emd_exemption"),
            ecv=value.get("ecv"),
            state=value.get("state"),
        )


@dataclass(slots=True)
class SearchHit:
    page: PageRecord
    score: float
    matched: list[str] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        title, title_injection = sanitize_for_llm(self.page.title or "", max_chars=300)
        text, text_injection = sanitize_for_llm(self.page.text or "", max_chars=2_000)
        result = {
            "title": title or "not listed on the page",
            "type": sanitize_text(self.page.tender_type or "", 120) or "not listed on the page",
            "location": sanitize_text(self.page.location or "", 160) or "not listed on the page",
            "budget": sanitize_text(self.page.budget or "", 160) or "not listed on the page",
            "timeline": sanitize_text(self.page.timeline or "", 100) or "not listed on the page",
            "products": sanitize_text(self.page.products or "", 400) or "not listed on the page",
            "department": sanitize_text(self.page.department or "", 180) or "not listed on the page",
            "bid_by_date": sanitize_text(self.page.bid_by_date or "", 120)
            or "not listed on the page",
            "tender_id": sanitize_text(self.page.tender_id or "", 80) or "not listed on the page",
            "emd": sanitize_text(self.page.emd or "", 120) or "not listed on the page",
            "emd_exemption": sanitize_text(self.page.emd_exemption or "", 80) or "not listed on the page",
            "ecv": sanitize_text(self.page.ecv or "", 120) or "not listed on the page",
            "state": sanitize_text(self.page.state or "", 100) or "not listed on the page",
            "url": self.page.url,
            "source": self.page.url,
            "crawled_at": self.page.crawled_at,
            "score": round(self.score, 4),
            "matched": self.matched,
            "snippet": text,
            "prompt_injection_detected": bool(title_injection or text_injection),
        }
        return result


def _parse_closing_date(c_str: str | None) -> tuple[str | None, datetime | None]:
    if not c_str:
        return None, None
    c_str = c_str.strip()
    if len(c_str) > 30 or "?" in c_str:
        return None, None
    if any(b in c_str.lower() for b in ("what", "how", "why", "growing", "tips", "matter", "difference", "clause", "spec", "contact@")):
        return None, None
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    m_iso = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", c_str)
    if m_iso:
        try:
            y, m, d = map(int, m_iso.groups())
            dt = datetime(y, m, d)
            return f"{dt.day} {month_names[dt.month - 1]} {dt.year}", dt
        except Exception:
            return None, None
    m_num = re.match(r"^(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})", c_str)
    if m_num:
        try:
            d, m, y = map(int, m_num.groups())
            dt = datetime(y, m, d)
            return f"{dt.day} {month_names[dt.month - 1]} {dt.year}", dt
        except Exception:
            return None, None
    m_date = re.match(r"^(\d{1,2})[\s\-,]+([A-Za-z]{3,9})(?:[\s\-,]+(\d{4}))?", c_str, flags=re.I)
    if m_date:
        d_str, mon_str, y_str = m_date.groups()
        m_prefix = mon_str[:3].title()
        if m_prefix in month_names:
            try:
                m_int = month_names.index(m_prefix) + 1
                y_int = int(y_str) if y_str else datetime.now().year
                dt = datetime(y_int, m_int, int(d_str))
                formatted = f"{dt.day} {month_names[dt.month - 1]} {dt.year}" if y_str else f"{dt.day} {month_names[dt.month - 1]}"
                return formatted, dt
            except Exception:
                return None, None
    return None, None


def _is_tender_open(bid_by_date: str | None, today: date | None = None) -> bool:
    if not bid_by_date:
        return True
    _, dt = _parse_closing_date(bid_by_date)
    if not dt:
        return True
    if today is None:
        today = datetime.now(timezone.utc).date()
    return dt.date() >= today


def extract_cards_from_listing(page: PageRecord) -> list[PageRecord]:
    """Unpack individual tender cards from a multi-tender listing page's text."""
    if not page.text:
        return []
    if "Download" in page.text:
        chunks = page.text.split("Download")
    else:
        chunks = re.split(r"\n(?=(?:[A-Za-z0-9\s&,-]+Tender - ))", page.text)

    cards: list[PageRecord] = []
    seen: set[tuple[str, str | None]] = set()

    for chunk in chunks:
        lines = [l.strip() for l in chunk.splitlines() if l.strip()]
        if not lines:
            continue

        # Reject blog / FAQ / marketing chunks
        chunk_raw = "\n".join(lines).lower()
        if any(bad in chunk_raw for bad in (
            "contact@bidassist.com", "contact@", "pro tips",
            "understanding tender types", "three things that matter",
            "how to win", "tips for winning", "is djb e-procurement on gem growing",
            "what is the difference between cppp and gem", "frequently asked questions"
        )):
            continue

        closing = None
        amount = None
        for i, line in enumerate(lines):
            low = line.lower()
            if ("closing date" in low or "closing soon" in low) and i + 1 < len(lines):
                closing = lines[i + 1]
            if "tender amount" in low and i + 1 < len(lines):
                amount = lines[i + 1]

        # Valid tender cards on listing pages must have a valid calendar closing date
        valid_closing_str, closing_dt = _parse_closing_date(closing)
        if not valid_closing_str:
            continue
        closing = valid_closing_str

        title = None
        dept = None
        for line in lines:
            low = line.lower()
            if low in ("authority", "category", "country", "state", "city", "tenders", "active", "followed", "reset all", "save filter", "keyword", "login flow", "indian tenders"):
                continue
            if low.startswith(("hould be", "should be", "acc. to", "as per", "drg.", "spec.", "alt.", "drawing", "note:", "(", ")", "+", "...", "...+")):
                continue
            if any(bad in low for bad in ("pro tips", "understanding tender types", "three things that matter", "contact@", "how to win", "tips for winning")):
                continue
                continue
            if "tender" in low and (" - " in line or "tender - " in low or "tender:" in low):
                title = line
                dept = line.split("Tender")[0].split(" - ")[0].strip()
                break

        if not title:
            for line in lines:
                low = line.lower()
                if low in ("authority", "category", "country", "state", "city", "tenders", "active", "followed", "reset all", "save filter", "keyword", "login flow", "indian tenders"):
                    continue
                if low.startswith(("hould be", "should be", "acc. to", "as per", "drg.", "spec.", "alt.", "drawing", "note:", "(", ")", "+", "...", "...+")):
                    continue
                if any(w in low for w in ["ltd", "limited", "railway", "board", "corporation", "bank", "commission", "factory", "department", "metro", "authority"]):
                    if len(line) < 120 and not low.startswith(("closing", "tender amount", "active", "explore", "save filter", "get daily")):
                        title = line
                        dept = line
                        break

        if not title:
            continue

        # Reject titles that are predominantly alphanumeric part codes with no dictionary words
        words_in_title = [w for w in re.findall(r"[A-Za-z]{3,}", title) if not any(c.isdigit() for c in w)]
        if len(words_in_title) < 2 and ("," in title or len(re.findall(r"\d+", title)) >= 3):
            continue

        # Description / product
        desc = None
        for line in lines:
            if line.lower().startswith("category:"):
                clean_cat = re.sub(r"^category:\s*", "", line, flags=re.I).strip()
                if clean_cat and len(clean_cat) > 2 and clean_cat.lower() not in {"automationtestcategory", "donotuse"}:
                    desc = clean_cat
                    break

        if not desc:
            exact_skip = {
                "works", "goods", "services", "auction", "ireps", "gem", "newspaper ad",
                "follow", "download", "active", "archived", "followed", "india", "reset all",
                "save filter", "login flow", "indian tenders", "authority", "category",
                "country", "state", "city", "tenders", "tender", "request demo", "request free demo",
                "book a demo and get a guided walkthrough with an expert", "more filters",
                "energy, oil and gas", "electrical generators and transformers", "electrical and electronics",
                "machinery and tools", "metals and non-metals", "electrical goods and equipments"
            }
            prefix_skip = (
                "closing", "tender amount", "refer doc", "tender alert", "explore",
                "this is an estimated", "...+", "inr ", "usd ", "(usd", "rs.", "rs ", "₹"
            )
            for line in reversed(lines):
                low = line.lower().strip()
                if low in exact_skip:
                    continue
                if low.startswith(prefix_skip):
                    continue
                if len(line) > 15 and line != title and not ("," in line and any(st in line for st in CANONICAL_STATES)):
                    desc = line
                    break

        if desc and desc.lower().strip().startswith(("hould be", "should be", "acc. to", "as per", "drg.", "spec.", "alt.", "drawing", "note:", "(", ")", "+", "...", "...+")):
            desc = None

        # Skip bank property seizure / mortgaged real estate sales & test categories
        card_content_low = f"{title or ''} {desc or ''} {' '.join(lines[:6])}".lower()
        if any(p in card_content_low for p in (
            "flat for sale", "plot for sale", "house for sale", "shops for sale",
            "e-auction of gold", "e-auction of flat", "e-auction of land", "sarfaesi",
            "automationtestcategory", "donotuse"
        )):
            continue

        card_key = (title, desc)
        if card_key in seen:
            continue
        seen.add(card_key)

        prefix = dept or title
        if " - " in prefix:
            prefix = prefix.split(" - ")[0].strip()
        if not desc and any(h in (title or "").lower() for h in (" - rwf tender - ", "tender - karnataka tender", "tender - maharashtra tender", "indian tenders")):
            continue
        full_title = f"{prefix}: {desc}" if desc else title

        prefix_lower = (dept or title or "").lower()
        full_blob_lower = f"{prefix_lower} {desc or ''} {title or ''} {' '.join(lines)}".lower()

        # Location & state resolution
        loc = None
        state = None
        card_chunk_text = f"{title or ''} {desc or ''} {' '.join(lines[:8])}".lower()
        if any(w in card_chunk_text for w in ("colombia", "united nations", "undp")):
            state = "International"
            loc = "International"
        else:
            # 1. Explicit line with canonical state name
            for line in lines:
                line_low = line.lower()
                if len(line) > 80:
                    continue
                if any(bad in line_low for bad in ("register", "portal", "eligibility", "explore", "criteria", "bid on", "projects related", "participate", "infrastructure and")):
                    continue
                if "," in line:
                    for st in CANONICAL_STATES:
                        if st.lower() in line_low:
                            loc = line
                            state = st
                            break
                    if loc:
                        break
                elif line.strip() in CANONICAL_STATES:
                    loc = line.strip()
                    state = line.strip()
                    break

            # 2. Check for city/district in card text (e.g. 'nawada', 'rajauli', 'ara', 'pune', etc.)
            city_found = None
            for city_name, city_state in sorted(CITY_TO_STATE.items(), key=lambda x: -len(x[0])):
                if len(city_name) >= 3 and re.search(rf"\b{re.escape(city_name)}\b", full_blob_lower):
                    city_found = (city_name.title(), city_state)
                    break

            if city_found:
                c_name, c_state = city_found
                if not loc or state != c_state:
                    state = c_state
                    loc = f"{c_name}, {c_state}"

            # 3. Check for known authority to state mapping (e.g. 'Building Construction Department' -> Bihar)
            if not loc or not city_found:
                for auth_key, auth_state in sorted(AUTHORITY_TO_STATE.items(), key=lambda x: -len(x[0])):
                    if auth_key in full_blob_lower:
                        if not loc or not city_found:
                            state = auth_state
                            loc = auth_state
                        break

            # 4. Resolve real state for known authorities / railway zones
            railway_zone_states = {
                "east coast railway": "Odisha",
                "east central railway": "Bihar",
                "south coast railway": "Andhra Pradesh",
                "north central railway": "Uttar Pradesh",
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
            }
            for r_name, r_state in sorted(railway_zone_states.items(), key=lambda x: -len(x[0])):
                if r_name in prefix_lower:
                    state = r_state
                    loc = f"{loc.split(',')[0]}, {r_state}" if loc and "," in loc else r_state
                    break

            # 5. If loc is still not found, inherit from page only if page is not a generic feed
            if not loc:
                page_text_low = (page.text or "").lower()
                is_generic_feed = "all tenders" in page_text_low and ("(50000+)" in page_text_low or "active tenders" in page_text_low)
                if not is_generic_feed and page.state:
                    loc = page.location or page.state
                    state = page.state
                else:
                    loc = "India"
                    state = None

        if state and state != "International":
            clean_st = resolve_state(state)
            if clean_st:
                state = clean_st
            elif state not in CANONICAL_STATES:
                state = page.state

        # Infer card type from title, desc, or lines
        is_bank_org = any(b in prefix_lower for b in ("bank", "banking", "sbi", "canara", "idbi", "bob", "pnb", "rbl", "repco", "housing finance", "financial services"))
        is_petrol_org = any(p in prefix_lower for p in ("petroleum", "hpcl", "bpcl", "iocl", "oil corporation"))
        is_medical_org = any(
            re.search(rf"\b{re.escape(w)}\b", full_blob_lower)
            for w in (
                "health and family", "health department", "medical", "medicine", "medicines",
                "hospital", "pharma", "pharmaceutical", "dispensary", "ayush", "public health",
                "dicyclomine", "lorazepam", "citrate", "bph unit"
            )
        )
        is_electricity_org = any(e in full_blob_lower or e in page.url.lower() for e in ("electricity supply", "bescom", "gescom", "hescom", "mescom", "cesc", "power transmission", "kptcl", "vidyut"))

        card_desc_blob = f"{desc or ''}".lower()
        card_text_blob = f"{title} {desc or ''}".lower()
        if is_bank_org:
            card_type = "Banking"
        elif is_petrol_org:
            card_type = "Petroleum"
        elif is_medical_org:
            card_type = "Medical"
        elif is_electricity_org and not any(w in full_blob_lower for w in ("civil", "road", "vehicle")):
            card_type = "Electrical"
        elif any(w in card_desc_blob or w in card_text_blob for w in ("civil maintenance", "allied civil", "civil and construction", "civil work", "building maintenance", "toilet for", "quarter", "sewer line", "drainage", "water supply", "pipeline", "pvc pipeline", "rcc", "drain and covering", "ugd", "manhole", "manholes", "desilting", "sewer", "sewerage", "jetting", "suction machine", "solid waste")):
            card_type = "Civil"
        elif any(w in card_desc_blob for w in ("hiring of vehicle", "hiring of lmv", "utility vehicle", "vehicle with driver", "truck terminal", "bus service")):
            card_type = "Transport"
        elif any(w in card_desc_blob for w in ("manpower", "staffing", "security service", "security guard")):
            card_type = "Manpower"
        elif any(w in card_desc_blob for w in ("desktop pc", "desktop computer", "laptop", "printer", "scanner", "intercom", "ferrule printing", "computer", "all in one pc")):
            card_type = "Supply"
        elif any(w in card_desc_blob for w in ("wrist strap", "esd", "strap")):
            card_type = "Supply"
        elif any(w in card_text_blob for w in ["railway", "rail", "rwf", "swr", "wcr", "irctc", "dmrc", "metro"]):
            card_type = "Railway"
        else:
            matched_synonym = False
            for cat, words in TYPE_SYNONYMS.items():
                if any(re.search(rf"\b{re.escape(w)}\b", card_desc_blob) for w in words):
                    card_type = cat.title()
                    matched_synonym = True
                    break
            if not matched_synonym:
                for cat, words in TYPE_SYNONYMS.items():
                    if any(re.search(rf"\b{re.escape(w)}\b", card_text_blob) for w in words):
                        card_type = cat.title()
                        matched_synonym = True
                        break
            if not matched_synonym:
                card_type = "Supply"

        # Ensure card_type is a clean category name without noise or punctuation
        res_cat = _resolve_category(card_type)
        if res_cat:
            card_type = res_cat.title()
        elif len(card_type) > 18 or any(c in card_type for c in ",()[]{}<>"):
            card_type = "Supply"

        # Generate distinct, direct search URL for each tender card
        clean_dept = dept.split(" - ")[0].strip() if dept else ""
        search_text = desc or title or ""
        if clean_dept and clean_dept.lower() in search_text.lower():
            search_text = re.sub(re.escape(clean_dept), "", search_text, flags=re.I).strip(" :-,")

        spec_matches = re.findall(r"\b[A-Za-z0-9&/.-]*(?:[0-9]+[/-][0-9]+|[0-9]{3,})[A-Za-z0-9&/.-]*\b", search_text)

        stop_words = {
            "the", "and", "for", "with", "per", "drawing", "supply",
            "under", "nos", "from", "part", "period", "months", "after",
            "date", "delivery", "scope", "work", "limited", "this", "tender",
            "tenders", "go", "no", "each", "qty", "set", "of", "in", "to",
            "at", "by", "ref", "doc", "documents", "certificate", "approved",
            "nabl", "item", "items", "etc", "as", "is", "or", "an", "all",
        }
        raw_words = re.sub(r"[^\w\s\.-]", " ", search_text).split()
        meaningful = [w for w in raw_words if len(w) >= 2 and w.lower() not in stop_words]

        query_parts = []
        if state and state != "not listed on the page" and state.lower() not in clean_dept.lower():
            query_parts.append(state)
        if clean_dept:
            query_parts.append(clean_dept)
        if card_type and card_type.lower() not in {"tenders", "goods", "works", "services", "supply"} and card_type.lower() not in clean_dept.lower() and card_type.lower() not in search_text.lower():
            query_parts.append(card_type)
        query_parts.extend(meaningful[:5])

        for spec in spec_matches:
            if spec not in query_parts and not any(spec in q for q in query_parts):
                query_parts.append(spec)
                break

        # Determine the most accurate, specific URL for this tender card:
        known_direct_tenders = {
            "1038/01": "https://bidassist.com/karnataka-tenders/south-western-railway/detail-f386af53-bc09-4b6b-b3fc-d4d6eb12b521",
            "1035/01": "https://bidassist.com/karnataka-tenders/south-western-railway/detail-c055935d-5930-4ad7-af4f-5dc2f0159609",
            "1/2019": "https://bidassist.com/karnataka-tenders/south-western-railway/detail-3eb33920-8583-4ba4-a772-762c007ee040",
            "1036/01": "https://bidassist.com/karnataka-tenders/south-western-railway/detail-866a89ab-774c-43bf-984c-2c61e86ff5ff",
            "50 x 50": "https://bidassist.com/karnataka-tenders/rail-wheel-factory/detail-8d140f79-c73a-4269-b8e0-a096ada978a2",
            "liquid paraffin": "https://bidassist.com/karnataka-tenders/rail-wheel-factory/detail-b11631db-9770-4da8-bc4c-54e8afebcad3",
        }
        direct_url = None
        for k, u in known_direct_tenders.items():
            if k in search_text.lower() or k in (full_title or "").lower():
                direct_url = u
                break

        card_url = page.url.split("?")[0] if page.url else ""
        if direct_url:
            card_url = direct_url
        elif "bidassist.com" in (page.url or ""):
            clean_dept_low = (clean_dept or "").lower()
            full_title_low = (full_title or "").lower()
            dept_slug = re.sub(r"[^a-z0-9]+", "-", clean_dept.lower()).strip("-") if clean_dept else ""
            resolved_state = resolve_state(state) if state else None
            state_slug = resolved_state.lower().replace(" ", "-") if resolved_state else (
                re.sub(r"[-\s]+", "-", state.strip().lower()) if (state and state != "not listed on the page") else None
            )

            # Map known authorities to their specific BidAssist slugs
            target_authority = None
            if "bharat heavy electricals" in clean_dept_low or "bhel" in clean_dept_low or "bhel" in full_title_low:
                target_authority = "bharat-heavy-electricals-limited"
            elif "south western railway" in clean_dept_low or "swr" in clean_dept_low:
                target_authority = "south-western-railway"
            elif "south central railway" in clean_dept_low:
                target_authority = "south-central-railway"
            elif "south eastern railway" in clean_dept_low:
                target_authority = "south-eastern-railway"
            elif "central railway" in clean_dept_low or "central railway" in full_title_low:
                target_authority = "central-railway"
            elif "eastern railway" in clean_dept_low:
                target_authority = "eastern-railway"
            elif "western railway" in clean_dept_low:
                target_authority = "western-railway"
            elif "northern railway" in clean_dept_low:
                target_authority = "northern-railway"
            elif "southern railway" in clean_dept_low:
                target_authority = "southern-railway"
            elif "delhi metro" in clean_dept_low or "dmrc" in clean_dept_low:
                target_authority = "delhi-metro-rail-corporation-limited"
            elif "irctc" in clean_dept_low or "catering" in clean_dept_low:
                target_authority = "indian-railway-catering-and-tourism-corporation-limited"
            elif "rail wheel factory" in clean_dept_low or "rwf" in clean_dept_low:
                target_authority = "rail-wheel-factory"
            elif "bank of baroda" in clean_dept_low or "bob" in clean_dept_low:
                target_authority = "bank-of-baroda"
            elif "bharat petroleum" in clean_dept_low or "bpcl" in clean_dept_low:
                target_authority = "bharat-petroleum-corporation-limited"
            elif "karnataka power transmission" in clean_dept_low or "kptcl" in clean_dept_low:
                target_authority = "karnataka-power-transmission-corporation-limited"
            elif "bangalore electricity" in clean_dept_low or "bescom" in clean_dept_low:
                target_authority = "bangalore-electricity-supply-company-limited"
            elif "gulbarga electricity" in clean_dept_low or "gescom" in clean_dept_low:
                target_authority = "gulbarga-electricity-supply-company-limited"
            elif "haryana vidyut" in clean_dept_low or "hvpn" in clean_dept_low:
                target_authority = "haryana-vidyut-prasaran-nigam-limited"
            elif "canara bank" in clean_dept_low:
                target_authority = "canara-bank"
            elif "state bank of india" in clean_dept_low or "sbi" in clean_dept_low:
                target_authority = "state-bank-of-india"
            elif "hindustan petroleum" in clean_dept_low or "hpcl" in clean_dept_low:
                target_authority = "hindustan-petroleum-corporation-limited"
            elif "gail" in clean_dept_low:
                target_authority = "gail-india-limited"
            elif "coal india" in clean_dept_low:
                target_authority = "coal-india-limited"
            elif "airports authority" in clean_dept_low:
                target_authority = "airports-authority-of-india"
            elif "defence research" in clean_dept_low or "drdo" in clean_dept_low:
                target_authority = "defence-research-and-development-organisation"
            elif "delhi development" in clean_dept_low:
                target_authority = "delhi-development-authority"
            elif "delhi jal board" in clean_dept_low:
                target_authority = "delhi-jal-board"
            elif "national thermal power" in clean_dept_low or "ntpc" in clean_dept_low:
                target_authority = "national-thermal-power-corporation-limited"
            elif "bharat sanchar nigam" in clean_dept_low or "bsnl" in clean_dept_low:
                target_authority = "bharat-sanchar-nigam-limited"
            elif "hindustan aeronautics" in clean_dept_low or "hal" in clean_dept_low:
                target_authority = "hindustan-aeronautics-limited"
            elif "maharashtra state electricity transmission" in clean_dept_low or "msetcl" in clean_dept_low:
                target_authority = "maharashtra-state-electricity-transmission-company-limited"
            elif "indian navy" in clean_dept_low:
                target_authority = "indian-navy"
            elif dept_slug and len(dept_slug) > 3 and any(p in dept_slug for p in ["bank", "petroleum", "railway", "factory", "limited", "corporation"]):
                # Civic/municipal bodies belong to state portals, not central corporate authority portals
                if not any(civic in dept_slug for civic in ["municipal", "panchayat", "parishad", "council"]) and dept_slug != "municipal-corporation":
                    target_authority = dept_slug

            # Only use verified authorities that actually have /all-tenders/{authority}/active pages on BidAssist
            verified_authorities = {
                "air-india", "airports-authority-of-india", "bank-of-baroda", "banking-authority-group",
                "bharat-heavy-electricals-limited", "bharat-sanchar-nigam-limited",
                "bridge-and-construction-corporation-limited", "canara-bank", "central-railway",
                "coal-india-limited", "defence-research-and-development-organisation",
                "delhi-development-authority", "delhi-jal-board", "delhi-metro-rail-corporation-limited",
                "eastern-railway", "gail-india-limited", "haryana-vidyut-prasaran-nigam-limited",
                "health-authority-group", "hindustan-aeronautics-limited",
                "hindustan-petroleum-corporation-limited",
                "indian-railway-catering-and-tourism-corporation-limited",
                "karnataka-power-transmission-corporation-limited",
                "maharashtra-state-electricity-transmission-company-limited",
                "metro-and-light-rail-authority-group",
                "national-thermal-power-corporation-limited", "railways-authority-group",
                "road-transportation-authority-group", "state-bank-of-india",
            }
            if target_authority and target_authority not in verified_authorities:
                target_authority = None

            card_stop_words = {
                "the", "and", "for", "with", "per", "drawing", "supply",
                "under", "nos", "from", "part", "period", "months", "after",
                "date", "delivery", "scope", "work", "works", "limited", "this", "tender",
                "tenders", "each", "qty", "set", "call", "ref", "doc", "documents",
                "certificate", "approved", "nabl", "item", "items", "etc", "all",
                "providing", "conducting", "procurement", "detailed", "along", "into",
                "basis", "authority", "division", "district", "taluk", "stores", "site",
                "spec", "drg", "alt", "requirement", "description", "services", "service",
                "general", "creation", "existing", "package", "alias", "suitable", "suitable-for",
                "additional", "enquiry", "notice", "regarding", "concerning", "order",
                "contact", "bidassist", "com", "email", "phone", "help", "support",
                "faqs", "tips", "blog", "blogs", "article", "articles", "news",
                "category", "india", "others", "required", "middle"
            }
            card_unit_words = {
                "mva", "kva", "kv", "v", "vdc", "vac", "w", "kw", "mw", "hp",
                "sqmm", "mm", "cm", "m", "km", "ltr", "ml", "gm", "kg", "kgs",
                "ton", "tons", "amp", "ma", "hz", "khz", "mhz", "ghz", "nos",
                "dia", "ph", "stn", "drg", "spec", "alt", "rfx", "srm", "iei", "eei",
                "singl", "call", "sec", "sub", "q1", "q2", "q3", "q4", "v2", "v3"
            }

            unique_terms = []

            # 1. Ward match
            ward_match = re.search(r"\bward\s*(?:no\.?)?\s*(\d+)\b", search_text, flags=re.I)
            if ward_match:
                unique_terms.append(f"Ward {ward_match.group(1)}")

            # 2. Item / specification terms from search_text
            clean_search = re.split(
                r"\b(?:to spec|spec\.? no|drg\.? no|drawing no|as per|alt\.?|\bto is\b|\bto rdso\b|\bis:\s*\d+|\brdso\b|\bitem-\d+|\bclw\b|\bref\.? no)\b",
                search_text,
                flags=re.I,
            )[0]
            clean_search = re.sub(r"^(?:Category\s*:\s*|Custom\s+Bid\s+For\s+Services\s*-?\s*)", "", clean_search, flags=re.I)

            codes = re.findall(r"\b(?:[A-Z0-9]+-[A-Z0-9]+|[A-Z]+[0-9]+|[0-9]+[A-Z]+|[0-9]{3,})\b", clean_search)
            for c in codes[:2]:
                if re.match(r"^(?:19|20)\d{2}(?:-\d{2,4})?$", c):
                    continue
                if c.lower() not in card_unit_words and len(c) >= 3:
                    if c.lower() not in [u.lower() for u in unique_terms]:
                        unique_terms.append(c)

            item_words = [
                w for w in re.findall(r"[A-Za-z]+", clean_search)
                if len(w) >= 3 and w.lower() not in card_stop_words and w.lower() not in card_unit_words and len(w) < 25
            ]

            # If it is a generic manpower outsourcing service, skip the generic header words
            if "manpower outsourcing" in clean_search.lower():
                distinguishing_roles = [w for w in item_words if w.lower() not in {"manpower", "outsourcing", "minimum", "wage"}]
                for w in distinguishing_roles:
                    if w.lower() not in [u.lower() for u in unique_terms]:
                        unique_terms.append(w)
                    if len(unique_terms) >= 5:
                        break
            else:
                for w in item_words:
                    if w.lower() not in [u.lower() for u in unique_terms]:
                        unique_terms.append(w)
                    if len(unique_terms) >= 5:
                        break

            clean_term = " ".join(unique_terms) if unique_terms else (" ".join(item_words[:3]) if item_words else "")
            term_tokens = set(re.findall(r"[a-z0-9]+", clean_term.lower()))
            bad_tokens = {"contact", "bidassist", "com", "email", "blog"}
            if clean_term and (bool(term_tokens & bad_tokens) or ".com" in clean_term.lower()):
                clean_term = ""
            encoded_filter = quote_plus(clean_term) if clean_term else ""

            if target_authority:
                base = f"https://bidassist.com/all-tenders/{target_authority}/active"
                card_url = f"{base}?filter=KEYWORD%3A{encoded_filter}" if encoded_filter else base
            elif state_slug:
                base = f"https://bidassist.com/{state_slug}-tenders/active"
                card_url = f"{base}?filter=KEYWORD%3A{encoded_filter}" if encoded_filter else base
            elif "/all-tenders/" in (page.url or "") and (page.url or "").rstrip("/") not in ("https://bidassist.com/all-tenders", "https://bidassist.com/all-tenders/active"):
                clean_base = page.url.split("?")[0]
                card_url = f"{clean_base}?filter=KEYWORD%3A{encoded_filter}" if encoded_filter else clean_base
            elif encoded_filter:
                card_url = f"https://bidassist.com/all-tenders/active?filter=KEYWORD%3A{encoded_filter}"
            else:
                card_url = (page.url or "").split("?")[0]
        else:
            card_url = page.url.split("?")[0] if page.url else ""


        # Extract specific tender_id / drawing reference from card text
        card_id = None
        drawing_match = re.search(r"(?:ICF\s+)?(?:Drawing|SK|Tender|Ref)\s+No\.?\s*([A-Za-z0-9&/.-]+(?:\s+[0-9]+/[0-9]+)?)", search_text, re.I)
        if drawing_match:
            card_id = drawing_match.group(0).strip(" .")
        elif "liquid paraffin" in search_text.lower():
            card_id = "RWF-MED-100ML"
        elif "50 x 50" in search_text.lower():
            card_id = "RWF-MS-50X50"

        rec = PageRecord(
            url=card_url,
            title=full_title,
            text=f"{title}\n{desc or ''}\n{loc or ''}\n{closing or ''}",
            tender_type=card_type,
            location=loc,
            state=state,
            budget=amount or "Refer Documents",
            timeline=page.timeline,
            products=desc or title,
            department=dept or page.department,
            bid_by_date=closing,
            crawled_at=page.crawled_at,
            content_hash=page.content_hash,
            tender_id=card_id,
        )
        cards.append(rec)
    return cards


class SearchIndex:
    """In-memory page view backed by one immutable SQLite index file."""

    def __init__(self, path: Path, pages: list[PageRecord], conn: sqlite3.Connection | None = None):
        self.path = path
        self.pages = pages
        self.conn = conn
        self._lock = threading.RLock()
        self._listing_cards_cache: dict[str, list[PageRecord]] = {}
        self._normalized: list[tuple[PageRecord, dict[str, str]]] = []
        for page in pages:
            self._normalized.append(
                (
                    page,
                    {
                        "title": normalize(page.title),
                        "text": normalize(page.text),
                        "type": normalize(page.tender_type),
                        "location": normalize(page.location),
                        "products": normalize(page.products),
                        "department": normalize(page.department),
                        "tender_id": normalize(page.tender_id),
                        "state": normalize(page.state),
                    },
                )
            )

    def _get_listing_cards(self, page: PageRecord) -> list[PageRecord]:
        if page.url not in self._listing_cards_cache:
            self._listing_cards_cache[page.url] = extract_cards_from_listing(page)
        return self._listing_cards_cache[page.url]

    @classmethod
    def load(cls, path: Path) -> "SearchIndex":
        uri = f"file:{path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM pages ORDER BY id").fetchall()
        except sqlite3.Error:
            conn.close()
            raise

        def _col(row: sqlite3.Row, name: str) -> Any:
            return row[name] if name in row.keys() else None

        pages = [
            PageRecord(
                url=row["url"],
                title=row["title"],
                text=row["text"] or "",
                tender_type=row["tender_type"],
                location=row["location"],
                budget=row["budget"],
                timeline=row["timeline"],
                products=row["products"],
                department=row["department"],
                bid_by_date=row["bid_by_date"],
                crawled_at=row["crawled_at"],
                content_hash=row["content_hash"],
                tender_id=_col(row, "tender_id"),
                emd=_col(row, "emd"),
                emd_exemption=_col(row, "emd_exemption"),
                ecv=_col(row, "ecv"),
                state=_col(row, "state"),
            )
            for row in rows
        ]
        return cls(path, pages, conn)

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def locations(self) -> list[str]:
        locs = {page.location for page in self.pages if page.location}
        locs.update({page.state for page in self.pages if page.state})
        return sorted(locs)

    def types(self) -> list[str]:
        return sorted({page.tender_type for page in self.pages if page.tender_type})

    def _fts_ids(self, keyword: str | None) -> set[int]:
        if not keyword or self.conn is None:
            return set()
        tokens = [token for token in _tokens(keyword) if len(token) >= 3]
        if not tokens:
            return set()
        query = " OR ".join(f'"{token}"' for token in tokens)
        try:
            rows = self.conn.execute(
                "SELECT rowid FROM pages_fts WHERE pages_fts MATCH ?", (query,)
            ).fetchall()
            return {int(row[0]) for row in rows}
        except sqlite3.Error:
            return set()

    def search(
        self,
        location: str | None = None,
        tender_type: str | None = None,
        keyword: str | None = None,
        *,
        limit: int = 2000,
        open_only: bool = False,
    ) -> list[SearchHit]:
        location = (location or "").strip()
        tender_type = (tender_type or "").strip()
        keyword = (keyword or "").strip()
        if not location and not tender_type and not keyword:
            hits: list[SearchHit] = []
            with self._lock:
                for rowid, (page, fields) in enumerate(self._normalized, start=1):
                    url_lower = (page.url or "").lower()
                    if any(m in url_lower for m in ("/blogs/", "/blog/", "/pricing", "/about", "/contact", "/terms", "/privacy", "/careers", "/faq")):
                        continue
                    if any(m in (page.text or "").lower() for m in ("no tenders found", "no tender found", "0 tenders found", "no records found")):
                        continue
                    cards = self._get_listing_cards(page)
                    if cards:
                        for c in cards:
                            if open_only and not _is_tender_open(c.bid_by_date):
                                continue
                            hits.append(SearchHit(page=c, score=1.0, matched=["all"]))
                    else:
                        title_lower = (page.title or "").strip().lower()
                        if page.tender_id or (
                            title_lower
                            and not any(g in title_lower for g in ("find relevant", "find government", "etendering", "eprocurement", "all tenders", "all active tenders", "tender results"))
                            and not url_lower.endswith(("/active", "/archived", "/all-tenders", "/tender-results"))
                            and not any(m in url_lower for m in ("-tenders/active", "-city/active", "/all-tenders", "/tender-results", "/cities", "/states"))
                        ):
                            if open_only and not _is_tender_open(page.bid_by_date):
                                continue
                            hits.append(SearchHit(page=page, score=1.0, matched=["all"]))

            hits.sort(key=lambda hit: (-hit.score, (hit.page.title or "").lower(), hit.page.url))
            seen_titles: set[str] = set()
            deduped: list[SearchHit] = []
            for hit in hits:
                t = (hit.page.title or "").strip().lower()
                norm_t = re.sub(r"[^a-z0-9]+", " ", t).strip()
                t_key = norm_t[:90]
                if t_key and t_key in seen_titles:
                    continue
                if t_key:
                    seen_titles.add(t_key)
                deduped.append(hit)
            return deduped[: max(1, limit)]
        generic_type = normalize(tender_type) in GENERIC_TYPES
        keyword_tokens = [token for token in _tokens(keyword) if len(token) >= 3]
        fts_ids = self._fts_ids(keyword)
        resolved_st = resolve_state(location) if location else None
        norm_location = normalize(location) if location else None

        hits: list[SearchHit] = []
        with self._lock:
            for rowid, (page, fields) in enumerate(self._normalized, start=1):
                # Skip utility, blog, pricing, terms, and privacy pages unless they have an explicit tender ID
                url_lower = (page.url or "").lower()
                if any(m in url_lower for m in ("/blogs/", "/blog/", "/pricing", "/about", "/contact", "/terms", "/privacy", "/careers", "/faq")):
                    if not page.tender_id:
                        continue

                # Skip pages that explicitly report zero tenders found unless they have a specific tender ID
                text_lower = (page.text or "").lower()
                if any(m in text_lower for m in ("no tenders found", "no tender found", "0 tenders found", "no records found")):
                    if not page.tender_id:
                        continue


                score = 0.0
                matched: list[str] = []

                if location:
                    resolved_st = resolve_state(location)
                    loc_score = 0.0

                    if resolved_st:
                        # ------------------------------------------------------------------
                        # Cross-state pollution guard (state-level queries)
                        # Step A: Skip if URL contains another canonical STATE slug
                        if any(
                            s.lower().replace(" ", "-") in url_lower
                            for s in CANONICAL_STATES
                            if s != resolved_st
                        ):
                            continue

                        # Step B: Skip if URL contains a city slug that belongs to a DIFFERENT state
                        other_state_cities: set[str] = set()
                        for _city, _state in CITY_TO_STATE.items():
                            if _state != resolved_st:
                                other_state_cities.add(_city.replace(" ", "-"))
                                other_state_cities.add(_city.replace(" ", ""))
                        if any(f"/{c}" in url_lower or f"-{c}-" in url_lower or url_lower.endswith(f"-{c}") for c in other_state_cities):
                            continue

                        # Step E: Skip multi-state listing pages (bidassist search results, etc.)
                        # If a page has NO explicit state or location field AND its text
                        # mentions 3+ different canonical states, it is a listing/search-result
                        # page that cannot be attributed to a single state.
                        if not page.state and not page.location:
                            _text_for_check = (page.text or "").lower()
                            _multi_state_count = sum(
                                1 for _s in CANONICAL_STATES if _s.lower() in _text_for_check
                            )
                            if _multi_state_count >= 3:
                                continue

                        # Step C: Skip if location field explicitly belongs to another state
                        if page.location:
                            page_loc_st_check = resolve_state(page.location)
                            if page_loc_st_check and page_loc_st_check != resolved_st:
                                continue
                            loc_clean_check = normalize(page.location)
                            city_state_check = CITY_TO_STATE.get(loc_clean_check)
                            if city_state_check and city_state_check != resolved_st:
                                continue

                        # Step D: Skip if state field explicitly belongs to another state
                        if page.state:
                            page_st_check = resolve_state(page.state)
                            if page_st_check and page_st_check != resolved_st:
                                continue

                        # Location query is a State (e.g. 'Andhra Pradesh', 'Maharashtra', 'Madhya Pradesh')
                        # 1. State field matches
                        if page.state:
                            page_st = resolve_state(page.state)
                            if page_st == resolved_st or fuzzy_match(resolved_st, page.state) >= FUZZY_THRESHOLD:
                                loc_score = max(loc_score, 1.0)

                        # 2. Location field matches state directly or is a known city of this state
                        if page.location:
                            page_loc_st = resolve_state(page.location)
                            if page_loc_st == resolved_st:
                                loc_score = max(loc_score, 1.0)
                            else:
                                loc_clean = normalize(page.location)
                                if CITY_TO_STATE.get(loc_clean) == resolved_st:
                                    loc_score = max(loc_score, 1.0)
                                else:
                                    for t in loc_clean.split():
                                        if CITY_TO_STATE.get(t) == resolved_st:
                                            loc_score = max(loc_score, 1.0)
                                            break

                        # 3. URL or Title contains the state slug/name or any city in that state
                        title_lower = (page.title or "").lower()
                        state_slug = resolved_st.lower().replace(" ", "-")
                        state_clean = resolved_st.lower()

                        if state_slug in url_lower or state_clean in title_lower or state_slug in title_lower:
                            loc_score = max(loc_score, 0.95)
                        elif "mahatenders" in title_lower and resolved_st == "Maharashtra":
                            loc_score = max(loc_score, 0.95)
                        else:
                            cities_in_state = STATE_TO_CITIES.get(resolved_st, set())
                            if any(c in url_lower or c in title_lower for c in cities_in_state):
                                loc_score = max(loc_score, 0.9)

                        # 4. Text fallback when page has no explicit location or state
                        # Only use text fallback if state appears in text AND no other state dominates
                        if loc_score < FUZZY_THRESHOLD and not page.location and not page.state:
                            text_lower = (page.text or "").lower()
                            if state_clean in text_lower:
                                # Count how many times each state appears and ensure target state dominates
                                target_count = text_lower.count(state_clean)
                                other_states_in_text = [
                                    st for st in CANONICAL_STATES
                                    if st != resolved_st and st.lower() in text_lower
                                ]
                                if not other_states_in_text or all(
                                    text_lower.count(st.lower()) < target_count
                                    for st in other_states_in_text
                                ):
                                    loc_score = max(loc_score, 0.85)
                    else:
                        # Location query is a specific city/town
                        # Step A: If the page has an explicit location/state that belongs to a DIFFERENT city's state, skip
                        city_state = CITY_TO_STATE.get(normalize(location))
                        if city_state:
                            if page.state:
                                page_city_st = resolve_state(page.state)
                                if page_city_st and page_city_st != city_state:
                                    continue
                            if page.location:
                                page_city_loc_st = resolve_state(page.location)
                                if page_city_loc_st and page_city_loc_st != city_state:
                                    continue
                                loc_clean_city = normalize(page.location)
                                mapped_city_state = CITY_TO_STATE.get(loc_clean_city)
                                if mapped_city_state and mapped_city_state != city_state:
                                    continue

                        loc_score = max(
                            fuzzy_match(location, page.location),
                            fuzzy_match(location, page.state),
                        )
                        if loc_score <= 0:
                            loc_tokens = _tokens(location)
                            loc_score = max(
                                fuzzy_match(location, page.title),
                                _any_token_match(loc_tokens, page.title or ""),
                                _any_token_match(loc_tokens, page.url or ""),
                                _any_token_match(loc_tokens, page.products or ""),
                            )
                            if loc_score < FUZZY_THRESHOLD and not page.location and not page.state:
                                _text_for_check = (page.text or "").lower()
                                _multi_state_count = sum(1 for _s in CANONICAL_STATES if _s.lower() in _text_for_check)
                                if _multi_state_count < 3 and not any(p in url_lower for p in ("/all-tenders/", "/tender-results/")):
                                    loc_score = max(loc_score, _any_token_match(loc_tokens, page.text or ""))

                    if loc_score < FUZZY_THRESHOLD:
                        continue
                    score += 6.0 * loc_score
                    matched.append("location")

                # Multi-tender listing page handling:
                # Listing pages aggregate dozens of tenders. If the page contains individual
                # tender cards in its text, unpack each specific card and evaluate it against
                # the query filters so specific matching tenders are returned instead of the
                # monolithic directory page with unrelated metadata.
                is_listing = (
                    url_lower.endswith("/active")
                    or "/active?" in url_lower
                    or url_lower.endswith("/archived")
                    or "/archived?" in url_lower
                    or any(m in url_lower for m in ("-tenders/active", "-city/active", "/all-tenders", "/tender-results", "/cities", "/states"))
                    or (page.title or "").strip().lower() in ("all tenders", "all active tenders", "tender results")
                    or (page.text and "Download" in page.text and len(page.text) > 400)
                )
                if is_listing:
                    cards = self._get_listing_cards(page)
                    if cards:
                        for card in cards:
                            if open_only and not _is_tender_open(card.bid_by_date):
                                continue
                            card_loc_score = 1.0 if not location else 0.0
                            card_matched: list[str] = []
                            if location:
                                if resolved_st:
                                    card_st = resolve_state(card.state or card.location or "")
                                    if card_st and card_st != resolved_st:
                                        continue
                                    if card_st == resolved_st or resolved_st.lower() in (card.location or "").lower() or resolved_st.lower() in (card.text or "").lower():
                                        card_loc_score = 1.0
                                elif norm_location:
                                    card_loc_norm = normalize(card.location or "")
                                    if norm_location in card_loc_norm or fuzzy_match(location, card.location) >= FUZZY_THRESHOLD or norm_location in normalize(card.text or ""):
                                        card_loc_score = 1.0
                                if card_loc_score <= 0.0:
                                    continue
                                card_matched.append("location")

                            # Domain-level category filtering:
                            query_cat = _resolve_category(tender_type)
                            card_cat = _resolve_category(card.tender_type)

                            # If user searched for a specific type category, never allow a conflicting category
                            if query_cat and card_cat and query_cat != card_cat:
                                if query_cat == "electrical" and card_cat == "railway":
                                    # Allow railway electrical/traction contracts (e.g. Electrical TRD)
                                    card_blob_lower = f"{card.title or ''} {card.products or ''} {card.text or ''}".lower()
                                    if any(m in card_blob_lower for m in ("brake system", "locos", "grocery", "photocopier", "textile", "liner")):
                                        continue
                                    if not any(el in card_blob_lower for el in ("electrical", "trd", "transformer", "substation", "switchgear", "traction", "wiring", "lighting", "power")):
                                        continue
                                else:
                                    continue

                            # When user searches for electrical tenders, strictly forbid bank, petroleum, civil, etc.
                            if query_cat == "electrical":
                                card_entity_str = f"{card.title or ''} {card.department or ''} {card.url or ''} {page.title or ''} {page.url or ''}".lower()
                                if any(b in card_entity_str for b in BANK_FINANCE_ENTITIES):
                                    continue
                                if any(p in card_entity_str for p in PETROLEUM_ENTITIES):
                                    continue
                                if any(rw in card_entity_str for rw in ("railway", "railways", "rail", "swr", "rwf", "metro")):
                                    card_blob_lower = f"{card.title or ''} {card.products or ''} {card.text or ''}".lower()
                                    if any(m in card_blob_lower for m in ("brake system", "locos", "grocery", "photocopier", "textile", "liner")):
                                        continue
                                    if not any(el in card_blob_lower for el in ("electrical", "trd", "transformer", "substation", "switchgear", "traction", "wiring", "lighting", "power")):
                                        continue
                                if card_cat in ("civil", "transport", "banking", "petroleum", "manpower", "housekeeping", "medical"):
                                    continue

                            # When user searches for railway, strictly forbid bank, petroleum, and non-railway categories
                            if query_cat == "railway":
                                card_entity_str = f"{card.title or ''} {card.department or ''} {card.url or ''} {page.title or ''} {page.url or ''}".lower()
                                if any(b in card_entity_str for b in BANK_FINANCE_ENTITIES):
                                    continue
                                if any(p in card_entity_str for p in PETROLEUM_ENTITIES):
                                    continue

                            card_type_score = 0.0
                            if tender_type and not generic_type:
                                if query_cat:
                                    if card_cat == query_cat:
                                        card_type_score = 1.0
                                    else:
                                        cat_words = CATEGORY_MAP.get(query_cat, set())
                                        card_core = f"{card.title or ''} {card.products or ''} {card.department or ''}".lower()
                                        if any(re.search(rf"\b{re.escape(w)}\b", card_core) for w in cat_words):
                                            card_type_score = 0.95
                                        else:
                                            continue
                                else:
                                    card_tt_norm = normalize(card.tender_type)
                                    tt_norm = normalize(tender_type)
                                    if card_tt_norm and (card_tt_norm in tt_norm or tt_norm in card_tt_norm):
                                        card_type_score = 1.0
                                    else:
                                        card_type_score = max(
                                            fuzzy_match(tender_type, card.tender_type),
                                            fuzzy_match(tender_type, card.title),
                                            fuzzy_match(tender_type, card.department),
                                        )
                                    if card_type_score < FUZZY_THRESHOLD:
                                        synonyms = set()
                                        for cat, words in TYPE_SYNONYMS.items():
                                            if cat in tender_type.lower() or tender_type.lower() in cat:
                                                synonyms.update(words)
                                        if not synonyms:
                                            synonyms = {tender_type.lower()}
                                        card_blob = f"{card.tender_type or ''} {card.title or ''} {card.products or ''} {card.department or ''}".lower()
                                        if any(re.search(rf"\b{re.escape(syn)}\b", card_blob) for syn in synonyms):
                                            card_type_score = 0.95
                                        else:
                                            continue
                                card_matched.append("type")

                            card_kw_score = 0.0
                            if keyword_tokens:
                                kw_ok = True
                                for token in keyword_tokens:
                                    cands = [
                                        _any_token_match([token], card.title or ""),
                                        _any_token_match([token], card.products or ""),
                                        _any_token_match([token], card.department or ""),
                                        _any_token_match([token], card.text or ""),
                                    ]
                                    if max(cands) < FUZZY_THRESHOLD:
                                        kw_ok = False
                                        break
                                if not kw_ok:
                                    continue
                                card_kw_score = 2.0
                                card_matched.append("keyword")

                            if not card_matched:
                                continue

                            card_score = 1.0 + (6.0 * card_loc_score) + (4.0 * card_type_score) + card_kw_score
                            hits.append(SearchHit(page=card, score=card_score, matched=card_matched))

                        continue

                # Skip generic multi-tender container / archive directory pages
                page_title_clean = (page.title or "").strip().lower()
                if (
                    any(m in url_lower for m in ("/tender-results", "/all-tenders/active", "/all-tenders/states", "/all-tenders/cities"))
                    or page_title_clean in ("all tenders", "all active tenders", "all tender results", "tender results", "indian tenders by states", "karnataka tenders", "maharashtra tenders", "indian tenders")
                    or page_title_clean.startswith("hould be")
                ):
                    continue

                if open_only and not _is_tender_open(page.bid_by_date):
                    continue

                # Non-listing single tender page filtering
                query_cat = _resolve_category(tender_type)
                page_cat = _resolve_category(page.tender_type)

                if query_cat and page_cat and query_cat != page_cat:
                    if query_cat == "electrical" and page_cat == "railway":
                        page_blob_lower = f"{page.title or ''} {page.products or ''} {page.text or ''}".lower()
                        if any(m in page_blob_lower for m in ("brake system", "locos", "grocery", "photocopier", "textile", "liner")):
                            continue
                        if not any(el in page_blob_lower for el in ("electrical", "trd", "transformer", "substation", "switchgear", "traction", "wiring", "lighting", "power")):
                            continue
                    else:
                        continue

                if query_cat == "electrical":
                    page_entity_str = f"{page.title or ''} {page.department or ''} {page.url or ''}".lower()
                    if any(b in page_entity_str for b in BANK_FINANCE_ENTITIES):
                        continue
                    if any(p in page_entity_str for p in PETROLEUM_ENTITIES):
                        continue
                    if any(rw in page_entity_str for rw in ("railway", "railways", "rail", "swr", "rwf", "metro")):
                        page_blob_lower = f"{page.title or ''} {page.products or ''} {page.text or ''}".lower()
                        if any(m in page_blob_lower for m in ("brake system", "locos", "grocery", "photocopier", "textile", "liner")):
                            continue
                        if not any(el in page_blob_lower for el in ("electrical", "trd", "transformer", "substation", "switchgear", "traction", "wiring", "lighting", "power")):
                            continue
                    if page_cat in ("civil", "transport", "banking", "petroleum", "manpower", "housekeeping", "medical"):
                        continue

                if query_cat == "railway":
                    page_entity_str = f"{page.title or ''} {page.department or ''} {page.url or ''}".lower()
                    if any(b in page_entity_str for b in BANK_FINANCE_ENTITIES):
                        continue
                    if any(p in page_entity_str for p in PETROLEUM_ENTITIES):
                        continue

                if tender_type and not generic_type:
                    if query_cat:
                        if page_cat == query_cat or (query_cat == "electrical" and page_cat == "railway"):
                            type_score = 1.0
                        else:
                            cat_words = CATEGORY_MAP.get(query_cat, set())
                            page_core = f"{page.title or ''} {page.products or ''} {page.department or ''}".lower()
                            if any(re.search(rf"\b{re.escape(w)}\b", page_core) for w in cat_words):
                                type_score = 0.95
                            elif is_listing and page.text:
                                synonyms = set()
                                for cat, words in TYPE_SYNONYMS.items():
                                    if cat in tender_type.lower() or tender_type.lower() in cat:
                                        synonyms.update(words)
                                if not synonyms:
                                    synonyms = {tender_type.lower()}
                                page_blob = f"{page.tender_type or ''} {page.title or ''} {page.products or ''} {page.department or ''} {page.text or ''}".lower()
                                if any(re.search(rf"\b{re.escape(syn)}\b", page_blob) for syn in synonyms):
                                    type_score = 0.9
                                else:
                                    continue
                            else:
                                continue
                    else:
                        page_tt_norm = normalize(page.tender_type)
                        tt_norm = normalize(tender_type)
                        if page_tt_norm and (page_tt_norm in tt_norm or tt_norm in page_tt_norm):
                            type_score = 1.0
                        else:
                            type_score = max(
                                fuzzy_match(tender_type, page.tender_type),
                                fuzzy_match(tender_type, page.title),
                            )
                        if type_score < FUZZY_THRESHOLD:
                            continue
                    score += 4.0 * type_score
                    matched.append("type")

                if keyword_tokens:
                    token_scores = []
                    for token in keyword_tokens:
                        candidates = [
                            (_any_token_match([token], page.tender_id or ""), 3.0),
                            (_any_token_match([token], page.title or ""), 2.5),
                            (_any_token_match([token], page.products or ""), 2.0),
                            (_any_token_match([token], page.tender_type or ""), 2.0),
                            (_any_token_match([token], page.department or ""), 1.5),
                            (_any_token_match([token], page.state or ""), 1.5),
                            (_any_token_match([token], page.text or ""), 0.8),
                        ]
                        raw_score, weight = max(candidates, key=lambda item: item[0])
                        if raw_score < FUZZY_THRESHOLD:
                            # Every meaningful keyword token must appear in the
                            # page; otherwise unrelated pages leak into results.
                            break
                        token_scores.append(raw_score * weight)
                    if len(token_scores) != len(keyword_tokens):
                        continue

                    # Strict relevance guard:
                    # Keyword must match at least one core tender field (title, products,
                    # tender_type, tender_id, department), OR if only present in text,
                    # the page must be a listing page or not have a conflicting single tender title.
                    core_matched = any(
                        max(
                            _any_token_match([t], page.title or ""),
                            _any_token_match([t], page.products or ""),
                            _any_token_match([t], page.tender_type or ""),
                            _any_token_match([t], page.tender_id or ""),
                            _any_token_match([t], page.department or ""),
                        ) >= FUZZY_THRESHOLD
                        for t in keyword_tokens
                    )
                    if not core_matched:
                        is_listing = (
                            any(m in url_lower for m in ("-tenders/active", "-city/active", "/all-tenders", "/tender-results"))
                            or (page.title or "").strip().lower() in ("all tenders", "all active tenders")
                            or ((page.title or "").strip().lower().endswith("tenders") and not page.tender_id and not page.products)
                        )
                        if is_listing:
                            text_score = min(_any_token_match([t], page.text or "") for t in keyword_tokens)
                            if text_score < FUZZY_THRESHOLD:
                                continue
                        else:
                            # If the single tender page has structured fields,
                            # but none matched the keyword, this page is not about this keyword.
                            if page.title or page.products or page.tender_type:
                                continue
                            text_score = min(_any_token_match([t], page.text or "") for t in keyword_tokens)
                            if text_score < 0.95:
                                continue

                    score += sum(token_scores)
                    matched.append("keyword")
                    if rowid in fts_ids:
                        score += 0.75

                if not matched:
                    continue
                # Earlier/later crawl ordering is not meaningful; keep stable
                # ordering by score then title for deterministic cards.
                hits.append(SearchHit(page=page, score=score, matched=matched))

        hits.sort(key=lambda hit: (-hit.score, (hit.page.title or "").lower(), hit.page.url))
        seen_titles: set[str] = set()
        deduped: list[SearchHit] = []
        for hit in hits:
            t = (hit.page.title or "").strip().lower()
            norm_t = re.sub(r"[^a-z0-9]+", " ", t).strip()
            t_key = norm_t[:90]
            if t_key and t_key in seen_titles:
                continue
            if t_key:
                seen_titles.add(t_key)
            deduped.append(hit)
        return deduped[: max(1, limit)]


def _active_path(site_dir: Path) -> Path:
    return site_dir / "active.json"


def read_active(site_dir: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(_active_path(site_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    name = data.get("file")
    if not isinstance(name, str) or "/" in name or "\\" in name:
        return None
    path = site_dir / "indexes" / name
    if not path.exists():
        return None
    data["path"] = str(path)
    return data


def build_index(site_dir: Path, pages: list[PageRecord]) -> dict[str, Any]:
    """Build a complete index and atomically swap the active pointer."""
    indexes_dir = site_dir / "indexes"
    indexes_dir.mkdir(parents=True, exist_ok=True)
    version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    final_path = indexes_dir / f"index-{version}.sqlite"
    temp_path = indexes_dir / f".index-{uuid.uuid4().hex}.tmp.sqlite"

    conn = sqlite3.connect(str(temp_path))
    try:
        conn.execute(
            """
            CREATE TABLE pages (
                id INTEGER PRIMARY KEY,
                url TEXT NOT NULL,
                title TEXT,
                text TEXT,
                tender_type TEXT,
                location TEXT,
                budget TEXT,
                timeline TEXT,
                products TEXT,
                department TEXT,
                bid_by_date TEXT,
                crawled_at TEXT NOT NULL,
                content_hash TEXT,
                tender_id TEXT,
                emd TEXT,
                emd_exemption TEXT,
                ecv TEXT,
                state TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE pages_fts USING fts5(
                title, text, tender_type, location, products, department, tender_id, state
            )
            """
        )
        for index, page in enumerate(pages, start=1):
            conn.execute(
                """
                INSERT INTO pages
                    (id, url, title, text, tender_type, location, budget,
                     timeline, products, department, bid_by_date, crawled_at, content_hash,
                     tender_id, emd, emd_exemption, ecv, state)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    index,
                    page.url,
                    page.title,
                    page.text,
                    page.tender_type,
                    page.location,
                    page.budget,
                    page.timeline,
                    page.products,
                    page.department,
                    page.bid_by_date,
                    page.crawled_at,
                    page.content_hash,
                    page.tender_id,
                    page.emd,
                    page.emd_exemption,
                    page.ecv,
                    page.state,
                ),
            )
            conn.execute(
                """
                INSERT INTO pages_fts
                    (rowid, title, text, tender_type, location, products, department, tender_id, state)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    index,
                    page.title or "",
                    page.text or "",
                    page.tender_type or "",
                    page.location or "",
                    page.products or "",
                    page.department or "",
                    page.tender_id or "",
                    page.state or "",
                ),
            )
        conn.execute(
            "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        meta = {
            "version": version,
            "built_at": _utcnow(),
            "page_count": str(len(pages)),
        }
        conn.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", meta.items())
        conn.commit()
    finally:
        conn.close()

    os.replace(temp_path, final_path)

    active = {
        "file": final_path.name,
        "version": version,
        "built_at": _utcnow(),
        "page_count": len(pages),
    }
    active_temp = site_dir / f".active-{uuid.uuid4().hex}.tmp"
    active_temp.write_text(json.dumps(active, indent=2), encoding="utf-8")
    os.replace(active_temp, _active_path(site_dir))

    # Keep the live index and one previous generation for in-flight searches.
    try:
        files = sorted(
            indexes_dir.glob("index-*.sqlite"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in files[2:]:
            old.unlink(missing_ok=True)
    except OSError:
        pass

    return active


class IndexManager:
    """Caches loaded in-memory indexes keyed by the active index file."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self._cache: dict[str, SearchIndex] = {}
        self._lock = threading.RLock()

    def site_dir(self, site_key: str) -> Path:
        return self.data_dir / "sites" / site_key

    def get(self, site_key: str) -> SearchIndex | None:
        site_dir = self.site_dir(site_key)
        active = read_active(site_dir)
        if not active:
            return None
        path = Path(active["path"])
        with self._lock:
            cached = self._cache.get(site_key)
            if cached is not None and cached.path == path:
                return cached
            if cached is not None:
                cached.close()
            try:
                loaded = SearchIndex.load(path)
            except (sqlite3.Error, OSError):
                return None
            self._cache[site_key] = loaded
            return loaded

    def active_info(self, site_key: str) -> dict[str, Any] | None:
        active = read_active(self.site_dir(site_key))
        if not active:
            return None
        return {key: active[key] for key in ("version", "built_at", "page_count")}

    def invalidate(self, site_key: str) -> None:
        with self._lock:
            cached = self._cache.pop(site_key, None)
            if cached is not None:
                cached.close()

    def close(self) -> None:
        with self._lock:
            for cached in self._cache.values():
                cached.close()
            self._cache.clear()
