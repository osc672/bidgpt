"""Development launcher with a reload scope that ignores runtime data.

`uvicorn app.main:app --reload` watches the whole working directory by default.
BidGPT writes crawl data (SQLite registry, per-site indexes) inside `data/` while
requests are in flight, so a reload triggered by those writes can restart the
server mid-request and the browser reports "Failed to fetch".

Watching only `app/` and `static/` keeps code reloads working without restarting
on crawl output. Use this script for local development:

    python run.py

For production, run without reload (see the Dockerfile / README).
"""

from __future__ import annotations

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        reload_dirs=["app", "static"],
    )
