# BidGPT

BidGPT is a tender-search assistant that any website can embed with a single
`<script>` tag. It crawls the website **once** (and re-crawls on a schedule),
builds a private per-site page index, and answers visitor questions from that
index only. At question time it never queries an external tender database.

- Backend: Python 3.11+, FastAPI
- Crawler: `httpx` + BeautifulSoup, Playwright for JavaScript pages, `pypdf` for PDFs
- Index: one immutable SQLite FTS5 file per site, loaded into memory for search
- LLM: one tool only, `search_pages(location, type, keyword)`, with a grounded
  deterministic fallback when no LLM key is configured
- Widget: dependency-free `widget.js`, accessible, light/dark, source links
- MCP: stdio server exposing the same `search_pages` tool

See [ARCHITECTURE.md](ARCHITECTURE.md) for the two-phase workflow and the
atomic index-swap design.

## Quick start

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
# source .venv/bin/activate

pip install -r requirements.txt
python -m playwright install chromium   # needed for JS-rendered pages
cp .env.example .env                    # Windows: copy .env.example .env
python run.py                           # http://127.0.0.1:8000
```

`run.py` is a development launcher that reloads only `app/` and `static/`.
Running `uvicorn app.main:app --reload` directly watches the whole directory,
including `data/`, and a crawl writing its index can restart the server
mid-request (the browser then reports "Failed to fetch"). If you prefer the
CLI, limit the watched directories:

```bash
uvicorn app.main:app --reload --reload-dir app --reload-dir static --port 8000
```

For production, run without `--reload`.

Open <http://localhost:8000/> for the setup page, <http://localhost:8000/docs>
for the OpenAPI UI, or <http://localhost:8000/demo> for the demo page. The JSON
service description is at <http://localhost:8000/info>.

Playwright's browser cache can be large. If the system drive is small, install
the browser on another drive and point both the installer and the app at it:

```powershell
# PowerShell
$env:PLAYWRIGHT_BROWSERS_PATH="D:\bidgpt\.playwright"
python -m playwright install chromium
```

The app automatically uses `./.playwright` when that directory exists and
`PLAYWRIGHT_BROWSERS_PATH` is not set. The tests do the same. Keep the variable
in `.env` for a persistent choice; `python -m playwright install` itself does
not read `.env`.

## Optional Firecrawl backend

[Firecrawl](https://www.firecrawl.dev) can crawl JavaScript-heavy or
bot-protected public sites for you. BidGPT still stores the returned pages in
its own per-site index; Firecrawl is contacted only during a crawl, never when a
visitor asks a question.

1. Create an API key at <https://www.firecrawl.dev>.
2. Add it to `.env`:
   ```env
   CRAWLER_BACKEND=auto
   FIRECRAWL_API_KEY=fc-...
   ```
3. Restart the app. `auto` uses Firecrawl whenever a key is present. Set
   `CRAWLER_BACKEND=local` to force the built-in crawler, or `firecrawl` to
   require Firecrawl.

Notes:

- Loopback and private hosts always use the local crawler (Firecrawl cannot
  reach them), so tests and the local demo are unaffected.
- Firecrawl cannot bypass logins. Only public pages are indexed, and
  login-protected URLs are skipped.
- Firecrawl usage is billed per page/credit. Keep `MAX_PAGES` and
  `CRAWL_DEPTH` modest when trying it out.
- `GET /info` reports whether Firecrawl is configured.

## Register a site and embed the widget

Registration kicks off a background crawl immediately.

```bash
curl -X POST http://localhost:8000/sites \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.example.gov.in/tenders"}'
```

```json
{
  "site_key": "sk_8f2c1a9b0d4e6f7a8b9c",
  "url": "https://www.example.gov.in/tenders",
  "status": "queued",
  "created": true,
  "status_url": "http://localhost:8000/sites/sk_8f2c1a9b0d4e6f7a8b9c/status",
  "widget_snippet": "<script src=\"http://localhost:8000/widget.js\" data-site=\"sk_8f2c1a9b0d4e6f7a8b9c\"></script>"
}
```

Registering the same normalized URL again returns `200` with the existing key.
If that site previously failed, registration also retries the crawl.

Poll the status endpoint until `status` is `ready` and `has_index` is `true`:

```bash
curl http://localhost:8000/sites/sk_8f2c1a9b0d4e6f7a8b9c/status
```

Add the snippet returned by the API to the registered website:

```html
<script src="https://YOUR-DOMAIN/widget.js" data-site="SITE_KEY"></script>
```

The widget shows “Getting ready” (and keeps polling) until the site has an
index. During a scheduled re-crawl it keeps answering from the previous index.

Widget attributes:

| Attribute | Required | Meaning |
| --- | --- | --- |
| `data-site` | yes | Site key returned by `POST /sites` |
| `data-api` | no | API origin; defaults to the origin serving `widget.js` |
| `data-theme` | no | `light` or `dark`; defaults to the visitor's system theme |

Demo: after registering a site, open
`http://localhost:8000/demo?site=SITE_KEY`. The home page at `/` also offers a
small setup form that registers a URL and opens the widget automatically.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Setup/demo UI |
| `POST` | `/sites` | Register a website and start the first crawl |
| `GET` | `/sites/{site_key}/status` | Crawl status, page counts, active index metadata |
| `POST` | `/sites/{site_key}/recrawl` | Queue a manual re-crawl (`202`, or `409` if running) |
| `POST` | `/chat` | Ask a grounded tender question |
| `GET` | `/tools/claude` | Anthropic tool definition |
| `GET` | `/tools/openai` | OpenAI function/tool definition |
| `GET` | `/health` | Service health check |
| `GET` | `/info` | JSON service description and endpoint list |
| `GET` | `/widget.js` | Embeddable widget script |
| `GET` | `/demo` | Demo embed page |

