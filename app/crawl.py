"""Background crawl orchestration and atomic index swaps."""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

from .config import Settings
from .crawler import Crawler
from .firecrawl import FirecrawlCrawler
from .index import IndexManager, build_index
from .registry import Registry, utcnow
from .urls import host_is_private

log = logging.getLogger("bidgpt.crawl")


def build_crawler(settings: Settings, url: str) -> Crawler | FirecrawlCrawler:
    """Pick a crawl backend for one site.

    `auto` uses Firecrawl when a key is configured, except for private and
    loopback hosts (tests, the local fake site) where Firecrawl cannot reach the
    target and the built-in crawler is correct.
    """
    if settings.crawler_backend == "firecrawl":
        return FirecrawlCrawler(settings)
    if settings.firecrawl_configured:
        host = urlparse(url).hostname or ""
        if not host_is_private(host):
            return FirecrawlCrawler(settings)
    return Crawler(settings)


class CrawlManager:
    def __init__(
        self,
        settings: Settings,
        registry: Registry,
        index_manager: IndexManager,
    ):
        self.settings = settings
        self.registry = registry
        self.index_manager = index_manager
        self._tasks: dict[str, asyncio.Task] = {}

    def is_running(self, site_key: str) -> bool:
        task = self._tasks.get(site_key)
        return task is not None and not task.done()

    def start(self, site_key: str, *, reason: str = "manual") -> bool:
        site = self.registry.get(site_key)
        if site is None:
            return False
        if self.is_running(site_key):
            return False
        self.registry.update(site_key, status="queued", last_error=None)
        task = asyncio.create_task(self._run(site_key, reason))
        self._tasks[site_key] = task
        task.add_done_callback(lambda finished: self._finished(site_key, finished))
        return True

    def _finished(self, site_key: str, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.exception("crawl task crashed for %s", site_key, exc_info=exc)
            try:
                self.registry.mark_failed(site_key, f"crawl crashed: {exc}")
            except Exception:
                log.exception("could not mark crashed crawl failed")

    async def _run(self, site_key: str, reason: str) -> None:
        site = self.registry.get(site_key)
        if site is None:
            return
        log.info("crawl starting site=%s reason=%s url=%s", site_key, reason, site.url)
        self.registry.set_crawl_started(site_key)

        def progress(count: int) -> None:
            # Keep the public status useful while the crawl is in flight.
            self.registry.update(site_key, pages_found=count)

        crawler = await asyncio.to_thread(build_crawler, self.settings, site.url)
        log.info(
            "crawl backend site=%s backend=%s",
            site_key,
            type(crawler).__name__,
        )
        try:
            result = await crawler.crawl(site.url, progress=progress)
            errors = [error.public() for error in result.errors]
            if not result.pages:
                message = "No pages could be indexed."
                if result.errors:
                    message = f"No pages could be indexed. First error: {result.errors[0].error}"
                self.registry.mark_failed(
                    site_key,
                    message,
                    errors=errors,
                    interval_seconds=min(self.settings.recrawl_interval_seconds, 3600),
                )
                log.warning("crawl failed site=%s: %s", site_key, message)
                return

            site_dir = self.settings.site_dir(site_key)
            active = await asyncio.to_thread(build_index, site_dir, result.pages)
            self.registry.mark_ready(
                site_key,
                pages_found=len(result.pages),
                active_index=active["file"],
                errors=errors,
                interval_seconds=self.settings.recrawl_interval_seconds,
            )
            self.index_manager.invalidate(site_key)
            log.info(
                "crawl ready site=%s pages=%s errors=%s version=%s",
                site_key,
                len(result.pages),
                len(errors),
                active["version"],
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("crawl failed site=%s", site_key)
            self.registry.mark_failed(
                site_key,
                f"{type(exc).__name__}: {exc}",
                interval_seconds=min(self.settings.recrawl_interval_seconds, 3600),
            )

    async def stop(self) -> None:
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()


class CrawlScheduler:
    """Checks due sites periodically; recrawl interval is configurable."""

    def __init__(self, settings: Settings, registry: Registry, manager: CrawlManager):
        self.settings = settings
        self.registry = registry
        self.manager = manager
        self._task: asyncio.Task | None = None

    async def run(self) -> None:
        # Give the API a moment to finish starting, then check immediately.
        while True:
            try:
                for site in self.registry.due_sites(utcnow()):
                    if self.manager.start(site.site_key, reason="schedule"):
                        log.info("scheduled recrawl site=%s", site.site_key)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("scheduler iteration failed")
            await asyncio.sleep(30)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
