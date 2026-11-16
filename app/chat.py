"""Grounded chat orchestration: intent -> page index -> LLM or fallback."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .index import IndexManager, SearchIndex, _fuzzy_token, normalize
from .llm import LLMClient
from .registry import Registry, SiteRecord
from .security import sanitize_for_llm, sanitize_text

GREETING_RE = re.compile(
    r"^\s*(?:hi|hello|hey|namaste|hola|good\s+(?:morning|afternoon|evening)|"
    r"start|help|howdy)\b",
    re.I,
)
GENERIC_TENDER_WORDS = {
    "tender",
    "tenders",
    "tendor",
    "tendors",
    "bid",
    "bids",
    "rfp",
    "rfps",
    "rfq",
    "rfqs",
    "procurement",
    "procurements",
    "notice",
    "notices",
}

GENERIC_ALL_WORDS = {
    "all", "type", "types", "category", "categories", "location", "locations",
    "and", "or", "tender", "tenders", "tendor", "tendors", "bid", "bids",
    "rfp", "rfps", "in", "of", "for", "every", "pan", "india", "show", "view",
    "get", "list", "give", "me", "the", "please", "any", "here", "available", "with"
}

ALL_TENDERS_PATTERNS = [
    r"^\s*all\s*$",
    r"^\s*all\s+tenders?\s*$",
    r"^\s*all\s+bids?\s*$",
    r"^\s*all\s+rfps?\s*$",
    r"^\s*(?:show|view|find|get|list|see|give\s+me)\s+(?:all|all\s+tenders?)\s*$",
    r"\ball\s+types?\b.*\ball\s+locations?\b",
    r"\ball\s+locations?\b.*\ball\s+types?\b",
    r"\ball\s+(?:types?|categories?)\s+and\s+(?:all\s+)?locations?\b",
    r"\ball\s+locations?\s+and\s+(?:all\s+)?(?:types?|categories?)\b",
    r"^\s*all\s+types?\s*$",
    r"^\s*all\s+locations?\s*$",
    r"^\s*all\s+types?\s+of\s+tenders?\s*$",
    r"^\s*every\s+tenders?\s*$",
    r"^\s*all\s+tenders?\s+all\s+locations?\s*$",
    r"^\s*all\s+india\s+tenders?\s*$",
    r"^\s*pan\s+india\s+tenders?\s*$",
]
ALL_TENDERS_RE = re.compile("|".join(f"(?:{p})" for p in ALL_TENDERS_PATTERNS), re.I)

from .geo import CITY_TO_STATE, KNOWN_CITIES, STATE_ALIASES, TYPE_SYNONYMS, resolve_state

STOPWORDS = {
    "a",
    "about",
    "all",
    "also",
    "am",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "available",
    "be",
    "bid",
    "bids",
    "by",
    "can",
    "could",
    "details",
    "do",
    "find",
    "for",
    "from",
    "get",
    "give",
    "have",
    "help",
    "here",
    "i",
    "in",
    "is",
    "it",
    "latest",
    "list",
    "looking",
    "me",
    "my",
    "near",
    "need",
    "of",
    "on",
    "or",
    "please",
    "procurement",
    "procurements",
    "rfp",
    "rfps",
    "rfq",
    "rfqs",
    "show",
    "tender",
    "tenders",
    "tendor",
    "tendors",
    "the",
    "there",
    "to",
    "want",
    "what",
    "which",
    "with",
    "you",
    "your",
}


@dataclass(slots=True)
class Intent:
    location: str | None = None
    tender_type: str | None = None
    keyword: str | None = None
    greeting: bool = False
    generic_tender: bool = False
    all_tenders: bool = False
    missing: list[str] = field(default_factory=list)
    context_location: bool = False
    context_type: bool = False


@dataclass(slots=True)
class ChatOutcome:
    answer: str
    tenders: list[dict[str, Any]]
    sources: list[str]
    status: str
    missing: list[str] = field(default_factory=list)
    intent: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "tenders": self.tenders,
            "sources": self.sources,
            "status": self.status,
            "missing": self.missing,
            "intent": self.intent,
        }


def _message_tokens(value: str) -> list[str]:
    return normalize(value).split()


def _phrase_in_text(phrase: str, text: str) -> bool:
    """Match a place/type phrase in free text, allowing spelling variants."""
    phrase_tokens = [token for token in normalize(phrase).split() if token]
    if not phrase_tokens:
        return False
    text_tokens = _message_tokens(text)
    if not text_tokens:
        return False
    for phrase_token in phrase_tokens:
        if not any(_fuzzy_token(phrase_token, token) >= 0.82 for token in text_tokens):
            return False
    return True


def _location_candidates(index: SearchIndex) -> list[str]:
    candidates: set[str] = set()
    for location in index.locations():
        full = sanitize_text(location, 160)
        if full:
            candidates.add(full)
            first = full.split(",")[0].strip()
            if first:
                candidates.add(first)
    return sorted(candidates, key=lambda value: (-len(value), value))


def _match_index_type(index: SearchIndex, message: str) -> str | None:
    tokens = _message_tokens(message)
    if not tokens:
        return None
    token_set = set(tokens)

    for type_name in index.types():
        t_low = type_name.lower()
        if "electrical" in t_low and token_set & {"electrical", "electricity", "electric", "power"}:
            return "Electrical Works" if "Electrical Works" in index.types() else "Electrical"
        if "railway" in t_low and token_set & {"railway", "railways", "rail", "train", "trains", "metro"}:
            return "Railway"
        if "civil" in t_low and token_set & {"civil", "construction"}:
            return "Civil Works" if "Civil Works" in index.types() else "Civil"
        if "medical" in t_low and token_set & {"medical", "medicine", "medicines", "pharma", "health"}:
            return "Medical Supplies" if "Medical Supplies" in index.types() else "Medical"

    # Filter out location and generic words before matching remaining index types
    location_words = {
        "pradesh", "state", "states", "india", "indian", "district", "dist", "city",
        "north", "south", "east", "west", "central", "nadu"
    }
    type_tokens_in_msg = []
    for t in tokens:
        if t in STATE_ALIASES or t in KNOWN_CITIES or t in CITY_TO_STATE or resolve_state(t):
            continue
        if t in location_words or t in GENERIC_TENDER_WORDS or t in STOPWORDS or len(t) < 3:
            continue
        type_tokens_in_msg.append(t)

    if not type_tokens_in_msg:
        return None

    msg_type_phrase = " ".join(type_tokens_in_msg)
    best: tuple[float, str] | None = None
    for type_name in index.types():
        type_norm = normalize(type_name)
        type_tokens = [token for token in type_norm.split() if len(token) >= 3]
        if not type_tokens:
            continue

        if _phrase_in_text(type_name, message) or msg_type_phrase == type_norm:
            score = 1.0
        elif len(type_tokens) <= 3 and msg_type_phrase in type_norm:
            score = 0.95
        else:
            word_scores = [
                max((_fuzzy_token(word, t_tok) for t_tok in type_tokens), default=0.0)
                for word in type_tokens_in_msg
            ]
            if not word_scores or min(word_scores) < 0.82:
                continue
            avg_score = sum(word_scores) / len(word_scores)
            length_penalty = min(1.0, (len(type_tokens_in_msg) + 2) / len(type_tokens))
            score = avg_score * (0.5 + 0.5 * length_penalty)

        if score >= 0.75 and (best is None or score > best[0]):
            best = (score, type_name)

    return best[1] if best else None


def _infer_type_from_words(message: str) -> str | None:
    tokens = set(_message_tokens(message))
    if tokens & {"railway", "railways", "rail", "train", "trains", "metro"}:
        return "railway"
    if tokens & {"electricity", "power", "electrical", "electrician", "substation", "transformer"}:
        return "electrical"
    for category, words in TYPE_SYNONYMS.items():
        if tokens & words or any(_phrase_in_text(word, message) for word in words if " " in word):
            return category
    return None


def _is_valid_location_candidate(candidate: str, index: SearchIndex | None = None) -> bool:
    if not candidate:
        return False
    lowered = candidate.lower().strip()
    if lowered in STATE_ALIASES or lowered in KNOWN_CITIES:
        return True
    if len(lowered) < 3 or lowered in STOPWORDS or lowered in GENERIC_TENDER_WORDS or bool(GREETING_RE.search(lowered)):
        return False
    if lowered.isdigit() or lowered in {"india"}:
        return False
    if _infer_type_from_words(lowered) or any(lowered in words for words in TYPE_SYNONYMS.values()):
        return False
    if index and _match_index_type(index, lowered):
        return False
    return True


def _fallback_location(
    message: str,
    history: list[dict[str, str]] | None = None,
    index: SearchIndex | None = None,
    tender_type: str | None = None,
) -> str | None:
    """Pick up an unknown or abbreviated place so we can return an honest result or search by place."""
    msg_tokens = _message_tokens(message)
    # 1. State alias check
    for tok in msg_tokens:
        if tok in STATE_ALIASES:
            return STATE_ALIASES[tok]

    # 2. Known cities check
    for tok in msg_tokens:
        if tok in KNOWN_CITIES:
            return tok.title()

    patterns = [
        r"\b(?:in|at|near|from)\s+([A-Za-z]{2,}(?:\s+[A-Za-z]{2,})?)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, message, re.I)
        if not match:
            continue
        candidate = match.group(1).strip()
        lowered = candidate.lower()
        if lowered in STATE_ALIASES:
            return STATE_ALIASES[lowered]
        if _is_valid_location_candidate(candidate, index):
            return candidate.title()

    if history:
        for item in reversed(history):
            if item.get("role") == "assistant":
                content = item.get("content", "").lower()
                if "which location" in content:
                    tokens = [
                        t for t in _message_tokens(message)
                        if _is_valid_location_candidate(t, index)
                    ]
                    if tokens:
                        joined = " ".join(tokens)
                        if joined.lower() in STATE_ALIASES:
                            return STATE_ALIASES[joined.lower()]
                        return joined.title()
                break

    type_tokens = set(_message_tokens(tender_type or ""))
    for syn_cat, syn_words in TYPE_SYNONYMS.items():
        if tender_type and syn_cat in tender_type.lower():
            type_tokens.update(syn_words)

    rem_tokens = [
        t for t in msg_tokens
        if t not in type_tokens and _is_valid_location_candidate(t, index)
    ]
    if 1 <= len(rem_tokens) <= 3 and not bool(GREETING_RE.search(message)):
        kw_candidate = " ".join(rem_tokens)
        if index and index.search(keyword=kw_candidate):
            return None
        return kw_candidate.title()

    return None


def _extract_keyword(message: str, intent: Intent) -> str | None:
    tokens = _message_tokens(message)
    removed = set(_message_tokens(intent.location or ""))
    removed |= set(_message_tokens(intent.tender_type or ""))
    # Also remove state alias token from keyword if location came from alias (e.g. "ap" -> "Andhra Pradesh")
    if intent.location:
        for alias, state_name in STATE_ALIASES.items():
            if state_name.lower() == intent.location.lower():
                removed.add(alias)
                for part in alias.split():
                    removed.add(part)
    words: list[str] = []
    for token in tokens:
        if len(token) < 3 or token in STOPWORDS or token in removed:
            continue
        if intent.location and resolve_state(token) == intent.location:
            continue
        if token.isdigit():
            continue
        words.append(token)
        if len(words) >= 8:
            break
    if set(tokens) & {"railway", "railways", "rail", "train", "trains", "metro"}:
        if "railway" not in words:
            words.append("railway")
    return " ".join(words) if words else None


def parse_intent(
    message: str,
    history: list[dict[str, str]],
    index: SearchIndex,
) -> Intent:
    intent = Intent()
    intent.greeting = bool(GREETING_RE.search(message))
    tokens = set(_message_tokens(message))
    intent.generic_tender = bool(tokens & GENERIC_TENDER_WORDS)

    # Check if user specifically requested all tenders across all types and locations
    all_clean_tokens = [t for t in _message_tokens(message) if t not in GENERIC_ALL_WORDS]
    if ALL_TENDERS_RE.search(message) or not all_clean_tokens:
        has_specific_loc = any(tok in STATE_ALIASES or tok in KNOWN_CITIES for tok in all_clean_tokens) or (
            bool(all_clean_tokens) and bool(resolve_state(" ".join(all_clean_tokens)))
        )
        has_specific_type = (
            bool(all_clean_tokens)
            and (bool(_match_index_type(index, " ".join(all_clean_tokens))) or bool(_infer_type_from_words(" ".join(all_clean_tokens))))
        )
        if not has_specific_loc and not has_specific_type:
            intent.all_tenders = True
            intent.generic_tender = True
            intent.location = None
            intent.tender_type = None
            intent.keyword = None
            intent.missing = []
            return intent

    # 1. Direct match on index types or known category synonyms in current message
    tender_type = _match_index_type(index, message)
    if not tender_type:
        inferred = _infer_type_from_words(message)
        if inferred:
            tender_type = inferred.title()
            intent.generic_tender = False
    intent.tender_type = tender_type

    # 2. Location candidates from current message first
    location = resolve_state(message)
    if location:
        intent.generic_tender = True

    if not location:
        msg_tokens = _message_tokens(message)
        for tok in msg_tokens:
            st = resolve_state(tok)
            if st:
                location = st
                intent.generic_tender = True
                break
            if tok in CITY_TO_STATE:
                location = tok.title()
                break

    if not location:
        for candidate in _location_candidates(index):
            if _phrase_in_text(candidate, message):
                location = candidate
                break

    if not location:
        location = _fallback_location(message, history=None, index=index, tender_type=intent.tender_type)

    intent.location = location

    # If user provided a location in response to the greeting prompt ("Which type of tender and which location are you looking for?"):
    if intent.location and not intent.tender_type and history:
        last_assistant = next((h.get("content", "").lower() for h in reversed(history) if h.get("role") == "assistant"), "")
        if "which type of tender" in last_assistant and "which location" in last_assistant:
            intent.generic_tender = True

    # 3. Extract keyword from current message
    intent.keyword = _extract_keyword(message, intent)

    # 4. Context inheritance from history:
    # If current turn didn't provide a tender type or keyword, inherit from previous user messages
    if not intent.tender_type and not intent.keyword and not intent.generic_tender:
        for item in reversed(history[-6:]):
            if item.get("role") != "user":
                continue
            prev_content = item.get("content", "")
            prev_type = _match_index_type(index, prev_content)
            if not prev_type:
                inferred_prev = _infer_type_from_words(prev_content)
                if inferred_prev:
                    prev_type = inferred_prev.title()
            if prev_type:
                intent.tender_type = prev_type
                intent.context_type = True
            temp_prev = Intent()
            temp_prev.tender_type = prev_type
            prev_kw = _extract_keyword(prev_content, temp_prev)
            if prev_kw and not intent.keyword:
                intent.keyword = prev_kw
            if intent.tender_type or intent.keyword:
                break

    # Inherit location from history if current message has NO location
    explicit_all_locations = any(
        phrase in message.lower()
        for phrase in ("all india", "pan india", "all locations", "across india", "all over india", "any location")
    )
    if not intent.location and not explicit_all_locations:
        for item in reversed(history[-6:]):
            if item.get("role") != "user":
                continue
            prev_msg = item.get("content", "")
            for tok in _message_tokens(prev_msg):
                if tok in STATE_ALIASES:
                    intent.location = STATE_ALIASES[tok]
                    intent.context_location = True
                    break
                if tok in KNOWN_CITIES:
                    intent.location = tok.title()
                    intent.context_location = True
                    break
            if intent.location:
                break
            for candidate in _location_candidates(index):
                if _phrase_in_text(candidate, prev_msg):
                    intent.location = candidate
                    intent.context_location = True
                    break
            if not intent.location:
                prev_loc = _fallback_location(prev_msg, history=None, index=index)
                if prev_loc:
                    intent.location = prev_loc
                    intent.context_location = True
                    break
            if intent.location:
                break

    if not intent.location and not intent.keyword:
        intent.missing.append("location")
    if not intent.tender_type and not intent.generic_tender and not intent.keyword:
        intent.missing.append("type")
    return intent


class ChatService:
    MAX_CARDS = 2000

    def __init__(
        self,
        settings: Settings,
        registry: Registry,
        index_manager: IndexManager,
        llm: LLMClient | None = None,
    ):
        self.settings = settings
        self.registry = registry
        self.index_manager = index_manager
        self.llm = llm or LLMClient(settings)

    async def answer(
        self,
        site_key: str,
        message: str,
        history: list[dict[str, str]] | None = None,
    ) -> ChatOutcome:
        site = self.registry.get(site_key)
        if site is None:
            raise KeyError("site not found")
        history = history or []
        message, _ = sanitize_for_llm(message, max_chars=2_000)
        index = self.index_manager.get(site_key)
        if index is None or site.active_index is None:
            return ChatOutcome(
                answer="This site's tender index is still getting ready. Please try again shortly.",
                tenders=[],
                sources=[],
                status=site.status,
            )

        intent = parse_intent(message, history, index)
        if intent.all_tenders:
            hits = index.search(None, None, None, limit=self.MAX_CARDS)
            cards = [hit.public() for hit in hits]
            sources = self._unique_sources(cards)
            count = len(cards)
            noun = "tender" if count == 1 else "tenders"
            answer = f"I found {count} {noun} across all categories and locations on {site.domain}. You can filter by category or location below."
            return ChatOutcome(
                answer=answer,
                tenders=cards,
                sources=sources,
                status=site.status,
                missing=[],
                intent=self._intent_public(intent),
            )

        if intent.greeting and not intent.location and not intent.tender_type and not intent.keyword:
            return ChatOutcome(
                answer="Hi! Which type of tender and which location are you looking for?",
                tenders=[],
                sources=[],
                status=site.status,
                missing=["location", "type"],
                intent=self._intent_public(intent),
            )

        if intent.missing and not intent.keyword:
            if "location" in intent.missing and "type" in intent.missing:
                answer = "Which type of tender and which location are you looking for?"
                return ChatOutcome(
                    answer=answer,
                    tenders=[],
                    sources=[],
                    status=site.status,
                    missing=intent.missing,
                    intent=self._intent_public(intent),
                )
            elif "type" in intent.missing:
                answer = "Which type of tender are you looking for?"
                return ChatOutcome(
                    answer=answer,
                    tenders=[],
                    sources=[],
                    status=site.status,
                    missing=intent.missing,
                    intent=self._intent_public(intent),
                )
            # When only "location" is missing and intent.tender_type is present,
            # we do not block with empty results! We search across all locations
            # and provide location filter chips so the user can easily narrow down.

        hits = index.search(
            intent.location,
            intent.tender_type,
            intent.keyword,
            limit=self.MAX_CARDS,
        )
        if not hits and intent.keyword and intent.tender_type:
            hits = index.search(
                intent.location,
                None,
                intent.keyword,
                limit=self.MAX_CARDS,
            )
        cards = [hit.public() for hit in hits]
        sources = self._unique_sources(cards)
        answer = self._fallback_answer(site, intent, cards)

        if self.settings.llm_configured:
            llm_answer = await self.llm.answer(
                domain=site.domain,
                message=message,
                history=history,
                searcher=lambda args: self._tool_search(index, intent, args),
                hint={
                    "location": intent.location,
                    "type": intent.tender_type,
                    "keyword": intent.keyword,
                },
            )
            if llm_answer is not None and llm_answer.text:
                answer = llm_answer.text
                if llm_answer.results:
                    cards = llm_answer.results[: self.MAX_CARDS]
                    sources = self._unique_sources(cards)
                elif not llm_answer.results:
                    # The model searched and found nothing; keep the answer and
                    # remove any deterministic cards so text and cards agree.
                    cards = []
                    sources = []

        return ChatOutcome(
            answer=answer,
            tenders=cards,
            sources=sources,
            status=site.status,
            intent=self._intent_public(intent),
        )

    def _tool_search(
        self, index: SearchIndex, intent: Intent, args: dict[str, Any]
    ) -> list[dict[str, Any]]:
        location = sanitize_text(args.get("location") or intent.location or "", 160) or None
        tender_type = sanitize_text(args.get("type") or intent.tender_type or "", 120) or None
        keyword = sanitize_text(args.get("keyword") or intent.keyword or "", 200) or None
        hits = index.search(location, tender_type, keyword, limit=self.MAX_CARDS)
        return [hit.public() for hit in hits]

    def _fallback_answer(
        self, site: SiteRecord, intent: Intent, cards: list[dict[str, Any]]
    ) -> str:
        where = intent.location
        kw = intent.keyword
        is_generic = not kw and (not intent.tender_type or intent.tender_type.lower() in ("tenders", "tender", "all", "any", "any tender", "any tenders"))
        what = f"'{kw}'" if kw else (intent.tender_type or "tenders")
        if not cards:
            if where:
                return (
                    f"No tenders matching {what} in {where} are listed on this site. "
                    "Try a different type or location."
                )
            if "location" in intent.missing:
                return f"No {what} tenders found on {site.domain}. Which location are you looking for?"
            query_desc = f"'{kw}'" if kw else what
            return (
                f"No tenders matching {query_desc} are listed on this site. "
                "Try a different search term or location."
            )
        count = len(cards)
        noun = "tender" if count == 1 else "tenders"
        if where:
            if is_generic:
                return f"I found {count} {noun} in {where} on {site.domain}. Details are below."
            return f"I found {count} {noun} for {what} in {where} on {site.domain}. Details are below."
        elif kw:
            if not where:
                return f"I found {count} {noun} matching '{kw}' across all locations on {site.domain}. Which location are you looking for? You can filter by location below."
            return f"I found {count} {noun} matching '{kw}' on {site.domain}. Details are below."
        else:
            if "location" in intent.missing:
                return f"I found {count} {noun} for {what} across all locations on {site.domain}. Which location are you looking for? You can filter by location below."
            return f"I found {count} {noun} for {what} on {site.domain}. Details are below."

    @staticmethod
    def _unique_sources(cards: list[dict[str, Any]]) -> list[str]:
        seen: set[str] = set()
        sources: list[str] = []
        for card in cards:
            url = card.get("url")
            if isinstance(url, str) and url.startswith(("http://", "https://")) and url not in seen:
                seen.add(url)
                sources.append(url)
        return sources

    @staticmethod
    def _intent_public(intent: Intent) -> dict[str, Any]:
        return {
            "location": intent.location,
            "type": intent.tender_type,
            "keyword": intent.keyword,
            "generic_tender": intent.generic_tender,
            "all_tenders": intent.all_tenders,
            "used_history": intent.context_location or intent.context_type,
        }
