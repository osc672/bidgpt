"""Input sanitization, prompt-injection defense and in-process rate limiting."""

from __future__ import annotations

import html
import re
import threading
import time
from collections import defaultdict, deque
from typing import Deque

# Control characters that should never reach an index, an LLM or a browser.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")
_SPACE_RE = re.compile(r"[ \t\f\v]+")
_NEWLINES_RE = re.compile(r"\n{3,}")
_TAG_RE = re.compile(r"<[^>]{1,400}>")

_INJECTION_PATTERNS = [
    re.compile(r"\bignore\s+(all\s+)?(previous|prior|above|earlier)\s+instructions?\b", re.I),
    re.compile(r"\bdisregard\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|rules?)\b", re.I),
    re.compile(r"\bforget\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|rules?)\b", re.I),
    re.compile(r"\byou\s+are\s+now\s+(a|an)\s+", re.I),
    re.compile(r"\bsystem\s+prompt\b", re.I),
    re.compile(r"\bdeveloper\s+message\b", re.I),
    re.compile(r"\breveal\s+(the\s+)?(api\s+key|secret|password|token)\b", re.I),
    re.compile(r"\bdo\s+not\s+follow\s+(the\s+)?(system|developer)\b", re.I),
    re.compile(r"<\s*/?\s*(system|assistant|user)\s*>", re.I),
]


def sanitize_text(value: object, max_chars: int = 20_000) -> str:
    """Make crawled/user text safe, bounded and deterministic."""
    if value is None:
        return ""
    text = str(value)
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_RE.sub(" ", text)
    text = _ZERO_WIDTH_RE.sub("", text)
    text = _SPACE_RE.sub(" ", text)
    text = _NEWLINES_RE.sub("\n\n", text)
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0].strip()
        text += " …"
    return text


def strip_markup(value: object, max_chars: int = 20_000) -> str:
    text = str(value or "")
    text = _TAG_RE.sub(" ", text)
    return sanitize_text(text, max_chars=max_chars)


def neutralize_prompt_injection(text: str) -> tuple[str, bool]:
    """Redact instruction-like spans before page text is shown to an LLM.

    Tender pages rarely contain these phrases. Redaction is deliberately
    conservative, and the system prompt also tells the model that all page
    content is untrusted data.
    """
    detected = False
    result = text
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(result):
            detected = True
            result = pattern.sub("[redacted instruction-like text]", result)
    return result, detected


def sanitize_for_llm(value: object, max_chars: int = 4_000) -> tuple[str, bool]:
    text = sanitize_text(value, max_chars=max_chars)
    text, detected = neutralize_prompt_injection(text)
    # Never allow fake role tags to escape the data block.
    text = text.replace("<", "‹").replace(">", "›")
    return text, detected


def safe_source_url(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if value.startswith(("http://", "https://")):
        return value
    return None


class SlidingWindowRateLimiter:
    """Small, dependency-free per-process limiter keyed by IP/site."""

    def __init__(self, window_seconds: int = 60):
        self.window_seconds = max(1, window_seconds)
        self._hits: dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, now: float | None = None) -> tuple[bool, int]:
        if limit <= 0:
            return True, 0
        now = time.monotonic() if now is None else now
        cutoff = now - self.window_seconds
        with self._lock:
            bucket = self._hits[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                retry = max(1, int(self.window_seconds - (now - bucket[0])) + 1)
                return False, retry
            bucket.append(now)
            return True, 0

    def prune(self) -> None:
        cutoff = time.monotonic() - self.window_seconds
        with self._lock:
            for key in list(self._hits):
                bucket = self._hits[key]
                while bucket and bucket[0] <= cutoff:
                    bucket.popleft()
                if not bucket:
                    del self._hits[key]
