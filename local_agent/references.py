from __future__ import annotations

import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
REFERENCE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class LocalFileReferences:
    """Keep selected local paths on this computer; only opaque IDs reach Cutroom."""

    def __init__(self, database_path: Path | None = None):
        local_app_data = Path(
            os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        )
        self.database_path = database_path or local_app_data / "Cutroom" / "agent-files.sqlite3"

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=30)
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS local_file_references ("
                "id TEXT PRIMARY KEY, kind TEXT NOT NULL, path TEXT NOT NULL)"
            )
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _resolve_selected_file(raw_path: str | Path, extensions: set[str]) -> Path:
        selected = Path(raw_path).expanduser()
        if selected.is_symlink():
            raise ValueError("Selected media files cannot be symbolic links.")
        try:
            resolved = selected.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise FileNotFoundError(f"Selected file is not available: {selected}") from exc
        if not resolved.is_file() or resolved.suffix.lower() not in extensions:
            raise ValueError("Choose an existing video or audio file with a supported format.")
        return resolved

    @staticmethod
    def _resolve_output_directory(raw_path: str | Path) -> Path:
        selected = Path(raw_path).expanduser()
        if selected.is_symlink():
            raise ValueError("The output folder cannot be a symbolic link.")
        try:
            resolved = selected.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise FileNotFoundError(f"Selected output folder is not available: {selected}") from exc
        if not resolved.is_dir():
            raise ValueError("Choose an existing output folder.")
        return resolved

    def create_video_job(
        self,
        video_path: str | Path,
        audio_path: str | Path,
        output_directory: str | Path,
    ) -> dict[str, Any]:
        video = self._resolve_selected_file(video_path, VIDEO_EXTENSIONS)
        audio = self._resolve_selected_file(audio_path, AUDIO_EXTENSIONS)
        output_folder = self._resolve_output_directory(output_directory)

        video_id = secrets.token_hex(16)
        audio_id = secrets.token_hex(16)
        output_id = secrets.token_hex(16)
        output_path = output_folder / f"cutroom-sync-{output_id}.mp4"
        with self._connect() as connection:
            connection.executemany(
                "INSERT INTO local_file_references(id,kind,path) VALUES(?,?,?)",
                (
                    (video_id, "video", str(video)),
                    (audio_id, "audio", str(audio)),
                    (output_id, "output", str(output_path)),
                ),
            )
        return {
            "jobType": "video_sync",
            "videoRef": video_id,
            "audioRef": audio_id,
            "outputRef": output_id,
        }

    def resolve_video_job(self, payload: dict[str, Any]) -> dict[str, Path]:
        if (
            set(payload) != {"jobType", "videoRef", "audioRef", "outputRef"}
            or payload.get("jobType") != "video_sync"
        ):
            raise ValueError("The local video job references are invalid.")

        expected = {"videoRef": "video", "audioRef": "audio", "outputRef": "output"}
        identifiers = [payload[key] for key in expected]
        if any(
            not isinstance(identifier, str)
            or not REFERENCE_ID_PATTERN.fullmatch(identifier)
            for identifier in identifiers
        ):
            raise ValueError("The local video job references are invalid.")

        resolved: dict[str, Path] = {}
        with self._connect() as connection:
            for key, kind in expected.items():
                row = connection.execute(
                    "SELECT kind,path FROM local_file_references WHERE id=?",
                    (payload[key],),
                ).fetchone()
                if not row or row[0] != kind:
                    raise ValueError("The job refers to files not selected on this computer.")
                path = Path(row[1])
                if path.is_symlink():
                    raise ValueError("A selected local file reference is no longer valid.")
                resolved[key] = path

        resolved["videoRef"] = self._resolve_selected_file(
            resolved["videoRef"], VIDEO_EXTENSIONS
        )
        resolved["audioRef"] = self._resolve_selected_file(
            resolved["audioRef"], AUDIO_EXTENSIONS
        )
        output_path = resolved["outputRef"]
        try:
            output_folder = output_path.parent.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise FileNotFoundError("The selected output folder is no longer available.") from exc
        if (
            output_path.suffix.lower() != ".mp4"
            or output_folder != output_path.parent
            or not output_folder.is_dir()
            or output_path.is_symlink()
        ):
            raise ValueError("The selected output path is no longer valid.")
        resolved["outputRef"] = output_folder / output_path.name
        return resolved

    def delete_video_job(self, payload: dict[str, Any]) -> None:
        identifiers = [
            payload.get(key) for key in ("videoRef", "audioRef", "outputRef")
        ]
        if any(
            not isinstance(identifier, str)
            or not REFERENCE_ID_PATTERN.fullmatch(identifier)
            for identifier in identifiers
        ):
            return
        with self._connect() as connection:
            connection.executemany(
                "DELETE FROM local_file_references WHERE id=?",
                ((identifier,) for identifier in identifiers),
            )
