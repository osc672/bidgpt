# BidGPT architecture

BidGPT is deliberately split into two phases:

1. **Crawl and index** — happens when a site is registered and on a schedule.
2. **Answer** — happens when a visitor asks a question and reads only the
   per-site index built in phase 1.

No external tender database is consulted in phase 2.

```text
PHASE 1 — CRAWL ONCE PER SITE (registration + scheduled re-crawl)

  POST /sites  ──► validate URL + SSRF guard ──► registry row (queued)
                                                       │
                                                       ▼
                                      background crawl task (asyncio)
                                                       │
              ┌────────────────────────────────────────┼──────────────────────────────┐
              │                                        │                              │
              ▼                                        ▼                              ▼
      robots.txt fetch                       httpx + BeautifulSoup             Playwright (only for
      (allow/deny, delay)                    HTML / text / redirects           JS-shell pages)
              │                                        │                              │
              └────────────────────────────┬───────────┴──────────────────────────────┘
                                           ▼
                                 same-domain link queue
                                 depth / page / size limits
                                 per-request politeness delay
                                           │
                                           ▼
                         field extraction + sanitization + hashing
                         (title, type, location, budget, timeline,
                          products/scope, department, bid-by date)
                                           │
                                           ▼
                     build complete SQLite FTS5 index in a temp file
                     data/sites/<site_key>/indexes/index-<version>.sqlite
                                           │
                                           ▼
                     atomic rename + atomic active.json pointer swap
                                           │
                                           ▼
                         registry status = ready, index version set


PHASE 2 — ANSWER FROM THE PER-SITE INDEX (no crawler, no tender DB)

  widget / API client
        │  POST /chat {site_key, message, history}
        ▼
  origin check ──► per-IP limit ──► per-site limit
        │
        ▼
  IndexManager loads active index into memory (read-only SQLite)
        │
        ▼
  intent parsing: location, tender type, optional keyword
        │
        ├── greeting / missing field ──► short follow-up question
        │
        ▼
  search_pages(location, type, keyword)
        │  location/type matching: case-insensitive + typo-tolerant
        │  keyword matching: every token must match the page
        │  scoring favours title/products/department/text, FTS5 bonus
        ▼
  matching PageRecords (only from this site's index)
        │
        ├── LLM configured ──► forced tool call ──► grounded intro sentence
        │                      (provider failure falls back below)
        └── no LLM ─────────► deterministic grounded sentence
        │
        ▼
  response: answer + one card per tender + source URLs
```

## Components

| Component | File | Responsibility |
| --- | --- | --- |
| API | `app/main.py` | Routes, CORS/origin checks, rate limits, static widget/demo |
| Settings | `app/config.py` | Environment configuration |
| Crawler | `app/crawler.py` | Same-domain BFS crawl, robots.txt, redirects, Playwright, PDF |
| Firecrawl backend | `app/firecrawl.py` | Optional managed crawl; same `CrawlResult`/`PageRecord` output |
| Extraction | `app/extract.py` | Tender fields from HTML tables/labels and PDF text |
| Index | `app/index.py` | FTS5 build, in-memory search, fuzzy matching, index manager |
| Registry | `app/registry.py` | Site metadata and crawl status only |
| Crawl orchestration | `app/crawl.py` | Background tasks, atomic swap, scheduled re-crawl |
| Chat | `app/chat.py` | Intent, missing-field prompts, deterministic answers |
| LLM | `app/llm.py` | Forced `search_pages` tool loop (OpenAI/Anthropic) |
| Security | `app/security.py` | Sanitization, injection handling, rate limiter |
| URLs | `app/urls.py` | Normalization, same-site checks, SSRF guard |
| Tools/MCP | `app/tools.py`, `app/mcp.py` | One-tool contract over HTTP schemas and MCP stdio |
| Widget | `static/widget.js` | Embeddable UI, polling, cards, source links |
| Setup UI | `static/demo.html` | Registration/demo page served at `/` and `/demo` |

## Storage layout

```text
data/
  registry.sqlite                         # site metadata/status, no tender content
  sites/
    <site_key>/
      active.json                         # tiny pointer: {file, version, built_at, page_count}
      indexes/
        index-20261030T091500-1a2b3c4d.sqlite   # immutable live generation
        index-20261030T031500-9e8f7a6b.sqlite   # previous generation (kept for in-flight reads)
```

- **Registry:** one row per registered site: normalized URL, domain, status
  (`queued` / `crawling` / `ready` / `failed`), page counts, timestamps, the
  active index filename and a bounded per-URL error list.
- **Index:** `pages` table with the extracted fields and full sanitized page
  text, plus a `pages_fts` FTS5 virtual table. The FTS table is used as a
  keyword candidate bonus; final matching is deterministic and in memory.
- **No tender data in the registry.** `registry.sqlite` stores metadata only.

## Atomic index swap

A crawl never edits the live index:

1. `build_index()` writes a complete new file to a hidden temp name inside
   `indexes/`.
2. The finished file is renamed to `index-<version>.sqlite`.
3. A temp `active.json` is written and `os.replace()` swaps it in one step.
4. Only after that does the registry mark the site `ready` with the new file
   name and the in-memory cache get invalidated.
5. The newest two generations are retained; older files are deleted. A search
   already running against the previous file can finish safely because the
   file is immutable and still open.
6. If a crawl fails before step 3, the previous `active.json` and index remain
   live. The site may show `failed` with `has_index: true`, and the widget keeps
   answering from the last good index.

`IndexManager` caches one in-memory `SearchIndex` per site and compares the
cached file path with the current `active.json` pointer. A changed pointer
closes the old read-only connection and loads the new generation.

## Crawl policy

- Start URL is normalized (lowercased scheme/host, tracking parameters removed,
  fragment dropped) and deduplicated.
