from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

from flask import Blueprint, jsonify, request, send_file, session
from cryptography.fernet import InvalidToken

automation_api = Blueprint("automation_api", __name__)
_threads: dict[str, threading.Thread] = {}
_threads_lock = threading.Lock()
_supported_reference_extensions = {
    ".mp4", ".mov", ".mkv", ".webm",
    ".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".txt",
}
_video_edit_extensions = {
    "videoFile": {".mp4", ".mov", ".mkv", ".webm"},
    "audioFile": {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"},
}
_youtube_cartesia_voices = {
    "kabir": "cb9c954d-bcaa-43ed-82bf-aeb5e88a3cb5",
    "rohan": "4877b818-c7fe-4c89-b1cf-eadf8e23da72",
    "denil": "47c38ca4-5f35-497b-b1a3-415245fb35e1",
    "jacqueline_female": "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc",
}


def _automation_home() -> Path:
    configured = os.environ.get("YOUTUBE_VIDEO_AUTOMATION_HOME")
    candidates = [Path(configured).expanduser()] if configured else []
    candidates.extend(
        (
            server.ROOT / "YOUTUBE_VIDEO_AUTOMATION",
            server.ROOT.parent / "YOUTUBE_VIDEO_AUTOMATION",
        )
    )
    for candidate in candidates:
        root = candidate.resolve()
        if (root / "youtube_automation").is_dir():
            return root
    return candidates[0].resolve() if candidates else server.ROOT.parent / "YOUTUBE_VIDEO_AUTOMATION"


def _automation_interpreter(home: Path) -> Path:
    candidates = (
        home / "venv" / "Scripts" / "python.exe",
        home / ".venv" / "Scripts" / "python.exe",
    )
    return next((path for path in candidates if path.is_file()), Path(sys.executable))


def _read_workspace_api_url(workspace_id: str) -> str:
    with server.db() as con:
        row = con.execute(
            "SELECT api_url FROM workspace_automation_settings WHERE workspace_id=?",
            (workspace_id,),
        ).fetchone()
        legacy = con.execute(
            "SELECT ciphertext FROM workspace_automation_secrets WHERE workspace_id=?",
            (workspace_id,),
        ).fetchone()
    if row:
        return row["api_url"]
    if not legacy:
        return ""
    cipher = server.workspace_secret_cipher()
    if cipher is None:
        return ""
    try:
        payload = json.loads(cipher.decrypt(legacy["ciphertext"].encode()).decode())
    except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Saved automation credentials could not be decrypted. Restore the encryption key and try again.") from exc
    return str(payload.get("apiUrl") or "") if isinstance(payload, dict) else ""


def _read_workspace_secrets(workspace_id: str) -> dict:
    with server.db() as con:
        row = con.execute(
            "SELECT ciphertext FROM workspace_automation_secrets WHERE workspace_id=?",
            (workspace_id,),
        ).fetchone()
    if not row:
        return {}
    cipher = server.workspace_secret_cipher()
    if cipher is None:
        raise RuntimeError("Configure CUTROOM_SECRET_ENCRYPTION_KEY to restore automation credentials.")
    try:
        payload = cipher.decrypt(row["ciphertext"].encode()).decode()
        value = json.loads(payload)
    except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Saved automation credentials could not be decrypted. Restore the encryption key and try again.") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Saved automation settings are invalid. Re-enter the credentials.")
    return value


def _workspace_output_root(workspace_id: str) -> Path:
    return server.DATA / "automation" / workspace_id


def _remove_workspace_job_path(workspace_id: str, path: Path) -> None:
    root = _workspace_output_root(workspace_id).resolve()
    candidate = path.resolve()
    if candidate != root and root in candidate.parents:
        shutil.rmtree(candidate, ignore_errors=True)


def _resolve_job_output(workspace_id: str, job_id: str, row) -> Path:
    try:
        output_path = Path(row["output_path"])
        if output_path.is_symlink():
            raise ValueError("Automation output path is invalid.")
        output = output_path.resolve(strict=True)
    except (OSError, RuntimeError, TypeError) as exc:
        try:
            payload = json.loads(row["payload_json"])
            target_path = Path(payload["targetFolder"])
            if payload.get("workflow") != "youtube_batch" or target_path.is_symlink():
                raise ValueError("Automation output path is invalid.")
            output = target_path.resolve(strict=True)
        except (KeyError, OSError, RuntimeError, TypeError, json.JSONDecodeError) as fallback_error:
            raise ValueError("Completed automation output folder is unavailable.") from exc
        if not output.is_dir():
            raise ValueError("Completed automation output folder is unavailable.") from exc
    if output.is_symlink():
        raise ValueError("Automation output path is invalid.")
    workspace_root = _workspace_output_root(workspace_id).resolve()
    if output == workspace_root or workspace_root in output.parents:
        return output
    try:
        payload = json.loads(row["payload_json"])
        target = Path(payload["targetFolder"]).resolve(strict=True)
        legacy_output = (target / f"Cutroom-Kuaishou-{job_id}").resolve()
    except (KeyError, OSError, RuntimeError, TypeError, json.JSONDecodeError):
        raise ValueError("Automation output path is invalid.")
    workflow = payload.get("workflow")
    if workflow not in {"kuaishou", "youtube_batch"} or not target.is_dir():
        raise ValueError("Automation output path is invalid.")
    if workflow == "youtube_batch" and output != target:
        raise ValueError("Automation output path is invalid.")
    if workflow == "kuaishou" and (
        output not in {target, legacy_output}
        or (output == legacy_output and legacy_output.parent != target)
    ):
        raise ValueError("Automation output path is invalid.")
    return output