### `POST /chat`

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -H "Origin: https://www.example.gov.in" \
  -d '{
    "site_key": "sk_8f2c1a9b0d4e6f7a8b9c",
    "message": "Guntur electrical tenders",
    "history": [
      {"role": "user", "content": "Hi"},
      {"role": "assistant", "content": "Which type of tender and which location are you looking for?"}
    ]
  }'
```

```json
{
  "answer": "I found 1 tender for Electrical Works in Guntur, Andhra Pradesh on example.gov.in. Details are below.",
  "tenders": [
    {
      "title": "Supply and Installation of LED Street Lights",
      "type": "Electrical Works",
      "location": "Guntur, Andhra Pradesh",
      "budget": "Rs. 12,50,000",
      "timeline": "90 days",
      "products": "LED street light fittings, poles and installation",
      "department": "Guntur Municipal Corporation",
      "bid_by_date": "2026-10-31",
      "url": "https://www.example.gov.in/tenders/guntur-electrical.html",
      "source": "https://www.example.gov.in/tenders/guntur-electrical.html",
      "snippet": "…",
      "crawled_at": "2026-10-30T09:15:00+00:00",
      "score": 10.0,
      "matched": ["location", "type"],
      "prompt_injection_detected": false
    }
  ],
  "sources": ["https://www.example.gov.in/tenders/guntur-electrical.html"],
  "status": "ready",
  "missing": [],
  "intent": {
    "location": "Guntur, Andhra Pradesh",
    "type": "Electrical Works",
    "keyword": null,
    "generic_tender": true,
    "used_history": false
  }
}
```

Rules enforced by the chat service:

- A greeting asks for tender type and location.
- Missing location/type produces a short follow-up question instead of a guess.
- Matching is case-insensitive and tolerant of spelling variants
  (`Guntur` / `Guntoor`, `Vijayawada` / `Vijaywada`).
- One card is returned per indexed page/tender, with the title, type, location,
  budget, timeline, products/scope, department, bid-by date and source link.
  Any field absent from the page is returned as `"not listed on the page"`.
- No match returns an honest “No tenders matching …” answer; nothing is invented.
- The LLM is forced to call `search_pages` first and may only answer from the
  returned pages. Without an LLM key, deterministic grounded answers are used.
- `history` is limited to 20 messages and is sanitized before use.

## Configuration

Copy `.env.example` to `.env`. All values are read from the environment at
startup.

| Variable | Default | Description |
| --- | --- | --- |
| `BIDGPT_DATA_DIR` | `./data` | Registry and per-site index storage |
| `CRAWL_DEPTH` | `2` | Maximum link depth from the registered URL |
| `MAX_PAGES` | `100` | Maximum pages fetched per crawl |
| `RECRAWL_INTERVAL_SECONDS` | `21600` | Scheduled re-crawl interval (6 hours) |
| `REQUEST_DELAY_SECONDS` | `0.5` | Politeness delay between requests |
| `REQUEST_TIMEOUT_SECONDS` | `20` | Per-request timeout |
| `MAX_RESPONSE_BYTES` | `8000000` | HTML/PDF response size cap |
| `MAX_PDF_BYTES` | `15000000` | PDF size cap |
| `USER_AGENT` | `BidGPTBot/1.0 …` | Crawler user agent |
| `PLAYWRIGHT_ENABLED` | `true` | Enable JavaScript rendering |
| `PLAYWRIGHT_WAIT_MS` | `750` | Extra wait for JS-rendered content |
| `PLAYWRIGHT_BROWSERS_PATH` | repo `./.playwright` if present | Custom Playwright browser cache directory |
| `ALLOW_PRIVATE_URLS` | `false` | Allow loopback/private crawl targets (local demos/tests only) |
| `CRAWLER_BACKEND` | `auto` | `auto`, `local`, or `firecrawl` |
| `FIRECRAWL_API_KEY` | empty | Firecrawl API key; enables the Firecrawl backend |
| `FIRECRAWL_API_URL` | `https://api.firecrawl.dev/v2` | Firecrawl API base URL |
| `FIRECRAWL_TIMEOUT_SECONDS` | `300` | Maximum wait for one Firecrawl crawl job |
| `FIRECRAWL_POLL_SECONDS` | `2` | Poll interval while a Firecrawl job runs |
| `PUBLIC_BASE_URL` | request origin | Public URL used in widget snippets behind a proxy |
| `LLM_PROVIDER` | `openai` | `openai` (OpenAI-compatible) or `anthropic` |
| `LLM_API_KEY` | empty | Enables LLM answers when set (or `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`) |
| `LLM_MODEL` | provider default | Model name; defaults to `gpt-4o-mini` or `claude-3-5-haiku-latest` |
| `LLM_BASE_URL` | provider default | Custom compatible endpoint |
| `LLM_TIMEOUT_SECONDS` | `30` | LLM request timeout |
| `CHAT_RATE_LIMIT_PER_IP` | `20` | Chat requests per IP per window |
| `CHAT_RATE_LIMIT_PER_SITE` | `60` | Chat requests per site per window |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Rate-limit window |
| `ADMIN_API_KEY` | empty | When set, required as `X-Admin-Key` for registration and re-crawl |
| `TRUST_PROXY` | `false` | Trust `X-Forwarded-For` (only behind a trusted proxy) |
| `WIDGET_EXTRA_ORIGINS` | empty | Extra comma-separated origins allowed to call the API |
| `LOG_LEVEL` | `INFO` | Python log level |

