from __future__ import annotations

import threading
from pathlib import Path
from urllib.parse import urlparse

import imageio_ffmpeg
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "www.youtu.be",
    "www.youtube-nocookie.com",
}
SUPPORTED_BROWSERS = {"chrome", "edge", "firefox"}


class YoutubeDownloadCancelled(RuntimeError):
    pass


def is_authentication_required(message: str) -> bool:
    text = str(message).lower()
    return (
        "sign in to confirm you're not a bot" in text
        or "use --cookies-from-browser" in text
        or ("use --cookies" in text and "sign in" in text)
    )


def download_youtube_source(
    url: str,
    output_directory: Path,
    browser: str | None,
    cancel_event: threading.Event,
    progress_callback,
) -> Path:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in YOUTUBE_HOSTS:
        raise ValueError("The local agent only downloads HTTPS YouTube video URLs.")
    if browser is not None and browser not in SUPPORTED_BROWSERS:
        raise ValueError("Choose Chrome, Edge, or Firefox for the local browser session.")

    output_directory.mkdir(parents=True, exist_ok=True)
    options = {
        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
        "merge_output_format": "mp4",
        "outtmpl": str(output_directory / "source.%(ext)s"),
        "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 2,
        "fragment_retries": 2,
        "socket_timeout": 20,
        "progress_hooks": [],
    }
    if browser:
        options["cookiesfrombrowser"] = (browser, None, None, None)

    def report_progress(info):
        if cancel_event.is_set():
            raise YoutubeDownloadCancelled("The local YouTube download was canceled.")
        if info.get("status") != "downloading":
            return
        downloaded = info.get("downloaded_bytes")
        total = info.get("total_bytes") or info.get("total_bytes_estimate")
        percent = round(99 * downloaded / total) if downloaded and total else 0
        progress_callback(
            max(0, min(99, percent)),
            "Downloading YouTube source on Windows",
        )

    options["progress_hooks"] = [report_progress]
    try:
        with YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=True)
            candidates = [
                info.get("filepath"),
                info.get("_filename"),
                str(Path(downloader.prepare_filename(info)).with_suffix(".mp4")),
                *map(str, output_directory.glob("source.*")),
            ]
    except DownloadError as exc:
        if is_authentication_required(exc):
            raise RuntimeError(
                "YouTube still requires authentication. Sign in to the selected "
                "browser profile, then retry the Windows-agent download."
            ) from None
        raise RuntimeError(
            "The Windows agent could not download this video. Check the local agent "
            "window for the yt-dlp error."
        ) from None

    if cancel_event.is_set():
        raise YoutubeDownloadCancelled("The local YouTube download was canceled.")
    for candidate in candidates:
        if candidate:
            path = Path(candidate)
            if path.is_file() and path.stat().st_size and path.suffix.lower() in {
                ".mp4",
                ".mkv",
                ".webm",
                ".mov",
                ".m4v",
            }:
                return path
    raise RuntimeError("yt-dlp completed without producing a usable video file.")
