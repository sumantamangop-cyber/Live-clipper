"""Loads the key files for a real run. Only ``__main__`` calls this, so tests never read real keys.

Must run before ``config`` is imported: config reads the environment at import time.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_keys() -> None:
    data_dir = Path(os.environ.get("LIVE_CLIPPER_DIR", "live_clipper_data"))
    # real environment variables win (override=False); files are optional
    for path in (data_dir / "keys.env", data_dir / "twitch.env", REPO_ROOT / ".env"):
        load_dotenv(path, override=False)
