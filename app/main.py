"""FastAPI application: site registration, crawl status, chat and tools."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from .chat import ChatService
from .config import Settings
from .crawl import CrawlManager, CrawlScheduler
from .index import IndexManager
from .llm import LLMClient
from .models import ChatRequest, SiteCreate
from .registry import Registry
from .security import SlidingWindowRateLimiter
from .tools import claude_tool, openai_tool
from .urls import normalize_url, origin_matches, validate_site_url

log = logging.getLogger("bidgpt.api")
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _cors_headers(origin: str) -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": origin,
        "Vary": "Origin",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, X-Admin-Key",
        "Access-Control-Max-Age": "600",
    }


def _client_ip(request: Request, settings: Settings) -> str:
    if settings.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    registry = Registry(settings.registry_path)
    index_manager = IndexManager(settings.data_dir)
    crawl_manager = CrawlManager(settings, registry, index_manager)
    scheduler = CrawlScheduler(settings, registry, crawl_manager)
    chat_service = ChatService(settings, registry, index_manager, LLMClient(settings))
    limiter = SlidingWindowRateLimiter(settings.rate_limit_window_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            settings.sites_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        try:
            registry.recover_interrupted()
        except Exception as e:
            log.warning("Could not recover interrupted crawls: %s", e)
        is_serverless = bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
        if not is_serverless:
            scheduler.start()
        try:
            yield
        finally:
            if not is_serverless:
                try:
                    await scheduler.stop()
                    await crawl_manager.stop()
                except Exception:
                    pass
            index_manager.close()
            registry.close()

    app = FastAPI(
        title="BidGPT",
        version="1.0.0",
        description="Crawl-once, search-your-own-site tender assistant.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.registry = registry
    app.state.index_manager = index_manager
    app.state.crawl_manager = crawl_manager
    app.state.chat_service = chat_service
    app.state.limiter = limiter

    def is_self_origin(request: Request, origin: str | None) -> bool:
        """Allow the API's own origin so the bundled demo works same-origin."""
        if not origin:
            return False
        cleaned = origin.rstrip("/")
        candidates = {str(request.base_url).rstrip("/")}
        if settings.public_base_url:
            candidates.add(settings.public_base_url.rstrip("/"))
        return cleaned in candidates

    def origin_known(origin: str | None, request: Request) -> bool:
        if not origin:
            return False
        if is_self_origin(request, origin):
            return True
        if any(origin.rstrip("/") == item.rstrip("/") for item in settings.widget_extra_origins):
            return True
        return any(
            origin_matches(origin, site.domain, settings.widget_extra_origins)
            for site in registry.list_all()
        )

    @app.middleware("http")
    async def vercel_path_rewrite(request: Request, call_next):
        matched = (
            request.headers.get("x-matched-path")
            or request.headers.get("x-invoke-path")
            or request.headers.get("x-forwarded-uri")
        )
        if matched:
            request.scope["path"] = matched.split("?")[0]
        else:
            path = request.scope.get("path", "")
            if path in ("/api/index.py", "/api/index", "/api", "/api/"):
                request.scope["path"] = "/"
            elif path.startswith("/api/") and path not in ("/api/sites",):
                request.scope["path"] = path[4:]
        return await call_next(request)

    @app.middleware("http")
    async def cors_middleware(request: Request, call_next):
        origin = request.headers.get("origin")
        known = origin_known(origin, request)
        if request.method == "OPTIONS" and request.headers.get("access-control-request-method"):
            if not known:
                return JSONResponse({"detail": "Origin not allowed"}, status_code=403)
            return Response(status_code=204, headers=_cors_headers(origin or ""))
        response = await call_next(request)
        if origin and known:
            for key, value in _cors_headers(origin).items():
                response.headers.setdefault(key, value)
        return response

    def enforce_site_origin(request: Request, domain: str) -> None:
        origin = request.headers.get("origin")
        if (
            origin
            and not is_self_origin(request, origin)
            and not origin_matches(origin, domain, settings.widget_extra_origins)
        ):
            raise HTTPException(status_code=403, detail="Origin is not allowed for this site")

    def require_admin(request: Request) -> None:
        if not settings.admin_api_key:
            return
        supplied = request.headers.get("x-admin-key", "")
        if supplied != settings.admin_api_key:
            raise HTTPException(status_code=401, detail="Invalid admin key")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "bidgpt"}

    @app.get("/v1/models")
    async def v1_models() -> dict[str, object]:
        return {"object": "list", "data": []}

    @app.get("/")
    @app.get("/index.html")
    @app.get("/api")
    @app.get("/api/")
    @app.get("/api/index.py")
    @app.get("/api/index")
    async def root(request: Request):
        matched = (
            request.headers.get("x-matched-path")
            or request.headers.get("x-invoke-path")
            or request.headers.get("x-forwarded-uri")
            or request.headers.get("x-vercel-matched-path")
            or request.url.path
            or ""
        )
        if "search" in matched or "site" in request.query_params or request.url.path.startswith("/search"):
            return await search_page()

        accept = request.headers.get("accept", "")
        if "application/json" in accept and "text/html" not in accept:
            return {
                "service": "BidGPT",
                "status": "ready",
                "widget": "/widget.js",
                "docs": "/docs",
            }
        index_path = STATIC_DIR / "index.html"
        if index_path.exists():
            return FileResponse(
                index_path,
                media_type="text/html; charset=utf-8",
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )
        return {
            "service": "BidGPT",
            "status": "ready",
            "widget": "/widget.js",
            "docs": "/docs",
        }

    @app.get("/search")
    @app.get("/search.html")
    @app.get("/api/search")
    @app.get("/api/search.html")
    async def search_page():
        path = STATIC_DIR / "search.html"
        if not path.exists():
            raise HTTPException(status_code=404, detail="search.html not found")
        return FileResponse(
            path,
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    @app.get("/api/sites")
    @app.get("/sites")
    async def api_sites() -> list[dict[str, object]]:
        return [
            {
                "site_key": s.site_key,
                "url": s.url,
                "domain": s.domain,
                "status": s.status,
                "pages_indexed": s.pages_indexed,
            }
            for s in registry.list_all()
        ]

    @app.get("/info")
    async def info() -> dict[str, object]:
        return {
            "service": "BidGPT",
            "version": "1.0.0",
            "workflow": "crawl once per site, then answer from the per-site page index only",
            "crawler": {
                "backend": settings.crawler_backend,
                "firecrawl_configured": settings.firecrawl_configured,
            },
            "endpoints": [
                "POST /sites",
                "GET /sites/{key}/status",
                "POST /sites/{key}/recrawl",
                "POST /chat",
                "GET /tools/claude",
                "GET /tools/openai",
            ],
        }

    @app.post("/sites", status_code=201)
    @app.post("/api/sites", status_code=201)
    async def register_site(body: SiteCreate, request: Request):
        require_admin(request)
        try:
            # DNS lookups and the SSRF check are blocking calls; keep them off
            # the event loop so a slow resolver cannot stall other requests.
            url, domain = await asyncio.to_thread(
                validate_site_url,
                body.url,
                allow_private=settings.allow_private_urls,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        existing = registry.get_by_url(url)
        if existing is not None:
            if existing.status == "failed" and not crawl_manager.is_running(existing.site_key):
                crawl_manager.start(existing.site_key, reason="register-retry")
            return JSONResponse(
                status_code=200,
                content=_registration_payload(request, existing.site_key, url, created=False),
            )

        site = registry.create(url, domain)
        crawl_manager.start(site.site_key, reason="register")
        return JSONResponse(
            status_code=201,
            content=_registration_payload(request, site.site_key, url, created=True),
        )

    def _registration_payload(
        request: Request, site_key: str, url: str, *, created: bool
    ) -> dict[str, object]:
        base = settings.public_base_url or str(request.base_url).rstrip("/")
        site = registry.get(site_key)
        return {
            "site_key": site_key,
            "url": url,
            "status": site.status if site else "queued",
            "created": created,
            "status_url": f"{base}/sites/{site_key}/status",
            "widget_snippet": (
                f'<script src="{base}/widget.js" data-site="{site_key}"></script>'
            ),
        }

    @app.get("/sites/{site_key}/status")
    @app.get("/api/sites/{site_key}/status")
    async def site_status(site_key: str, request: Request) -> dict[str, object]:
        site = registry.get(site_key)
        if site is None:
            raise HTTPException(status_code=404, detail="Unknown site key")
        enforce_site_origin(request, site.domain)
        payload = site.public()
        payload["index"] = index_manager.active_info(site_key)
        return payload

    @app.post("/sites/{site_key}/recrawl", status_code=202)
    @app.post("/api/sites/{site_key}/recrawl", status_code=202)
    async def recrawl(site_key: str, request: Request):
        require_admin(request)
        site = registry.get(site_key)
        if site is None:
            raise HTTPException(status_code=404, detail="Unknown site key")
        if crawl_manager.is_running(site_key):
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "A crawl is already running for this site",
                    "status": site.status,
                    "site_key": site_key,
                },
            )
        crawl_manager.start(site_key, reason="recrawl")
        return {"site_key": site_key, "status": "queued"}

    @app.post("/chat")
    @app.post("/api/chat")
    async def chat(body: ChatRequest, request: Request):
        site = registry.get(body.site_key)
        if site is None:
            raise HTTPException(status_code=404, detail="Unknown site key")
        enforce_site_origin(request, site.domain)

        ip = _client_ip(request, settings)
        allowed_ip, retry_ip = limiter.allow(
            f"ip:{ip}", settings.chat_rate_limit_per_ip
        )
        if not allowed_ip:
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(retry_ip)},
                content={"detail": "Too many chat requests. Please slow down."},
            )
        allowed_site, retry_site = limiter.allow(
            f"site:{body.site_key}", settings.chat_rate_limit_per_site
        )
        if not allowed_site:
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(retry_site)},
                content={"detail": "Too many chat requests for this site. Please slow down."},
            )

        history = [item.model_dump() for item in body.history]
        try:
            outcome = await chat_service.answer(body.site_key, body.message, history)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown site key") from exc
        return outcome.public()

    @app.get("/tools/claude")
    async def tools_claude() -> dict[str, object]:
        return {"tools": [claude_tool()]}

    @app.get("/tools/openai")
    async def tools_openai() -> dict[str, object]:
        return {"tools": [openai_tool()]}

    @app.get("/widget.js")
    @app.get("/api/widget.js")
    async def widget_js():
        path = STATIC_DIR / "widget.js"
        if not path.exists():
            raise HTTPException(status_code=404, detail="widget.js not found")
        return FileResponse(
            path,
            media_type="application/javascript; charset=utf-8",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Cross-Origin-Resource-Policy": "cross-origin",
            },
        )

    return app


app = create_app()
