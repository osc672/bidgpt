import os
import shutil
from pathlib import Path

# On Vercel / serverless runtimes, the deployment filesystem is read-only.
# Discover the data folder across potential bundling layouts and copy to /tmp/data
is_serverless = bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
tmp_data = Path("/tmp/data")

candidates = [
    Path(__file__).resolve().parent.parent / "data",
    Path(__file__).resolve().parent / "data",
    Path.cwd() / "data",
    Path("/var/task/data"),
    Path("/var/task/api/data"),
]

repo_data = None
for cand in candidates:
    if (cand / "registry.sqlite").exists():
        repo_data = cand
        break

if repo_data is None:
    # Deep search fallback across bundle base directories
    for base in [Path(__file__).resolve().parent.parent, Path.cwd(), Path("/var/task")]:
        if base.exists():
            try:
                for p in base.rglob("registry.sqlite"):
                    repo_data = p.parent
                    break
            except Exception:
                pass
            if repo_data:
                break

if is_serverless:
    os.environ["PLAYWRIGHT_ENABLED"] = "false"
    if repo_data is not None:
        try:
            tmp_data.mkdir(parents=True, exist_ok=True)
            if not (tmp_data / "registry.sqlite").exists() or not (tmp_data / "sites").exists():
                shutil.copytree(repo_data, tmp_data, dirs_exist_ok=True)
        except Exception:
            pass

    if (tmp_data / "registry.sqlite").exists():
        os.environ["BIDGPT_DATA_DIR"] = str(tmp_data)
    elif repo_data is not None:
        os.environ["BIDGPT_DATA_DIR"] = str(repo_data)

from app.config import Settings
from app.main import create_app

settings = Settings.from_env()
if (tmp_data / "registry.sqlite").exists():
    settings.data_dir = tmp_data
elif repo_data is not None:
    settings.data_dir = repo_data

app = create_app(settings)