## MCP server

The MCP server exposes the same one-tool contract over newline-delimited
JSON-RPC 2.0 on stdio:

```bash
python -m app.mcp
```

Example client configuration (run from the repository root):

```json
{
  "mcpServers": {
    "bidgpt": {
      "command": "python",
      "args": ["-m", "app.mcp"],
      "env": { "BIDGPT_DATA_DIR": "D:/bidgpt/data" }
    }
  }
}
```

Tool: `search_pages(site_key, location, type, keyword)`. It reads the same
immutable index files as the HTTP API and never writes to them. Point
`BIDGPT_DATA_DIR` at the same directory the API uses (or a read-only copy).

## Tests

```bash
python -m pytest -q
```

The suite starts a local fake tender site with three pages (a normal HTML page,
a JavaScript-rendered page and a PDF), crawls it with Playwright, checks field
extraction and Guntur/Guntoor/unknown-place search, then stops the site and
blocks outbound sockets to prove Phase 2 answers come only from the index.
A re-crawl test adds a fourth page and verifies the index version changes and
the new tender becomes searchable only after the swap. Additional tests cover
rate limiting and the MCP tool.

Chromium must be installed for the JS-page test:

```bash
python -m playwright install chromium
```

## Docker

```bash
cp .env.example .env        # set PUBLIC_BASE_URL, LLM_API_KEY, ADMIN_API_KEY
docker compose up -d --build
curl http://localhost:8000/health
```

