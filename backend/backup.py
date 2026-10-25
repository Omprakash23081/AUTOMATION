"""Create a local archive of Cutroom's database and media files."""
from __future__ import annotations

import os
import sys
import zipfile
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("CUTROOM_DATA", ROOT / "data")).resolve()
BACKUP_DIR = Path(os.environ.get("CUTROOM_BACKUP_DIR", ROOT / "backups")).resolve()
ALREADY_COMPRESSED = {
    ".mp4", ".mov", ".webm", ".m4v", ".mp3", ".m4a", ".wav",
    ".jpg", ".jpeg", ".png", ".webp", ".zip",
}


def main() -> int:
    if not DATA.is_dir():
        print(f"Cutroom data folder was not found: {DATA}", file=sys.stderr)
        return 1

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    archive = BACKUP_DIR / f"cutroom-backup-{stamp}.zip"
    files = sorted(path for path in DATA.rglob("*") if path.is_file())
    if not files:
        print(f"No files to back up in {DATA}", file=sys.stderr)
        return 1

    with zipfile.ZipFile(archive, "w", allowZip64=True) as bundle:
        for path in files:
            relative = Path("data") / path.relative_to(DATA)
            compression = zipfile.ZIP_STORED if path.suffix.lower() in ALREADY_COMPRESSED else zipfile.ZIP_DEFLATED
            bundle.write(path, relative.as_posix(), compress_type=compression)

    print(f"Backup created: {archive}")
    print(f"Included {len(files)} files from {DATA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
