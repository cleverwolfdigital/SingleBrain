"""Central config. Everything overridable via env so the VPS can differ from dev."""
import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SB_DATA_DIR", str(APP_DIR / "data")))
DB_PATH = Path(os.environ.get("SB_DB_PATH", str(DATA_DIR / "singlebrain.db")))

# The single source of truth repo (already cloned on the VPS).
BRAIN_REPO = Path(os.environ.get("SB_BRAIN_REPO", "/root/singlebrain"))
JOURNAL_DIR = BRAIN_REPO / "Personal" / "Notes" / "Journal"

# Interim auth: a shared password gate until Magic Link + 2FA (Phase 2).
# Empty string = no gate (local dev only).
APP_PASSWORD = os.environ.get("SB_APP_PASSWORD", "")

# Whether journal writes get pushed to GitHub (commit always happens locally).
GIT_PUSH = os.environ.get("SB_GIT_PUSH", "1") == "1"

DATA_DIR.mkdir(parents=True, exist_ok=True)
