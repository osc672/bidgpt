"""URL normalization, same-site checks and an SSRF guard for registration."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
}

SKIP_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".css",
    ".js",
    ".zip",
    ".rar",
    ".7z",
    ".gz",
    ".tar",
    ".mp4",
    ".mp3",
    ".avi",
    ".mov",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
}

LOGIN_MARKERS = ("login", "signin", "sign-in", "sign_in", "log-in", "auth/", "/auth")


def _strip_default_port(parsed) -> str:
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return ""
    port = parsed.port
    if port and not ((parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)):
        return f"{host}:{port}"
    return host


def normalize_url(
    url: str, base: str | None = None, *, preserve_trailing_slash: bool = False
) -> str | None:
    """Return a canonical absolute http(s) URL, or None when unsupported.

    Trailing slashes are stripped by default so links to the same page are
    deduplicated. Redirect targets must pass `preserve_trailing_slash=True`:
    many servers redirect `/path` to `/path/`, and normalizing the target back
    to `/path` would create an endless redirect loop.
    """
    if not url:
        return None
    url = url.strip()
    if base:
        url = urljoin(base, url)
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    host_port = _strip_default_port(parsed)
    if not host_port:
        return None
    path = parsed.path or "/"
    if not preserve_trailing_slash and path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS
    ]
    query = urlencode(query_pairs, doseq=True)
    return urlunparse((parsed.scheme.lower(), host_port, path, "", query, ""))


def is_skippable_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    if any(path.endswith(ext) for ext in SKIP_EXTENSIONS):
        return True
    return False


def is_login_url(url: str) -> bool:
    parsed = urlparse(url)
    haystack = (parsed.path + "?" + parsed.query).lower()
    return any(marker in haystack for marker in LOGIN_MARKERS)


def same_site(host: str | None, base_host: str | None) -> bool:
    if not host or not base_host:
        return False
    host = host.lower().rstrip(".")
    base_host = base_host.lower().rstrip(".")
    if host == base_host:
        return True
    return host == f"www.{base_host}" or base_host == f"www.{host}"


def _is_private_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def host_is_private(host: str) -> bool:
    if not host:
        return True
    host = host.strip("[]").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        return True
    if _is_private_ip(host):
        return True
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        # A hostname that cannot resolve is not a usable crawl target anyway.
        return False
    for info in infos:
        sockaddr = info[4]
        if sockaddr and _is_private_ip(sockaddr[0]):
            return True
    return False


def validate_site_url(url: str, *, allow_private: bool = False) -> tuple[str, str]:
    """Validate a registration URL and return (normalized_url, hostname).

    Raises ValueError with a user-safe message. This is an SSRF guard: private
    and loopback targets are refused unless explicitly enabled for local demos
    and tests.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("A website URL is required.")
    raw = url.strip()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http and https URLs are supported.")
    if parsed.username or parsed.password:
        raise ValueError("URLs with embedded credentials are not allowed.")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise ValueError("The URL must include a hostname.")
    if not allow_private and host_is_private(host):
        raise ValueError("Private, loopback and link-local hosts are not allowed.")
    normalized = normalize_url(raw)
    if normalized is None:
        raise ValueError("The URL could not be normalized.")
    return normalized, host


def origin_host(origin: str | None) -> str | None:
    if not origin:
        return None
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"}:
        return None
    return (parsed.hostname or "").lower().rstrip(".")


def origin_matches(origin: str | None, domain: str, extra: tuple[str, ...] = ()) -> bool:
    """Accept only the registered site's origin (and optional demo origins)."""
    if not origin:
        return False
    cleaned = origin.rstrip("/")
    if any(cleaned == item.rstrip("/") for item in extra):
        return True
    host = origin_host(origin)
    return same_site(host, domain)
