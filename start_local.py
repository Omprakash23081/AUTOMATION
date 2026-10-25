"""Load local credentials from .env and start Cutroom in combined mode."""
from __future__ import annotations

import os
from pathlib import Path

from backend.local_config import load_local_secrets

ROOT = Path(__file__).resolve().parent


if __name__ == "__main__":
    load_local_secrets()
    os.chdir(ROOT)
    import server

    server.main()
