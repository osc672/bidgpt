"""Environment-driven configuration for BidGPT.

Everything that operators normally change lives here, so the crawler, index,
chat service and MCP server can all be constructed with an explicit Settings
object (which also makes tests deterministic).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return default if value is None else value


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_tuple(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    return tuple(item.strip().rstrip("/") for item in raw.split(",") if item.strip())


@dataclass(slots=True)
class Settings:
    # Storage
    data_dir: Path = field(default_factory=lambda: Path(_env("BIDGPT_DATA_DIR", "./data")))

    # Crawl policy
    crawl_depth: int = field(default_factory=lambda: _env_int("CRAWL_DEPTH", 2))
    max_pages: int = field(default_factory=lambda: _env_int("MAX_PAGES", 100))
    recrawl_interval_seconds: int = field(
        default_factory=lambda: _env_int("RECRAWL_INTERVAL_SECONDS", 6 * 60 * 60)
    )
    request_delay_seconds: float = field(
        default_factory=lambda: _env_float("REQUEST_DELAY_SECONDS", 0.5)
    )
    request_timeout_seconds: float = field(
        default_factory=lambda: _env_float("REQUEST_TIMEOUT_SECONDS", 20.0)
    )
    max_response_bytes: int = field(
        default_factory=lambda: _env_int("MAX_RESPONSE_BYTES", 8_000_000)
    )
    max_pdf_bytes: int = field(default_factory=lambda: _env_int("MAX_PDF_BYTES", 15_000_000))
    user_agent: str = field(
        default_factory=lambda: _env(
            "USER_AGENT", "BidGPTBot/1.0 (+https://github.com/bidgpt/bidgpt)"
        )
    )
    public_base_url: str = field(
        default_factory=lambda: _env("PUBLIC_BASE_URL").rstrip("/")
    )
    playwright_enabled: bool = field(
        default_factory=lambda: _env_bool("PLAYWRIGHT_ENABLED", True)
    )
    playwright_wait_ms: int = field(
        default_factory=lambda: _env_int("PLAYWRIGHT_WAIT_MS", 750)
    )
    allow_private_urls: bool = field(
        default_factory=lambda: _env_bool("ALLOW_PRIVATE_URLS", False)
    )
    max_text_chars: int = 50_000
    max_pdf_pages: int = 80

    # Crawler backend: auto (Firecrawl when a key is configured), local or
    # firecrawl. Firecrawl renders JS-heavy and bot-protected public sites.
    crawler_backend: str = field(
        default_factory=lambda: _env("CRAWLER_BACKEND", "auto").strip().lower()
    )
    firecrawl_api_key: str = field(
        default_factory=lambda: _env("FIRECRAWL_API_KEY") or _env("FIRECRAWL_KEY")
    )
    firecrawl_api_url: str = field(
        default_factory=lambda: _env(
            "FIRECRAWL_API_URL", "https://api.firecrawl.dev/v2"
        ).rstrip("/")
    )
    firecrawl_timeout_seconds: float = field(
        default_factory=lambda: _env_float("FIRECRAWL_TIMEOUT_SECONDS", 300.0)
    )
    firecrawl_poll_seconds: float = field(
        default_factory=lambda: _env_float("FIRECRAWL_POLL_SECONDS", 2.0)
    )

    # LLM
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "openai").lower())
    llm_api_key: str = field(
        default_factory=lambda: _env("LLM_API_KEY")
        or _env("OPENAI_API_KEY")
        or _env("ANTHROPIC_API_KEY")
    )
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL"))
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL").rstrip("/"))
    llm_timeout_seconds: float = field(
        default_factory=lambda: _env_float("LLM_TIMEOUT_SECONDS", 30.0)
    )
    llm_enabled: bool | None = None  # None => enabled iff an API key exists

    # Chat safety
    chat_rate_limit_per_ip: int = field(
        default_factory=lambda: _env_int("CHAT_RATE_LIMIT_PER_IP", 20)
    )
    chat_rate_limit_per_site: int = field(
        default_factory=lambda: _env_int("CHAT_RATE_LIMIT_PER_SITE", 60)
    )
    rate_limit_window_seconds: int = field(
        default_factory=lambda: _env_int("RATE_LIMIT_WINDOW_SECONDS", 60)
    )
    admin_api_key: str = field(default_factory=lambda: _env("ADMIN_API_KEY"))
    trust_proxy: bool = field(default_factory=lambda: _env_bool("TRUST_PROXY", False))
    widget_extra_origins: tuple[str, ...] = field(
        default_factory=lambda: _env_tuple("WIDGET_EXTRA_ORIGINS")
    )

    # Misc
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO").upper())

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir).expanduser().resolve()
        self.crawl_depth = max(0, self.crawl_depth)
        self.max_pages = max(1, self.max_pages)
        self.recrawl_interval_seconds = max(60, self.recrawl_interval_seconds)
        self.request_delay_seconds = max(0.0, self.request_delay_seconds)
        self.firecrawl_timeout_seconds = max(10.0, self.firecrawl_timeout_seconds)
        self.firecrawl_poll_seconds = max(0.0, self.firecrawl_poll_seconds)
        if self.crawler_backend not in {"auto", "local", "firecrawl"}:
            self.crawler_backend = "auto"

    @property
    def registry_path(self) -> Path:
        return self.data_dir / "registry.sqlite"

    @property
    def sites_dir(self) -> Path:
        return self.data_dir / "sites"

    @property
    def llm_configured(self) -> bool:
        if self.llm_enabled is False:
            return False
        if self.llm_enabled is True:
            return True
        return bool(self.llm_api_key.strip())

    @property
    def firecrawl_configured(self) -> bool:
        return self.crawler_backend != "local" and bool(self.firecrawl_api_key.strip())

    @property
    def resolved_llm_model(self) -> str:
        if self.llm_model:
            return self.llm_model
        if self.llm_provider == "anthropic":
            return "claude-3-5-haiku-latest"
        return "gpt-4o-mini"

    @property
    def resolved_llm_base_url(self) -> str:
        if self.llm_base_url:
            return self.llm_base_url
        if self.llm_provider == "anthropic":
            return "https://api.anthropic.com"
        return "https://api.openai.com/v1"

    def site_dir(self, site_key: str) -> Path:
        # Site keys are generated by this service, but never trust them as paths.
        safe = "".join(ch for ch in site_key if ch.isalnum() or ch in "-_")
        return self.sites_dir / safe

    @classmethod
    def from_env(cls) -> "Settings":
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except Exception:  # pragma: no cover - optional convenience
            pass
        return cls()