def _manifest_output_files(output: Path, payload: dict) -> list[Path]:
    manifest = payload.get("outputFiles")
    if not isinstance(manifest, list):
        return []
    files = []
    for relative_path in manifest:
        if not isinstance(relative_path, str):
            continue
        candidate = output.joinpath(*Path(relative_path).parts)
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if candidate.is_symlink() or not resolved.is_file() or output not in resolved.parents:
            continue
        files.append(resolved)
    return sorted(files, key=lambda item: str(item).casefold())


def _kuaishou_part_filename(part_number: int, video_title: str) -> str:
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", video_title)
    title = re.sub(r"_+", "_", re.sub(r"\s+", "_", title))
    title = title.strip(" ._-")[:100].strip(" ._-")
    return f"PART-{part_number}" + (f"_{title}" if title else "")


def _youtube_filename_prefix(name_prefix: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name_prefix.strip())
    cleaned = re.sub(r"_+", "_", re.sub(r"\s+", "_", cleaned))
    cleaned = cleaned.strip(" ._-")[:100].strip(" ._-")
    return cleaned or "video"


def _record_kuaishou_output_manifest(job_id: str, workspace_id: str, output: Path) -> None:
    with server.db() as con:
        row = con.execute(
            "SELECT payload_json FROM automation_jobs WHERE id=? AND workspace_id=?",
            (job_id, workspace_id),
        ).fetchone()
        if not row:
            return
        payload = json.loads(row["payload_json"])
        if payload.get("workflow") != "kuaishou" or not payload.get("targetFolder"):
            return
        links = [
            line.strip()
            for line in str(payload.get("urls") or "").splitlines()
            if line.strip()
        ]
        mode = int(payload.get("mode", 4))
        start_part = int(payload.get("startPart", 61))
        video_title = str(payload.get("videoTitle") or "").strip()
        output_files = []
        for index in range(len(links)):
            part_basename = _kuaishou_part_filename(start_part + index, video_title)
            relative_paths = [Path("videos") / f"{part_basename}.mp4"]
            if mode >= 2:
                relative_paths.append(Path("audio") / f"{part_basename}.m4a")
            if mode >= 3:
                relative_paths.append(Path("transcripts") / f"{part_basename}.txt")
            if mode == 4:
                relative_paths.append(Path("transcripts") / f"{part_basename}_hinglish.txt")
            for relative_path in relative_paths:
                candidate = output / relative_path
                if candidate.is_file() and not candidate.is_symlink():
                    output_files.append(relative_path.as_posix())
        payload["outputFiles"] = output_files
        con.execute(
            "UPDATE automation_jobs SET payload_json=?,updated_at=? WHERE id=? AND workspace_id=?",
            (
                json.dumps(payload, separators=(",", ":")),
                server.now(),
                job_id,
                workspace_id,
            ),
        )


