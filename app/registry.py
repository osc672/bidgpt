"""Site registry (metadata only, never tender data).

The registry is intentionally small: it stores the site key, the registered
domain, crawl status and a pointer to the currently active index file. Tender
content lives only in the per-site index files.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds") if dt else None


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@dataclass(slots=True)
class SiteRecord:
    site_key: str
    url: str
    domain: str
    status: str
    pages_found: int
    pages_indexed: int
    crawl_started_at: str | None
    last_crawled_at: str | None
    next_crawl_at: str | None
    active_index: str | None
    last_error: str | None
    errors: list[dict[str, str]]
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "SiteRecord":
        try:
            errors = json.loads(row["errors_json"] or "[]")
        except (TypeError, ValueError):
            errors = []
        return cls(
            site_key=row["site_key"],
            url=row["url"],
            domain=row["domain"],
            status=row["status"],
            pages_found=row["pages_found"],
            pages_indexed=row["pages_indexed"],
            crawl_started_at=row["crawl_started_at"],
            last_crawled_at=row["last_crawled_at"],
            next_crawl_at=row["next_crawl_at"],
            active_index=row["active_index"],
            last_error=row["last_error"],
            errors=errors,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def public(self) -> dict[str, Any]:
        return {
            "site_key": self.site_key,
            "url": self.url,
            "domain": self.domain,
            "status": self.status,
            "pages_found": self.pages_found,
            "page_count": self.pages_indexed,
            "pages_indexed": self.pages_indexed,
            "has_index": bool(self.active_index),
            "crawl_started_at": self.crawl_started_at,
            "last_crawled_at": self.last_crawled_at,
            "next_crawl_at": self.next_crawl_at,
            "last_error": self.last_error,
            # Surface a bounded error list; crawl errors are also kept in the
            # registry so operators can diagnose individual URL failures.
            "errors": self.errors[:50],
            "created_at": self.created_at,
        }


class Registry:
    """Thread-safe SQLite-backed site registry."""

    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self._lock = threading.RLock()
        try:
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            with self._lock:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=NORMAL")
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sites (
                        site_key TEXT PRIMARY KEY,
                        url TEXT NOT NULL UNIQUE,
                        domain TEXT NOT NULL,
                        status TEXT NOT NULL,
                        pages_found INTEGER NOT NULL DEFAULT 0,
                        pages_indexed INTEGER NOT NULL DEFAULT 0,
                        crawl_started_at TEXT,
                        last_crawled_at TEXT,
                        next_crawl_at TEXT,
                        active_index TEXT,
                        last_error TEXT,
                        errors_json TEXT NOT NULL DEFAULT '[]',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                self._conn.commit()
        except sqlite3.OperationalError:
            # Filesystem is read-only (e.g. Vercel / serverless); connect with read-only URI
            uri = f"file:{self.path.as_posix()}?mode=ro"
            self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ reads
    def get(self, site_key: str) -> SiteRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sites WHERE site_key = ?", (site_key,)
            ).fetchone()
        return SiteRecord.from_row(row) if row else None

    def get_by_url(self, url: str) -> SiteRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM sites WHERE url = ?", (url,)).fetchone()
        return SiteRecord.from_row(row) if row else None

    def list_all(self) -> list[SiteRecord]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM sites ORDER BY created_at").fetchall()
        return [SiteRecord.from_row(row) for row in rows]

    def due_sites(self, now: datetime | None = None) -> list[SiteRecord]:
        now = now or utcnow()
        result: list[SiteRecord] = []
        for site in self.list_all():
            if site.status in {"queued", "crawling"}:
                continue
            due = parse_iso(site.next_crawl_at)
            if due is None or due <= now:
                result.append(site)
        return result

    # ----------------------------------------------------------------- writes
    def create(self, url: str, domain: str) -> SiteRecord:
        now = iso(utcnow())
        key = "sk_" + secrets.token_hex(10)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sites
                    (site_key, url, domain, status, pages_found, pages_indexed,
                     crawl_started_at, last_crawled_at, next_crawl_at,
                     active_index, last_error, errors_json, created_at, updated_at)
                VALUES (?, ?, ?, 'queued', 0, 0, NULL, NULL, ?, NULL, NULL, '[]', ?, ?)
                """,
                (key, url, domain, now, now, now),
            )
            self._conn.commit()
        record = self.get(key)
        assert record is not None
        return record

    def update(self, site_key: str, **fields: Any) -> SiteRecord | None:
        allowed = {
            "url",
            "domain",
            "status",
            "pages_found",
            "pages_indexed",
            "crawl_started_at",
            "last_crawled_at",
            "next_crawl_at",
            "active_index",
            "last_error",
            "errors_json",
        }
        updates: dict[str, Any] = {}
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(f"unknown site field: {key}")
            if key == "errors" and isinstance(value, list):
                updates["errors_json"] = json.dumps(value[:200], ensure_ascii=False)
            elif key == "errors_json":
                updates["errors_json"] = value
            else:
                updates[key] = value
        updates["updated_at"] = iso(utcnow())
        clause = ", ".join(f"{name} = ?" for name in updates)
        with self._lock:
            self._conn.execute(
                f"UPDATE sites SET {clause} WHERE site_key = ?",
                (*updates.values(), site_key),
            )
            self._conn.commit()
        return self.get(site_key)

    def set_crawl_started(self, site_key: str) -> None:
        now = utcnow()
        self.update(
            site_key,
            status="crawling",
            crawl_started_at=iso(now),
            pages_found=0,
            pages_indexed=0,
            last_error=None,
            errors_json="[]",
        )

    def mark_ready(
        self,
        site_key: str,
        *,
        pages_found: int,
        active_index: str,
        errors: list[dict[str, str]],
        interval_seconds: int,
    ) -> None:
        now = utcnow()
        self.update(
            site_key,
            status="ready",
            pages_found=pages_found,
            pages_indexed=pages_found,
            last_crawled_at=iso(now),
            next_crawl_at=iso(now + timedelta(seconds=interval_seconds)),
            active_index=active_index,
            last_error=None,
            errors_json=json.dumps(errors[:200], ensure_ascii=False),
        )

    def mark_failed(
        self,
        site_key: str,
        error: str,
        *,
        errors: list[dict[str, str]] | None = None,
        interval_seconds: int = 3600,
        keep_index: bool = True,
    ) -> None:
        now = utcnow()
        fields: dict[str, Any] = {
            "status": "failed",
            "last_error": error[:2000],
            "next_crawl_at": iso(now + timedelta(seconds=interval_seconds)),
            "errors_json": json.dumps((errors or [])[:200], ensure_ascii=False),
        }
        if not keep_index:
            fields["active_index"] = None
            fields["pages_indexed"] = 0
        self.update(site_key, **fields)

    def recover_interrupted(self) -> None:
        """Crawls do not survive a process restart; requeue them immediately."""
        now = iso(utcnow())
        with self._lock:
            try:
                self._conn.execute(
                    """
                    UPDATE sites
                    SET status = 'failed',
                        last_error = 'crawl interrupted by service restart',
                        next_crawl_at = ?,
                        updated_at = ?
                    WHERE status IN ('queued', 'crawling')
                    """,
                    (now, now),
                )
                self._conn.commit()
            except sqlite3.OperationalError:
                # Read-only database on serverless
                pass
