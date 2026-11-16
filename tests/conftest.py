from __future__ import annotations

import os
from pathlib import Path

# Playwright's default browser cache may be unavailable on constrained CI/dev
# machines. If the repo-local cache exists, point Playwright at it.
_repo_playwright = Path(__file__).resolve().parent.parent / ".playwright"
if _repo_playwright.exists() and not os.getenv("PLAYWRIGHT_BROWSERS_PATH"):
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(_repo_playwright)