The image installs Chromium and its OS dependencies. Compose mounts `./data`
at `/data`, so the registry and indexes survive container rebuilds. The
container includes a health check against `/health`.

Deployment notes:

- Put BidGPT behind HTTPS (Caddy, nginx, a cloud load balancer, etc.) and set
  `PUBLIC_BASE_URL` to that public origin so widget snippets are correct.
- Only set `TRUST_PROXY=true` when a trusted reverse proxy overwrites
  `X-Forwarded-For`; otherwise clients can spoof their IP for rate limiting.
- Set `ADMIN_API_KEY` before exposing registration publicly.
- Keep `ALLOW_PRIVATE_URLS=false` in production. It exists for local demos and
  tests and is an SSRF guard.
- The `./data` directory contains the site registry (`registry.sqlite`) and
  read-only per-site indexes (`sites/<site_key>/indexes/*.sqlite`). Back it up
  as one unit.

## Project layout

```text
app/
  main.py       FastAPI app, routes, CORS/origin checks, rate limits
  config.py     Environment-driven settings
  crawler.py    Same-domain crawler, robots.txt, Playwright, PDFs, redirects
  extract.py    HTML/PDF tender-field extraction heuristics
  index.py      PageRecord, FTS5 build, in-memory search, atomic index manager
  registry.py   SQLite site registry (metadata only)
  crawl.py      Background crawl manager, atomic swap, scheduler
  chat.py       Intent parsing, missing-field prompts, grounded answers
  llm.py        Forced tool-call loop for OpenAI-compatible and Anthropic APIs
  security.py   Sanitization, prompt-injection handling, rate limiter
  urls.py       URL normalization, same-site checks, SSRF guard
  tools.py      The single search_pages tool schema
  mcp.py        MCP stdio server
static/
  widget.js     Embeddable widget
  demo.html     Setup/demo page served at / and /demo
tests/
  fake_site.py  Local 3-page site (HTML + JS page + PDF)
  ...           Crawler, extraction, search, Phase 2, re-crawl, MCP tests
```

## Security and limits

- **SSRF guard:** registration rejects loopback, private, link-local and
  credential-bearing URLs unless `ALLOW_PRIVATE_URLS=true`.
- **Same-domain crawl:** links are restricted to the registered host (with
  `www.` equivalence); redirects are checked before following; login-protected
  URLs are skipped rather than authenticated.
- **Robots.txt:** fetched and enforced; crawl delay is respected.
- **Politeness:** per-request delay, timeout, response-size and page-count caps.
- **Untrusted content:** crawled text is sanitized and truncated before it
  reaches the LLM. Page text is wrapped as untrusted data, prompt-injection
  patterns are logged, and the system prompt forbids following page instructions.
- **Grounded answers:** the LLM has exactly one tool and is forced to call it
  first; missing fields are never inferred from outside knowledge.
- **Origin control:** browser requests from an unregistered origin are rejected
  with `403`; CORS is granted only to the registered domain, the API's own
  origin (used by the bundled demo), and `WIDGET_EXTRA_ORIGINS`.
- **Rate limits:** per-IP and per-site chat limits with `Retry-After`.
- **Admin key:** optional protection for registration and re-crawl.
- **Atomic updates:** a crawl failure never deletes the last good index.

## Assumptions

- “Integration link” means the `<script>` embed snippet; there is no separate
  admin UI or integration button.
- One indexed page is one tender card. Pages listing many tenders in a table
  are not split into multiple cards.
- The crawler does not log in and does not crawl authenticated areas.
- The service stores tender text only in its own per-site index files; the site
  registry contains metadata and status, not tender records.
- Tender fields are extracted heuristically from the page. When the page does
  not show a field, the answer says `"not listed on the page"` rather than
  guessing.
- A scheduled re-crawl runs every 6 hours by default and keeps serving the
  previous index until the new one is complete.
