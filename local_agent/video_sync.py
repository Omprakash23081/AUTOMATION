from __future__ import annotations

import re
import sys
import threading
from pathlib import Path
from typing import Callable

from local_agent.references import LocalFileReferences


class VideoProcessingCancelled(RuntimeError):
    pass


JOB_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def _video_edit_module():
    automation_root = Path(__file__).resolve().parents[1] / "YOUTUBE_VIDEO_AUTOMATION"
    if not (automation_root / "youtube_automation" / "video_edit.py").is_file():
        raise RuntimeError("The existing local video-edit module was not found.")
    if str(automation_root) not in sys.path:
        sys.path.insert(0, str(automation_root))
    from youtube_automation import video_edit

    return video_edit


def process_video_sync(
    job_id: str,
    attempt: int,
    payload: dict,
    references: LocalFileReferences,
    cancel_event: threading.Event,
    progress_callback: Callable[[int, str], None],
) -> Path:
    if not JOB_ID_PATTERN.fullmatch(job_id):
        raise ValueError("The local video job identifier is invalid.")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ValueError("The local video job attempt is invalid.")

    try:
        local_paths = references.resolve_video_job(payload)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            "A selected local media file or output folder is no longer available."
        ) from exc
    video_path = local_paths["videoRef"]
    audio_path = local_paths["audioRef"]
    output_path = local_paths["outputRef"]
    video_edit = _video_edit_module()
    ffmpeg_path = video_edit.imageio_ffmpeg.get_ffmpeg_exe()
    if output_path.exists():
        if output_path.is_symlink() or not output_path.is_file() or output_path.stat().st_size <= 0:
            raise FileExistsError(f"The output path is not a valid local MP4: {output_path}")
        if video_edit.probe_duration(ffmpeg_path, output_path) > 0:
            return output_path
        raise FileExistsError(f"The output path already exists: {output_path}")
    video_duration = video_edit.probe_duration(ffmpeg_path, video_path)
    audio_duration = video_edit.probe_duration(ffmpeg_path, audio_path)
    if video_duration <= 0 or audio_duration <= 0:
        raise ValueError("The selected video and narration must have positive duration.")
    if not 0.25 <= video_duration / audio_duration <= 4.0:
        raise ValueError(
            "The video-to-narration duration ratio must be between 0.25 and 4."
        )
    if cancel_event.is_set():
        raise VideoProcessingCancelled("Video processing was canceled.")

    speed = video_duration / audio_duration
    scene_plan = video_edit.build_scene_plan_from_speeds(
        [(0.0, video_duration, speed)]
    )
    progress_callback(5, "Checking local video and narration")
    try:
        result = video_edit.render_video(
            video_path,
            audio_path,
            output_path,
            scene_plan,
            ffmpeg_path,
            audio_duration,
            cancel_event=cancel_event,
            progress_callback=progress_callback,
        )
    except video_edit.ProcessingCancelled as exc:
        raise VideoProcessingCancelled(str(exc)) from exc
    if cancel_event.is_set():
        raise VideoProcessingCancelled("Video processing was canceled.")
    if (
        not result.is_file()
        or result.is_symlink()
        or result.stat().st_size <= 0
        or result.parent.resolve(strict=True) != output_path.parent
    ):
        raise RuntimeError("The local video renderer did not produce a valid MP4.")
    if video_edit.probe_duration(ffmpeg_path, result) <= 0:
        raise RuntimeError("The local video export has no valid media duration.")
    return result
