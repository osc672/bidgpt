import os
import shutil
from pathlib import Path

# On Vercel / serverless runtimes, the deployment filesystem is read-only.
# Copy data to /tmp/data so SQLite can open, lock, and read without EROFS.
is_serverless = bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
if is_serverless:
    tmp_data = Path("/tmp/data")
    repo_data = Path(__file__).resolve().parent.parent / "data"
    if not (tmp_data / "registry.sqlite").exists() and repo_data.exists():
        try:
            shutil.copytree(repo_data, tmp_data, dirs_exist_ok=True)
        except Exception:
            pass
    if (tmp_data / "registry.sqlite").exists():
        os.environ["BIDGPT_DATA_DIR"] = str(tmp_data)
    os.environ["PLAYWRIGHT_ENABLED"] = "false"

from app.main import create_app

app = create_app()
