"""Load the small set of secrets used by the local server."""
from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent
LOCAL_SECRET_NAMES = {"SESSION_SECRET", "CUTROOM_SECRET_ENCRYPTION_KEY"}


def load_local_secrets(env_file: Path = ROOT / ".env") -> None:
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#") or "=" not in entry:
            continue
        name, value = entry.split("=", 1)
        name = name.strip()
        if name not in LOCAL_SECRET_NAMES:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if value:
            os.environ.setdefault(name, value)