def _record_youtube_output_manifest(job_id: str, workspace_id: str) -> None:
    with server.db() as con:
        row = con.execute(
            "SELECT payload_json FROM automation_jobs WHERE id=? AND workspace_id=?",
            (job_id, workspace_id),
        ).fetchone()
        if not row:
            return
        payload = json.loads(row["payload_json"])
        if payload.get("workflow") != "youtube_batch" or not payload.get("targetFolder"):
            return
        output = Path(payload["targetFolder"])
        links = [
            line.strip()
            for line in str(payload.get("urls") or "").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        prefix = _youtube_filename_prefix(
            str(payload.get("namePrefix") or output.name or "video")
        )
        start_number = int(payload.get("startNumber", 1))
        output_files = []
        for index in range(start_number, start_number + len(links)):
            stem = f"{prefix}{index:03}"
            for relative_path in (
                Path("videos") / f"{stem}.mp4",
                Path("transcripts") / f"{stem}.txt",
                Path("transcripts") / f"{stem}_english.txt",
                Path("audio") / f"{stem}_english.wav",
            ):
                candidate = output / relative_path
                if candidate.is_file() and not candidate.is_symlink():
                    output_files.append(relative_path.as_posix())
        payload["outputFiles"] = output_files
        con.execute(
            "UPDATE automation_jobs SET payload_json=?,updated_at=? WHERE id=? AND workspace_id=?",
            (
                json.dumps(payload, separators=(",", ":")),
                server.now(),
                job_id,
                workspace_id,
            ),
        )


def _list_jobs(workspace_id: str) -> list[dict]:
    with server.db() as con:
        rows = con.execute(
            "SELECT id,workflow,status,stage,progress,logs,error,created_at,finished_at "
            "FROM automation_jobs WHERE workspace_id=? ORDER BY created_at DESC LIMIT 20",
            (workspace_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _workspace_can_manage(workspace_id: str, user_id: str) -> bool:
    with server.db() as con:
        member = con.execute(
            "SELECT role FROM memberships WHERE workspace_id=? AND user_id=?",
            (workspace_id, user_id),
        ).fetchone()
    return bool(member and member["role"] in {"owner", "admin"})


@automation_api.get("/api/automation")
def automation_status():
    home = _automation_home()
    interpreter = _automation_interpreter(home)
    try:
        api_url = _read_workspace_api_url(session["workspace_id"])
        secrets = _read_workspace_secrets(session["workspace_id"])
        settings_error = None
    except RuntimeError as exc:
        try:
            api_url = _read_workspace_api_url(session["workspace_id"])
        except RuntimeError:
            api_url = ""
        secrets = {}
        settings_error = str(exc)
    api_key_configured = bool(secrets.get("apiKey"))
    cartesia_configured = bool(secrets.get("cartesiaApiKey"))
    return jsonify(
        sourceAvailable=(home / "youtube_automation").is_dir() and interpreter.is_file(),
        jobsAvailable=server.APP_MODE == "combined",
        credentials={
            "apiUrl": api_url,
            "kuaishouUrlConfigured": bool(api_url),
            "kuaishouKeyConfigured": api_key_configured,
            "kuaishouConfigured": bool(api_url and api_key_configured),
            "cartesiaConfigured": cartesia_configured,
        },
        credentialsError=settings_error,
        canManageCredentials=_workspace_can_manage(session["workspace_id"], session["user_id"]),
        jobs=_list_jobs(session["workspace_id"]),
    )


@automation_api.put("/api/automation/settings")
def save_automation_settings():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="Automation settings must be an object."), 400
    if not _workspace_can_manage(session["workspace_id"], session["user_id"]):
        return jsonify(error="Only workspace owners and admins can manage automation credentials."), 403
    api_url = str(data.get("apiUrl") or "").strip()
    api_key = str(data.get("apiKey") or "").strip()
    cartesia_key = str(data.get("cartesiaApiKey") or "").strip()
    if api_url:
        parsed = urlparse(api_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return jsonify(error="Kuaishou API URL must be a valid HTTPS URL without embedded credentials."), 400
    if api_key and (len(api_key) < 8 or any(character.isspace() for character in api_key)):
        return jsonify(error="Enter a valid Kuaishou API key without spaces."), 400
    if cartesia_key and (len(cartesia_key) < 20 or any(character.isspace() for character in cartesia_key)):
        return jsonify(error="Enter a valid Cartesia API key."), 400
    ciphertext = None
    if api_key or cartesia_key:
        cipher = server.workspace_secret_cipher()
        if cipher is None:
            return jsonify(error="Configure CUTROOM_SECRET_ENCRYPTION_KEY before saving API keys."), 503
        try:
            secrets = _read_workspace_secrets(session["workspace_id"])
        except RuntimeError as exc:
            return jsonify(error=str(exc)), 503
        if api_key:
            secrets["apiKey"] = api_key
        if cartesia_key:
            secrets["cartesiaApiKey"] = cartesia_key
        ciphertext = cipher.encrypt(json.dumps(secrets, separators=(",", ":")).encode()).decode()
    with server.db() as con:
        if "apiUrl" in data:
            if api_url:
                con.execute(
                    "INSERT INTO workspace_automation_settings(workspace_id,api_url,updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(workspace_id) DO UPDATE SET api_url=excluded.api_url,updated_at=excluded.updated_at",
                    (session["workspace_id"], api_url, server.now()),
                )
            else:
                con.execute(
                    "DELETE FROM workspace_automation_settings WHERE workspace_id=?",
                    (session["workspace_id"],),
                )
        if ciphertext:
            con.execute(
                "INSERT INTO workspace_automation_secrets(workspace_id,ciphertext,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(workspace_id) DO UPDATE SET ciphertext=excluded.ciphertext,updated_at=excluded.updated_at",
                (session["workspace_id"], ciphertext, server.now()),
            )
    try:
        saved_secrets = _read_workspace_secrets(session["workspace_id"])
    except RuntimeError:
        saved_secrets = {}
    saved_api_url = _read_workspace_api_url(session["workspace_id"])
    return jsonify(
        ok=True,
        kuaishouUrlConfigured=bool(saved_api_url),
        kuaishouKeyConfigured=bool(saved_secrets.get("apiKey")),
        kuaishouConfigured=bool(saved_api_url and saved_secrets.get("apiKey")),
        cartesiaConfigured=bool(saved_secrets.get("cartesiaApiKey")),
    )


@automation_api.post("/api/automation/references")
def scan_automation_references():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="Provide a folder path to scan."), 400
    raw_path = str(data.get("folder") or "").strip()
    if not raw_path:
        return jsonify(error="Choose a folder path first."), 400
    try:
        folder = Path(raw_path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return jsonify(error="That folder is not available on this computer."), 400
    if not folder.is_dir():
        return jsonify(error="Choose an existing folder."), 400
    files = []
    try:
        for path in sorted(folder.iterdir(), key=lambda item: item.name.casefold()):
            if path.is_symlink() or not path.is_file() or path.suffix.lower() not in _supported_reference_extensions:
                continue
            stat = path.stat()
            files.append({
                "name": path.name,
                "path": str(path),
                "type": path.suffix.lower().lstrip("."),
                "bytes": stat.st_size,
                "modifiedAt": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            })
            if len(files) >= 500:
                break
    except OSError:
        return jsonify(error="Could not read that folder."), 400
    return jsonify(folder=str(folder), readOnly=True, files=files)


def _validate_source_folder(raw_value: str) -> Path:
    try:
        folder = Path(raw_value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("Choose an available source folder.") from exc
    if not folder.is_dir():
        raise ValueError("Choose an existing source folder.")
    return folder


def _read_text_file(folder: Path, filename: str) -> Path:
    name = Path(filename).name
    if name != filename or not name.lower().endswith(".txt"):
        raise ValueError("Choose a .txt file from the scanned reference folder.")
    candidate = folder / name
    if candidate.is_symlink():
        raise ValueError("The selected transcript is not available in that folder.")
    try:
        path = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("The selected transcript is not available in that folder.") from exc
    if path.parent != folder or path.is_symlink() or not path.is_file():
        raise ValueError("The selected transcript is not available in that folder.")
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Transcript files must be 2 MB or smaller.")
    return path


def _save_video_edit_upload(upload, destination: Path, *, max_bytes: int) -> None:
    filename = str(upload.filename or "")
    if (
        not filename
        or Path(filename).name != filename
        or "/" in filename
        or "\\" in filename
    ):
        raise ValueError("Each uploaded file must have a valid filename.")
    stream = upload.stream
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    if size <= 0:
        raise ValueError(f"Uploaded file is empty: {filename}")
    if size > max_bytes:
        raise ValueError(f"Uploaded file exceeds the size limit: {filename}")
    upload.save(destination)


def _validate_youtube_urls(raw: str) -> list[str]:
    urls = [line.strip() for line in raw.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not 1 <= len(urls) <= 50:
        raise ValueError("Enter between 1 and 50 YouTube URLs.")
    for value in urls:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or host not in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}:
            raise ValueError("Only HTTPS YouTube video URLs are supported in the YouTube batch workflow.")
    return urls


def _prepare_job(workspace_id: str, job_id: str) -> tuple[Path, Path]:
    root = _workspace_output_root(workspace_id)
    working = root / "jobs" / job_id
    output = root / "outputs" / job_id
    working.mkdir(parents=True, exist_ok=False)
    output.mkdir(parents=True, exist_ok=False)
    return working, output


def _build_automation_job(workspace_id: str, job_id: str, params: dict) -> tuple[list[str], Path, Path, dict]:
    home = _automation_home()
    package = home / "youtube_automation"
    python = _automation_interpreter(home)
    if not package.is_dir():
        raise RuntimeError(
            "The sibling automation source was not found. Set YOUTUBE_VIDEO_AUTOMATION_HOME to its project folder."
        )
    if not python.is_file():
        raise RuntimeError("The sibling automation Python environment is unavailable.")
    workflow = params["workflow"]
    mode = int(params.get("mode", 4)) if workflow == "kuaishou" else None
    working, output = _prepare_job(workspace_id, job_id)
    profile_root = _workspace_output_root(workspace_id) / "profiles"
    chatgpt_profile = profile_root / "chatgpt"
    gemini_profile = profile_root / "gemini"
    if workflow != "kuaishou" or mode == 4:
        chatgpt_profile.mkdir(parents=True, exist_ok=True)
    if workflow != "kuaishou" or mode in {3, 4}:
        gemini_profile.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHON_DOTENV_DISABLED"] = "1"
    env["PYTHONPATH"] = os.pathsep.join((str(server.ROOT), str(home), env.get("PYTHONPATH", ""))).rstrip(os.pathsep)
    for name in ("API_URL", "API_KEY", "CARTESIA_API_KEY", "GEMINI_API_KEY"):
        env.pop(name, None)
    env.update({
        "VIDEO_FOLDER": str(output / "videos"),
        "AUDIO_FOLDER": str(output / "audio"),
        "TRANSCRIPT_FOLDER": str(output / "transcripts"),
        "PLAYWRIGHT_PROFILE_DIR": str(gemini_profile),
        "CHATGPT_PROFILE_DIR": str(chatgpt_profile),
        "CHATGPT_FALLBACK_PROFILE_DIR": str(chatgpt_profile),
        "PLAYWRIGHT_HEADLESS": "False",
    })
    command: list[str]
    if workflow == "kuaishou":
        shared_browser_profile = home / "playwright_profile"
        if mode in {3, 4}:
            env["PLAYWRIGHT_PROFILE_DIR"] = str(shared_browser_profile)
            env["CHATGPT_PROFILE_DIR"] = str(shared_browser_profile)
            env["GEMINI_FALLBACK_PROFILE_DIR"] = str(gemini_profile)
            env["CHATGPT_FALLBACK_PROFILE_DIR"] = str(chatgpt_profile)
        api_url = _read_workspace_api_url(workspace_id)
        settings = _read_workspace_secrets(workspace_id)
        api_key = str(settings.get("apiKey") or "")
        if not api_url or not api_key:
            raise ValueError("Save both the Kuaishou backend URL and API key before running this workflow.")
        links = [line.strip() for line in params.get("urls", "").splitlines() if line.strip()]
        if not 1 <= len(links) <= 100:
            raise ValueError("Enter between 1 and 100 Kuaishou links.")
        if any(urlparse(link).scheme != "https" or not urlparse(link).hostname for link in links):
            raise ValueError("Each Kuaishou link must be an HTTPS URL.")
        if mode not in {1, 2, 3, 4}:
            raise ValueError("Choose a Kuaishou processing mode from 1 to 4.")
        raw_start_part = params.get("startPart", 61)
        if isinstance(raw_start_part, bool):
            raise ValueError("Starting part number must be an integer from 1 to 999999999.")
        try:
            start_part = int(raw_start_part)
        except (TypeError, ValueError) as error:
            raise ValueError("Starting part number must be an integer from 1 to 999999999.") from error
        if str(start_part) != str(raw_start_part).strip() or not 1 <= start_part <= 999999999:
            raise ValueError("Starting part number must be an integer from 1 to 999999999.")
        video_title = str(params.get("videoTitle") or "").strip()
        if len(video_title) > 100:
            raise ValueError("Video title must be 100 characters or fewer.")
        params["startPart"] = start_part
        params["videoTitle"] = video_title
        env["START_PART"] = str(start_part)
        env["VIDEO_TITLE"] = video_title
        target_folder = str(params.get("targetFolder") or "").strip()
        if target_folder:
            target = _validate_source_folder(target_folder)
            params["targetFolder"] = str(target)
            _remove_workspace_job_path(workspace_id, output)
            output = target
            env["VIDEO_FOLDER"] = str(target / "videos")
            env["AUDIO_FOLDER"] = str(target / "audio")
            env["TRANSCRIPT_FOLDER"] = str(target / "transcripts")
        (working / "links.txt").write_text("\n".join(links) + "\n", encoding="utf-8")
        env["API_URL"] = api_url
        env["API_KEY"] = api_key
        command = [str(python), "-m", "youtube_automation.pipeline", "--mode", str(mode)]
    elif workflow == "youtube_batch":
        urls = _validate_youtube_urls(str(params.get("urls") or ""))
        allowed_steps = {
            "download",
            "transcript",
            "english",
            "tts",
            "timestamped_transcript",
        }
        raw_steps = params.get("steps", sorted(allowed_steps))
        if (
            not isinstance(raw_steps, list)
            or not raw_steps
            or any(not isinstance(step, str) for step in raw_steps)
            or len(set(raw_steps)) != len(raw_steps)
            or set(raw_steps) - allowed_steps
        ):
            raise ValueError("Select one or more valid YouTube batch steps.")
        steps = [step for step in (
            "download",
            "transcript",
            "english",
            "tts",
            "timestamped_transcript",
        ) if step in raw_steps]
        params["steps"] = steps
        if "tts" in steps:
            cartesia_key = str(
                _read_workspace_secrets(workspace_id).get("cartesiaApiKey") or ""
            )
            if not cartesia_key:
                raise ValueError(
                    "Save a Cartesia API key in Workspace Credentials before "
                    "selecting TTS."
                )
            env["CARTESIA_API_KEY"] = cartesia_key
        if "tts" in steps:
            voice_character = params.get("voiceCharacter", "")
            if not isinstance(voice_character, str):
                raise ValueError("Choose a valid Cartesia character voice.")
            voice_character = voice_character.strip().lower()
            if voice_character == "other":
                raw_voice_id = params.get("voiceId", "")
                if not isinstance(raw_voice_id, str):
                    raise ValueError("Cartesia voice ID must be text.")
                voice_id = raw_voice_id.strip()
                if not voice_id:
                    raise ValueError("Enter a Cartesia voice ID for the Other voice option.")
                if len(voice_id) > 200:
                    raise ValueError("Cartesia voice ID must be 200 characters or fewer.")
                params["voiceCharacter"] = "other"
                params["voiceId"] = voice_id
            elif voice_character:
                voice_id = _youtube_cartesia_voices.get(voice_character)
                if not voice_id:
                    raise ValueError("Choose a valid Cartesia character voice.")
                params["voiceCharacter"] = voice_character
            else:
                voice_id = ""
                params.pop("voiceId", None)
            if voice_id:
                env["CARTESIA_ENGLISH_VOICE_ID"] = voice_id
        language = str(params.get("language") or "en")
        if language not in {"en", "hi"}:
            raise ValueError("Choose English or Hindi transcript language.")
        raw_start_number = params.get("startNumber", 1)
        if isinstance(raw_start_number, bool):
            raise ValueError("Starting video number must be an integer from 1 to 999999999.")
        try:
            start_number = int(raw_start_number)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "Starting video number must be an integer from 1 to 999999999."
            ) from error
        if (
            str(start_number) != str(raw_start_number).strip()
            or not 1 <= start_number <= 999999999
        ):
            raise ValueError("Starting video number must be an integer from 1 to 999999999.")
        name_prefix = str(params.get("namePrefix") or "YouTube").strip()
        if len(name_prefix) > 100:
            raise ValueError("Output filename prefix must be 100 characters or fewer.")
        name_prefix = _youtube_filename_prefix(name_prefix or "YouTube")
        params["namePrefix"] = name_prefix
        target_folder = str(params.get("targetFolder") or "").strip()
        if target_folder:
            target = _validate_source_folder(target_folder)
            params["targetFolder"] = str(target)
            _remove_workspace_job_path(workspace_id, output)
            output = target
        (working / "youtube-links.txt").write_text("\n".join(urls) + "\n", encoding="utf-8")
        command = [
            str(python), "-m", "youtube_automation.youtube_download.downloader",
            "--authorized", "--links", str(working / "youtube-links.txt"),
            "--output-dir", str(output), "--transcript-language", language,
            "--start-number", str(start_number),
        ]
        if name_prefix:
            command.extend(["--name-prefix", name_prefix])
        command.extend(["--steps", ",".join(steps)])
    elif workflow in {"gemini_transcribe", "chatgpt_hinglish", "whisper"}:
        source = _validate_source_folder(str(params.get("sourceFolder") or ""))
        supported = {".m4a", ".mp3", ".wav"} if workflow in {"gemini_transcribe", "whisper"} else {".txt"}
        files = [path for path in source.iterdir() if path.is_file() and not path.is_symlink() and path.suffix.lower() in supported]
        if not files:
            raise ValueError(f"No supported {', '.join(sorted(supported))} files were found in that folder.")
        if workflow == "gemini_transcribe":
            env["AUDIO_FOLDER"] = str(source)
            command = [str(python), "-m", "youtube_automation.gemini.transcribe"]
        elif workflow == "chatgpt_hinglish":
            transcripts = working / "transcripts"
            transcripts.mkdir()
            for index, path in enumerate(sorted(files), start=1):
                target = transcripts / f"PART-{index}.txt"
                shutil.copyfile(path, target)
            env["TRANSCRIPT_FOLDER"] = str(transcripts)
            command = [str(python), "-m", "youtube_automation.chatgpt.transcribe"]
        else:
            audio = working / "audio"
            audio.mkdir()
            for path in files:
                shutil.copyfile(path, audio / path.name)
            config_file = working / "runner.json"
            config_file.write_text(json.dumps({
                "workflow": "whisper", "sourceDirectory": str(audio),
                "outputDirectory": str(output / "transcripts"),
            }), encoding="utf-8")
            command = [str(python), str(server.ROOT / "automation_runner.py"), str(config_file)]
    elif workflow == "english_translation":
        source = _validate_source_folder(str(params.get("sourceFolder") or ""))
        transcript = _read_text_file(source, str(params.get("sourceFile") or ""))
        working_transcript = working / transcript.name
        shutil.copyfile(transcript, working_transcript)
        result = output / f"{transcript.stem}_english.txt"
        config_file = working / "runner.json"
        config_file.write_text(json.dumps({
            "workflow": "english_translation", "sourceFile": str(working_transcript),
            "outputFile": str(result),
        }), encoding="utf-8")
        command = [str(python), str(server.ROOT / "automation_runner.py"), str(config_file)]
    elif workflow == "video_edit_sync":
        video_name = str(params.get("videoFile") or "")
        audio_name = str(params.get("audioFile") or "")
        video_suffix = Path(video_name).suffix.lower()
        audio_suffix = Path(audio_name).suffix.lower()
        if Path(video_name).name != video_name or video_suffix not in _video_edit_extensions["videoFile"]:
            raise ValueError("Upload a Chinese video in MP4, MOV, MKV, or WebM format.")
        if Path(audio_name).name != audio_name or audio_suffix not in _video_edit_extensions["audioFile"]:
            raise ValueError("Upload Hindi audio in MP3, WAV, M4A, AAC, FLAC, or OGG format.")
        transcript_name = str(params.get("transcriptFile") or "")
        if Path(transcript_name).name != transcript_name or Path(transcript_name).suffix.lower() != ".txt":
            raise ValueError("Upload the timestamped transcript as a .txt file.")
        video_stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", Path(video_name).stem)
        video_stem = video_stem.strip(" ._")[:100] or "video"
        working_video = working / f"source_video{video_suffix}"
        working_audio = working / f"hindi_audio{audio_suffix}"
        working_transcript = working / "audio_transcript.txt"
        gemini_cache_dir = (
            _workspace_output_root(workspace_id)
            / "temp"
            / "video_edit_gemini"
        )
        command = [
            str(python),
            "-m",
            "youtube_automation.video_edit",
            "--video",
            str(working_video),
            "--audio",
            str(working_audio),
            "--transcript",
            str(working_transcript),
            "--gemini-cache-dir",
            str(gemini_cache_dir),
            "--output",
            str(output / "videos" / f"{video_stem}_hindi_synchronized.mp4"),
        ]
    elif workflow == "cartesia_tts":
        settings = _read_workspace_secrets(workspace_id)
        if not settings.get("cartesiaApiKey"):
            raise ValueError("Save a Cartesia API key before generating speech.")
        text = str(params.get("text") or "").strip()
        if not text or len(text) > 30_000:
            raise ValueError("Enter transcript text between 1 and 30,000 characters.")
        transcript = working / "transcript.txt"
        transcript.write_text(text + "\n", encoding="utf-8")
        env["CARTESIA_API_KEY"] = settings["cartesiaApiKey"]
        command = [
            str(python), "-m", "youtube_automation.cartesia.tts",
            str(transcript), "--output", str(output / "speech.wav"),
        ]
    else:
        raise ValueError("Choose a supported automation workflow.")
    return command, working, output, env


def _update_job(job_id: str, **values) -> None:
    allowed = {"status", "stage", "progress", "logs", "output_path", "error", "finished_at", "updated_at"}
    if not values or set(values) - allowed:
        raise ValueError("Invalid automation job update.")
    values["updated_at"] = server.now()
    assignments = ",".join(f"{key}=?" for key in values)
    with server.db() as con:
        con.execute(f"UPDATE automation_jobs SET {assignments} WHERE id=?", (*values.values(), job_id))


def _download_progress_from_log(line: str) -> int | None:
    if re.search(r"\[Downloader\]\[PART-\d+\] (?:Video|Audio) download progress:", line):
        match = re.search(r"\((\d{1,3})%\)", line)
        if match:
            return min(99, int(match.group(1)))
    if re.search(r"\[Downloader\]\[PART-\d+\] Starting (?:Video|Audio) download:", line):
        return 0
    return None


def _run_automation_process(
    job_id: str,
    workspace_id: str,
    command: list[str],
    working: Path,
    output: Path,
    env: dict,
    timeout: int,
) -> None:
    log_tail = ""
    process = None
    try:
        _update_job(job_id, status="Running", stage="Starting", progress=1)
        process = subprocess.Popen(
            command,
            cwd=working,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        output_queue: queue.Queue[str | None] = queue.Queue()

        def read_output() -> None:
            assert process is not None and process.stdout is not None
            for line in process.stdout:
                output_queue.put(line.rstrip())
            output_queue.put(None)

        reader = threading.Thread(target=read_output, name=f"automation-log-{job_id}", daemon=True)
        reader.start()
        deadline = time.monotonic() + timeout * 60
        reader_finished = False
        while not reader_finished:
            if time.monotonic() >= deadline and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise TimeoutError(f"Automation job exceeded its {timeout}-minute time limit.")
            try:
                line = output_queue.get(timeout=0.5)
            except queue.Empty:
                if process.poll() is not None and not reader.is_alive():
                    break
                continue
            if line is None:
                reader_finished = True
                continue
            log_tail = (log_tail + line + "\n")[-20_000:]
            progress = _download_progress_from_log(line)
            updates = {"stage": line[:180] or "Running", "logs": log_tail}
            if progress is not None:
                updates["progress"] = progress
            _update_job(job_id, **updates)
        return_code = process.wait()
        reader.join(timeout=5)
        if return_code:
            raise RuntimeError(f"Automation process exited with code {return_code}.")
        _record_kuaishou_output_manifest(job_id, workspace_id, output)
        _record_youtube_output_manifest(job_id, workspace_id)
        _update_job(
            job_id,
            status="Succeeded",
            stage="Completed",
            progress=100,
            logs=log_tail,
            output_path=str(output),
            error=None,
            finished_at=server.now(),
        )
    except Exception as exc:
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        _update_job(
            job_id,
            status="Failed",
            stage="Failed",
            logs=log_tail,
            error=str(exc)[:1500] or exc.__class__.__name__,
            finished_at=server.now(),
        )
    finally:
        with _threads_lock:
            _threads.pop(job_id, None)


@automation_api.post("/api/automation/jobs")
def create_automation_job():
    if server.APP_MODE != "combined":
        return jsonify(error="Automation jobs currently require Cutroom’s local combined mode."), 503
    uploads = None
    if request.mimetype == "multipart/form-data":
        data = request.form.to_dict()
        uploads = {
            "video": request.files.get("video"),
            "audio": request.files.get("audio"),
            "transcript": request.files.get("transcript"),
        }
        if any(upload is None for upload in uploads.values()):
            return jsonify(error="Upload the video, audio, and timestamped transcript files."), 400
        data.update({
            "videoFile": uploads["video"].filename,
            "audioFile": uploads["audio"].filename,
            "transcriptFile": uploads["transcript"].filename,
        })
    else:
        data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="Automation job options must be an object."), 400
    workflow = str(data.get("workflow") or "")
    if uploads and workflow != "video_edit_sync":
        return jsonify(error="File uploads are supported only for VIDEO EDIT AUTOMATION."), 400
    if workflow == "video_edit_sync" and uploads is None:
        return jsonify(error="Upload the Chinese video, Hindi audio, and timestamped transcript files."), 400
    params = {**data, "workflow": workflow}
    if workflow in {"youtube_batch", "kuaishou"} and data.get("authorized") is not True:
        return jsonify(error="Confirm that you own or have permission to download the supplied videos."), 400
    with server.db() as con:
        member = con.execute(
            "SELECT role FROM memberships WHERE workspace_id=? AND user_id=?",
            (session["workspace_id"], session["user_id"]),
        ).fetchone()
    if not member or member["role"] not in {"owner", "admin", "editor"}:
        return jsonify(error="Your workspace role cannot start automation jobs."), 403
    job_id = uuid.uuid4().hex
    root = _workspace_output_root(session["workspace_id"])
    working = root / "jobs" / job_id
    output = root / "outputs" / job_id
    try:
        timeout = int(data.get("timeoutMinutes", 240))
        if not 1 <= timeout <= 1440:
            raise ValueError("Automation timeout must be between 1 and 1,440 minutes.")
        command, working, output, env = _build_automation_job(
            session["workspace_id"], job_id, params
        )
        if uploads:
            video_suffix = Path(params["videoFile"]).suffix.lower()
            audio_suffix = Path(params["audioFile"]).suffix.lower()
            _save_video_edit_upload(
                uploads["video"],
                working / f"source_video{video_suffix}",
                max_bytes=4 * 1024**3,
            )
            _save_video_edit_upload(
                uploads["audio"],
                working / f"hindi_audio{audio_suffix}",
                max_bytes=4 * 1024**3,
            )
            _save_video_edit_upload(
                uploads["transcript"],
                working / "audio_transcript.txt",
                max_bytes=2 * 1024**2,
            )
    except (TypeError, ValueError, RuntimeError, OSError) as exc:
        _remove_workspace_job_path(session["workspace_id"], working)
        _remove_workspace_job_path(session["workspace_id"], output)
        return jsonify(error=str(exc)), 400
    with server.db() as con:
        workspace_active = con.execute(
            "SELECT id FROM automation_jobs WHERE workspace_id=? AND status IN ('Queued','Running') LIMIT 1",
            (session["workspace_id"],),
        ).fetchone()
        if workspace_active:
            _remove_workspace_job_path(session["workspace_id"], working)
            _remove_workspace_job_path(session["workspace_id"], output)
            return jsonify(error="This workspace already has an automation job running."), 409
        try:
            con.execute(
                "INSERT INTO automation_jobs(id,workspace_id,user_id,workflow,status,stage,progress,payload_json,logs,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, session["workspace_id"], session["user_id"], workflow, "Queued", "Queued", 0,
                 json.dumps(params, separators=(",", ":")), "", server.now(), server.now()),
            )
        except server.sqlite3.IntegrityError:
            _remove_workspace_job_path(session["workspace_id"], working)
            _remove_workspace_job_path(session["workspace_id"], output)
            return jsonify(error="This workspace already has an automation job running."), 409
    thread = threading.Thread(
        target=_run_automation_process,
        args=(job_id, session["workspace_id"], command, working, output, env, timeout),
        name=f"cutroom-automation-{job_id}",
        daemon=True,
    )
    with _threads_lock:
        _threads[job_id] = thread
    thread.start()
    return jsonify(ok=True, jobId=job_id, status="Queued"), 202


@automation_api.get("/api/automation/jobs/<job_id>")
def get_automation_job(job_id: str):
    with server.db() as con:
        row = con.execute(
            "SELECT id,workflow,status,stage,progress,logs,error,created_at,finished_at "
            "FROM automation_jobs WHERE id=? AND workspace_id=?",
            (job_id, session["workspace_id"]),
        ).fetchone()
    if not row:
        return jsonify(error="Automation job not found."), 404
    return jsonify(job=dict(row))

@automation_api.get("/api/automation/jobs/<job_id>/files")
def get_automation_job_files(job_id: str):
    with server.db() as con:
        row = con.execute(
            "SELECT output_path,payload_json,workflow FROM automation_jobs WHERE id=? AND workspace_id=? AND status='Succeeded'",
            (job_id, session["workspace_id"]),
        ).fetchone()
    if not row:
        return jsonify(error="Completed automation job not found."), 404
    try:
        output = _resolve_job_output(session["workspace_id"], job_id, row)
    except ValueError as exc:
        return jsonify(error=str(exc)), 404
    files = []
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, json.JSONDecodeError):
        return jsonify(error="Automation output manifest is invalid."), 500
    try:
        uses_custom_output = bool(payload.get("targetFolder")) and (
            output == Path(payload["targetFolder"]).resolve()
        )
        candidates = (
            _manifest_output_files(output, payload)
            if uses_custom_output
            else sorted(output.rglob("*"), key=lambda item: str(item).casefold())
        )
        for path in candidates:
            if (
                path.is_symlink()
                or not path.is_file()
                or path.name.endswith(".metadata.json")
            ):
                continue
            resolved = path.resolve(strict=True)
            if output not in resolved.parents:
                continue
            files.append({
                "name": path.name,
                "path": path.relative_to(output).as_posix(),
                "bytes": path.stat().st_size,
                "download": f"/api/automation/jobs/{job_id}/files/{quote(path.relative_to(output).as_posix(), safe='/')}",
            })
            if len(files) >= 500:
                break
    except OSError:
        return jsonify(error="Could not read the completed output folder."), 500
    return jsonify(files=files)


@automation_api.get("/api/automation/jobs/<job_id>/files/<path:relative_path>")
def download_automation_job_file(job_id: str, relative_path: str):
    with server.db() as con:
        row = con.execute(
            "SELECT output_path,payload_json FROM automation_jobs WHERE id=? AND workspace_id=? AND status='Succeeded'",
            (job_id, session["workspace_id"]),
        ).fetchone()
    if not row:
        return jsonify(error="Completed automation output not found."), 404
    try:
        output = _resolve_job_output(session["workspace_id"], job_id, row)
    except ValueError as exc:
        return jsonify(error=str(exc)), 404
    candidate = output.joinpath(*Path(relative_path).parts)
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return jsonify(error="Output file not found."), 404
    if candidate.is_symlink() or not resolved.is_file() or output not in resolved.parents:
        return jsonify(error="Output file not found."), 404
    return send_file(resolved, as_attachment=True, download_name=resolved.name)


import server