- Only the registered host is crawled (`www.` equivalence allowed).
- `robots.txt` is fetched from the origin; disallowed URLs are skipped and
  `Crawl-delay` is honored (capped at 5 seconds).
- Depth and page limits, response-size and PDF-size limits, and a per-request
  delay bound the crawl.
- Redirects are followed manually up to 6 hops; each hop is re-checked for
  same-site and private-host rules.
- Login-protected URLs are skipped. A page is treated as login-protected only
  when it has a password field *and* a login URL/title or almost no other
  content, so public pages with a small header login form are still indexed.
  The crawler never authenticates.
- A failure for one URL is recorded in the error list and does not abort the
  crawl.
- HTML is parsed with BeautifulSoup/lxml. If the page looks like a JavaScript
  shell (very little text plus script/app markers, or “enable JavaScript”-style
  text), Playwright renders it once. Playwright request routing blocks
  cross-domain and private-network subresources.
- PDFs are fetched only when linked from crawled pages, parsed with `pypdf`,
  and converted into the same `PageRecord` shape.

### Optional Firecrawl backend

When `FIRECRAWL_API_KEY` is set and `CRAWLER_BACKEND` is `auto` (or
`firecrawl`), the crawl phase calls Firecrawl's `/v2/crawl` API instead of the
local fetcher. Firecrawl renders the site and returns markdown and filtered HTML
per page; BidGPT maps those documents into the same `PageRecord` shape and
builds the same immutable per-site index. Answering is unchanged: Phase 2 never
contacts Firecrawl or any tender database. Private/loopback hosts always use the
local crawler. Firecrawl cannot bypass a login, and login URLs are still
skipped.

## Search and matching

`search(location, type, keyword)` requires at least one criterion and returns
up to the requested limit, sorted by score then title.

- **Normalization:** Unicode NFKD, lower case, `&` → `and`, punctuation removed.
- **Location:** compared with the page's location field. If a page has no
  location field, its title/products are used as a fallback; a page that has a
  different explicit location does not leak into results merely because its
  body mentions the requested place.
- **Type:** compared with the page's type field and title. Every token of a
  multi-word type must match; category synonyms in the intent layer map common
  visitor words (for example “electrical”) to index type names.
- **Fuzzy tolerance:** exact match, substring, consonant-skeleton match
  (`Guntur` / `Guntoor` → `gntr`), then `difflib` ratio, with a `0.82`
  threshold.
- **Keyword:** every meaningful token must reach the threshold against the
  page; tokens are weighted title > products > department > body text. Pages
  that match only some keywords are excluded.
- **FTS5:** provides a candidate bonus for keyword searches; it never overrides
  the strict location/type filters.

## Chat contract

1. Greeting with no type/location → “Which type of tender and which location
   are you looking for?”
2. Missing type or location → a short follow-up question. Recent user turns are
   used to resolve follow-ups such as “Guntur” → “electrical”.
3. Otherwise → `search_pages` over the site index.
4. If an LLM is configured, its first response is forced to be a `search_pages`
   tool call. Tool arguments are merged with the parsed intent so the model
   cannot silently widen the search to another site. Page text is sent as
   explicitly untrusted data with instruction-like spans redacted.
5. The model writes at most a short intro; the server returns one structured
   card per matched page. If the tool returns nothing, cards are cleared so the
   text and cards agree.
6. Without an LLM key, or if the provider fails/times out, the deterministic
   formatter produces the same grounded style of answer.

Source links are restricted to `http`/`https` and always point at pages that
were actually crawled and indexed.

## Security model

- **SSRF:** private/loopback/link-local hosts rejected at registration and
  re-checked for redirects, discovered links, and Playwright subresources.
  `ALLOW_PRIVATE_URLS` exists only for local demos/tests.
- **Origin control:** `/chat` and `/sites/{key}/status` reject a browser
  `Origin` that does not match the registered domain, the API's own origin (for
  the bundled demo), or an explicitly allowed extra origin. CORS headers are
  emitted only for those origins.
- **Rate limiting:** in-process sliding windows for per-IP and per-site chat,
  returning `429` with `Retry-After`. For multi-replica deployments, put a
  shared limiter at the edge.
- **Input handling:** message length is bounded by the request model; crawled
  and user text is stripped of control characters, markup-like tags and
  instruction-like spans before reaching an LLM.
- **Admin:** optional `ADMIN_API_KEY` (`X-Admin-Key`) on registration and
  re-crawl.
- **Least data:** the LLM receives only bounded snippets of matching pages,
  never the registry or the whole crawl.

## Failure behavior

| Failure | Behavior |
| --- | --- |
| Registration URL invalid/private | `422`, no crawl |
| No pages indexed | `status=failed`, old index (if any) kept, retry in ≤1 hour |
| Some URLs fail | Crawl succeeds; errors recorded per URL in status |
| Crawl crashes/restarts | Startup marks interrupted crawls failed and the scheduler retries |
| LLM missing/failing | Deterministic grounded answer; no error to the visitor |
| Index missing | Chat says the index is still getting ready |
| Re-crawl in progress | `409`; existing index remains searchable |
| Unknown place/type | Honest empty result, no invented tenders |

## Scaling notes

- The current design is a single-process app: in-memory index cache,
  in-process rate limiter, and a SQLite registry.
- Per-site indexes are independent, so a future deployment can shard sites
  across workers by site key.
- For multiple API replicas, use a shared rate limiter and ensure all replicas
  see the same `data/` volume (or move `active.json`/indexes to object storage
  with a shared metadata store).
- The scheduler runs inside the API process. Running multiple schedulers would
  duplicate recrawls; in a multi-replica deployment keep the scheduler on one
  instance or move it to a dedicated worker.
