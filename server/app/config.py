import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("MODEL3D_DATA", ROOT / "data")).resolve()
JOBS_DIR = DATA_DIR / "jobs"
WEB_DIST = ROOT / "web" / "dist"
TOKEN = os.environ.get("MODEL3D_TOKEN", "")           # se impostato, l'API richiede il token (vedi README)
MESHY_API_KEY = os.environ.get("MESHY_API_KEY", "")     # solo lato server
MESHY_BASE = os.environ.get("MESHY_BASE_URL", "https://api.meshy.ai")
LOCAL_CMD = os.environ.get("MODEL3D_LOCAL_CMD", "")     # es. "ssh gpu 'python trellis2_worker.py --image {image} --out {out}'"
LOCAL_CMD_TIMEOUT = int(os.environ.get("MODEL3D_LOCAL_TIMEOUT", "3600"))
ENABLE_FIXTURE = os.environ.get("MODEL3D_ENABLE_FIXTURE", "1") == "1"
JOBS_DIR.mkdir(parents=True, exist_ok=True)
