import argparse
import html
import math
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import imageio_ffmpeg
import requests
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


TRANSCRIPT_HTTP_TIMEOUT = (10, 30)
MAX_TTS_VIDEO_DURATION_SECONDS = 3 * 60
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LINKS_FILE = PROJECT_ROOT / "ytlink.txt"
DEFAULT_OUTPUT_DIRECTORY = Path(r"H:\YOU TUBE\THE MORTAL WHO SURVIVES")
YOUTUBE_FORMAT_SELECTOR = (
    "bestvideo[height=1080][width<=1920][ext=mp4]+bestaudio[ext=m4a]/"
    "bestvideo[height=1080][width<=1920]+bestaudio[ext=m4a]/"
    "best[height=1080][width<=1920][ext=mp4]"
)
YOUTUBE_BATCH_STEPS = (
    "download",
    "transcript",
    "english",
    "tts",
    "timestamped_transcript",
)


def _format_size(byte_count):
    size = float(byte_count)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024


def find_downloaded_file(info, temporary_directory):
    candidates = []
    if isinstance(info, dict):
        candidates.extend((info.get("filepath"), info.get("_filename")))
    candidates.extend(Path(temporary_directory).glob("source.*"))

    for candidate in candidates:
        if candidate:
            path = Path(candidate)
            if path.is_file() and not path.name.endswith((".part", ".ytdl")):
                return path
    raise RuntimeError("yt-dlp completed without producing a video file")


def build_ffmpeg_command(
    ffmpeg_path,
    source_path,
    output_path,
    video_options=("-c:v", "copy"),
):
    return [
        str(ffmpeg_path),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostats",
        "-progress",
        "pipe:1",
        "-y",
        "-i",
        str(source_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        *video_options,
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def _format_duration(seconds):
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def run_ffmpeg_with_progress(command, duration_seconds):
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    progress_data = {}

    for line in process.stdout:
        key, separator, value = line.rstrip().partition("=")
        if not separator:
            if line.strip():
                print(f"[FFmpeg] {line.rstrip()}")
            continue

        progress_data[key] = value
        if key != "progress":
            continue

        elapsed = progress_data.get("out_time_us")
        speed = progress_data.get("speed", "unavailable")
        if elapsed and duration_seconds:
            elapsed_seconds = int(elapsed) / 1_000_000
            percent = min(100, int(elapsed_seconds * 100 / duration_seconds))
            status = (
                f"MP4 packaging: {percent}% | "
                f"{_format_duration(elapsed_seconds)} / "
                f"{_format_duration(duration_seconds)} | {speed}"
            )
        else:
            status = f"MP4 packaging in progress | {speed}"
        print(status)
        progress_data.clear()

    return_code = process.wait()
    if return_code:
        raise subprocess.CalledProcessError(return_code, command)


def get_video_dimensions(video_path, ffmpeg_path):
    result = subprocess.run(
        [str(ffmpeg_path), "-hide_banner", "-i", str(video_path)],
        capture_output=True,
        text=True,
    )
    match = re.search(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})\b", result.stderr, re.DOTALL)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def get_video_duration(video_path, ffmpeg_path):
    result = subprocess.run(
        [str(ffmpeg_path), "-hide_banner", "-i", str(video_path)],
        capture_output=True,
        text=True,
    )
    match = re.search(
        r"\bDuration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)",
        result.stderr,
    )
    if not match:
        raise RuntimeError(f"Could not determine video duration: {video_path}")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def is_standard_output(video_path, ffmpeg_path):
    video_path = Path(video_path)
    return (
        video_path.is_file()
        and video_path.stat().st_size > 0
        and get_video_dimensions(video_path, ffmpeg_path) is not None
    )


class _YoutubeDLLogger:
    def __init__(self):
        self.latest_debug_message = None

    def debug(self, message):
        if message.startswith("[debug] "):
            self.latest_debug_message = message[len("[debug] "):]

    def warning(self, message):
        pass

    def error(self, message):
        pass


def _make_download_progress_hook():
    last_status = None
    started = False
    output_lock = threading.Lock()

    def report_progress(progress):
        nonlocal last_status, started
        with output_lock:
            if progress.get("status") == "finished":
                return
            if progress.get("status") != "downloading":
                return

            downloaded = progress.get("downloaded_bytes", 0)
            total = progress.get("total_bytes") or progress.get("total_bytes_estimate")
            speed = progress.get("speed")
            eta = progress.get("eta")
            if total:
                percent = min(100, int(downloaded * 100 / total))
                speed_text = f"{_format_size(speed)}/s" if speed else "speed unavailable"
                eta_text = (
                    f"ETA {int(eta) // 3600:02d}:{(int(eta) % 3600) // 60:02d}:{int(eta) % 60:02d}"
                    if eta is not None
                    else "ETA unavailable"
                )
                status = (
                    f"YouTube download: {percent}% | "
                    f"{_format_size(downloaded)} / {_format_size(total)} | "
                    f"{speed_text} | {eta_text}"
                )
                if status != last_status:
                    print(status, end="\r", flush=True)
                    last_status = status
            elif not started:
                print("Downloading YouTube video data...", flush=True)
                started = True

    return report_progress


def download_video(url, output_path, *, authorized):
    if not authorized:
        raise PermissionError("Confirm that you own or may download this video")
    if not url.strip():
        raise ValueError("A YouTube video URL is required")

    output_path = Path(output_path)
    if output_path.suffix.lower() != ".mp4":
        raise ValueError("Output path must use the .mp4 extension")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    with tempfile.TemporaryDirectory(prefix="youtube_download_") as temporary_directory:
        progress_hook = _make_download_progress_hook()
        logger = _YoutubeDLLogger()
        options = {
            "format": YOUTUBE_FORMAT_SELECTOR,
            "merge_output_format": "mkv",
            "outtmpl": str(Path(temporary_directory) / "source.%(ext)s"),
            "ffmpeg_location": ffmpeg_path,
            "js_runtimes": {"node": {}},
            "noplaylist": True,
            "quiet": True,
            "verbose": True,
            "logger": logger,
            "noprogress": True,
            "no_warnings": True,
            "retries": 3,
            "concurrent_fragment_downloads": 8,
            "progress_hooks": [progress_hook],
        }
        print(f"Downloading source: {url}")
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
        except DownloadError as error:
            if "requested format is not available" in str(error).lower():
                raise RuntimeError(
                    "1080p is not available for this video. "
                    "No lower-resolution version was downloaded."
                ) from error
            if "conversion failed" in str(error).lower() and logger.latest_debug_message:
                raise RuntimeError(
                    f"{error}\nFFmpeg diagnostics:\n{logger.latest_debug_message}"
                ) from error
            raise
        print("Download finished; preparing the video...")
        source_path = find_downloaded_file(info, temporary_directory)
        print(f"Preparing MP4 (keeping original video resolution): {output_path}")

        with tempfile.NamedTemporaryFile(
            dir=output_path.parent,
            prefix=f".{output_path.stem}.",
            suffix=".mp4",
            delete=False,
        ) as temporary_file:
            temporary_output = Path(temporary_file.name)

        try:
            command = build_ffmpeg_command(ffmpeg_path, source_path, temporary_output)
            run_ffmpeg_with_progress(command, info.get("duration"))
            if not temporary_output.is_file() or temporary_output.stat().st_size == 0:
                raise RuntimeError("FFmpeg did not create the output video")
            temporary_output.replace(output_path)
            print(f"Saved video: {output_path}")
        except subprocess.CalledProcessError as error:
            raise RuntimeError(
                f"FFmpeg MP4 packaging failed with exit code {error.returncode}"
            ) from error
        finally:
            temporary_output.unlink(missing_ok=True)

    return output_path


def _clean_transcript_line(line):
    text = re.sub(r"<[^>]*>", "", html.unescape(line)).strip()
    return re.sub(r"\s+", " ", text).strip()


def filter_transcript_lines(lines):
    filtered = []
    previous_text = ""
    for line in lines:
        text = _clean_transcript_line(line)
        if (
            not text
            or text == "WEBVTT"
            or text.startswith(("NOTE", "STYLE", "REGION", "Kind:", "Language:"))
            or "-->" in text
            or re.fullmatch(r"\d+", text)
        ):
            continue

        words = text.split()
        normalized = [re.sub(r"[^\w']", "", word).casefold() for word in words]
        previous_words = previous_text.split()
        previous_normalized = [
            re.sub(r"[^\w']", "", word).casefold() for word in previous_words
        ]
        if normalized == previous_normalized:
            continue

        overlap = min(len(previous_normalized), len(normalized))
        while overlap >= 2 and previous_normalized[-overlap:] != normalized[:overlap]:
            overlap -= 1
        if overlap >= 2:
            words = words[overlap:]
            text = " ".join(words)
        if text:
            filtered.append(text)
            previous_text = text
    return filtered


def contains_hindi(lines):
    return any("\u0900" <= character <= "\u097f" for line in lines for character in line)


def _write_transcript(output_path, lines):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=output_path.parent,
        prefix=f".{output_path.stem}.",
        suffix=".txt",
        delete=False,
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)
        temporary_file.write("\n".join(lines) + "\n")

    try:
        temporary_path.replace(output_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return output_path


def _format_timestamped_transcript(snippets):
    snippets_by_second = {}
    for snippet in snippets:
        try:
            start = float(snippet.start)
        except (AttributeError, TypeError, ValueError, OverflowError) as error:
            raise RuntimeError("YouTube returned a transcript cue without a valid start time") from error
        if not math.isfinite(start) or start < 0:
            raise RuntimeError("YouTube returned a transcript cue without a valid start time")

        text = _clean_transcript_line(snippet.text)
        if (
            not text
            or text == "WEBVTT"
            or text.startswith(("NOTE", "STYLE", "REGION", "Kind:", "Language:"))
            or "-->" in text
            or re.fullmatch(r"\d+", text)
        ):
            continue
        snippets_by_second.setdefault(int(start), []).append(text)

    lines = []
    for second, cues in sorted(snippets_by_second.items()):
        text = " ".join(filter_transcript_lines(cues))
        if text:
            lines.append(f"[{_format_duration(second)}] {text}")
    return lines


def clean_existing_transcript(transcript_path):
    transcript_path = Path(transcript_path)
    original = transcript_path.read_text(encoding="utf-8-sig")
    cleaned_lines = filter_transcript_lines(original.splitlines())
    if not cleaned_lines:
        raise RuntimeError(f"Transcript contains no usable text: {transcript_path}")
    cleaned = "\n".join(cleaned_lines) + "\n"
    if cleaned != original:
        _write_transcript(transcript_path, cleaned_lines)
        print(f"Filtered existing transcript: {transcript_path}")
    return transcript_path


def download_transcript(
    url,
    output_path,
    *,
    authorized,
    language="en",
    timestamped=False,
):
    if not authorized:
        raise PermissionError("Confirm that you own or may download this video")
    if not url.strip():
        raise ValueError("A YouTube video URL is required")
    if language not in {"en", "hi"}:
        raise ValueError("Transcript language must be 'en' or 'hi'")

    output_path = Path(output_path)
    if output_path.suffix.lower() != ".txt":
        raise ValueError("Transcript output path must use the .txt extension")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    video_id = extract_video_id(url)
    from youtube_transcript_api import NoTranscriptFound, YouTubeTranscriptApi

    with TranscriptTimeoutSession() as http_client:
        api = YouTubeTranscriptApi(http_client=http_client)
        try:
            transcript = api.fetch(video_id, languages=[language])
        except NoTranscriptFound:
            if language != "en":
                raise RuntimeError(
                    f"No Hindi captions are available for {url}."
                )
            try:
                transcript = api.fetch(video_id, languages=["hi"])
            except NoTranscriptFound as hindi_error:
                raise RuntimeError(
                    f"No English or Hindi captions are available for {url}."
                ) from hindi_error

    transcript_lines = (
        _format_timestamped_transcript(transcript.snippets)
        if timestamped
        else filter_transcript_lines(snippet.text for snippet in transcript.snippets)
    )
    if not transcript_lines:
        raise RuntimeError(
            f"YouTube returned an empty {'timestamped ' if timestamped else ''}transcript for {url}"
        )

    _write_transcript(output_path, transcript_lines)
    print(f"Saved transcript: {output_path}")
    return output_path


def process_transcript(transcript_path):
    transcript_path = Path(transcript_path)
    from ..chatgpt.english import output_path_for, translate_file

    return translate_file(transcript_path, output_path_for(transcript_path))


def generate_transcript_audio(transcript_path, output_directory):
    from ..cartesia.tts import generate_english_audio

    transcript_path = Path(transcript_path)
    audio_path = Path(output_directory) / "audio" / f"{transcript_path.stem}.wav"
    print(f"Generating Cartesia speech: {transcript_path}")
    return generate_english_audio(transcript_path, audio_path)


class TranscriptTimeoutSession(requests.Session):
    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", TRANSCRIPT_HTTP_TIMEOUT)
        return super().request(method, url, **kwargs)


def extract_video_id(url):
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/", 1)[0]
    elif host == "youtube.com" or host.endswith(".youtube.com"):
        if parsed.path.rstrip("/") == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        else:
            parts = [part for part in parsed.path.split("/") if part]
            video_id = parts[1] if len(parts) >= 2 and parts[0] in {
                "shorts", "embed", "live", "v",
            } else ""
    else:
        video_id = ""

    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise ValueError(f"Could not find a valid YouTube video ID in URL: {url}")
    return video_id


def _safe_filename_prefix(value):
    prefix = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value.strip())
    prefix = re.sub(r"_+", "_", prefix)
    return prefix.strip(" ._-")[:100].strip(" ._-") or "video"


def download_links(
    links_file,
    output_directory,
    *,
    authorized,
    transcript_language="en",
    start_number=1,
    name_prefix=None,
    steps=None,
):
    if not authorized:
        raise PermissionError("Confirm that you own or may download these videos")
    if transcript_language not in {"en", "hi"}:
        raise ValueError("Transcript language must be 'en' or 'hi'")
    if isinstance(start_number, bool) or not isinstance(start_number, int) or start_number < 1:
        raise ValueError("Starting video number must be a positive integer")
    selected_steps = set(YOUTUBE_BATCH_STEPS if steps is None else steps)
    invalid_steps = selected_steps.difference(YOUTUBE_BATCH_STEPS)
    if invalid_steps:
        raise ValueError(
            "Unsupported YouTube batch step(s): "
            + ", ".join(sorted(invalid_steps))
        )
    if not selected_steps:
        raise ValueError("Select at least one YouTube batch step")

    links_file = Path(links_file)
    if not links_file.is_file():
        raise FileNotFoundError(f"Video URL list not found: {links_file}")

    urls = [
        line.strip()
        for line in links_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not urls:
        raise ValueError(f"No video URLs found in {links_file}")

    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    video_directory = output_directory / "videos"
    transcript_directory = output_directory / "transcripts"
    audio_directory = output_directory / "audio"
    video_directory.mkdir(parents=True, exist_ok=True)
    transcript_directory.mkdir(parents=True, exist_ok=True)
    audio_directory.mkdir(parents=True, exist_ok=True)
    name_prefix = _safe_filename_prefix(name_prefix or output_directory.name or "video")
    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe() if "download" in selected_steps else None
    completed = []
    failures = []

    for index, url in enumerate(urls, start=start_number):
        file_stem = f"{name_prefix}{index:03}"
        output_path = video_directory / f"{file_stem}.mp4"
        transcript_path = transcript_directory / f"{file_stem}.txt"
        if "download" in selected_steps:
            if is_standard_output(output_path, ffmpeg_path):
                print(f"Skipping existing video: {output_path}")
                completed.append(output_path)
            else:
                try:
                    completed.append(download_video(url, output_path, authorized=True))
                except Exception as error:
                    failures.append((index, error))
                    print(f"Failed URL {index}: {error}")
                    continue
        else:
            print(f"Skipping video download (not selected): {output_path}")

        english_transcript_path = transcript_path.with_name(
            f"{transcript_path.stem}_english.txt"
        )
        needs_english = "english" in selected_steps or "tts" in selected_steps
        english_output_exists = (
            english_transcript_path.is_file()
            and english_transcript_path.stat().st_size > 0
        )
        needs_source_transcript = (
            "transcript" in selected_steps
            or (needs_english and ("english" in selected_steps or not english_output_exists))
        )
        transcript_exists = transcript_path.is_file() and transcript_path.stat().st_size > 0
        if transcript_exists and needs_source_transcript:
            try:
                clean_existing_transcript(transcript_path)
                existing_lines = transcript_path.read_text(
                    encoding="utf-8-sig"
                ).splitlines()
                is_timestamped = any(
                    re.match(r"^\s*\[\d+:[0-5]\d:[0-5]\d\]", line)
                    for line in existing_lines
                )
                wrong_language = (
                    transcript_language == "hi"
                    and not contains_hindi(existing_lines)
                )
                if wrong_language or (
                    "transcript" in selected_steps and is_timestamped
                ):
                    transcript_exists = False
                else:
                    print(f"Skipping existing transcript: {transcript_path}")
            except Exception as error:
                failures.append((index, error))
                print(f"Failed to filter existing transcript {index}: {error}")
                transcript_exists = False

        if needs_source_transcript and not transcript_exists:
            try:
                if "transcript" not in selected_steps:
                    print(f"Fetching source transcript prerequisite: {transcript_path}")
                download_transcript(
                    url,
                    transcript_path,
                    authorized=True,
                    language=transcript_language,
                )
                transcript_exists = True
            except Exception as error:
                failures.append((index, error))
                print(f"Failed transcript {index}: {error}")
                continue

        if needs_english and ("english" in selected_steps or not english_output_exists):
            try:
                english_transcript_path = Path(process_transcript(transcript_path))
                if (
                    not english_transcript_path.is_file()
                    or not english_transcript_path.stat().st_size
                ):
                    raise RuntimeError(
                        f"Processed English TTS script is missing or empty: "
                        f"{english_transcript_path}"
                    )
            except Exception as error:
                failures.append((index, error))
                print(f"Failed English transcript {index}: {error}")
                continue

        if "tts" in selected_steps:
            try:
                video_duration = None
                if "download" in selected_steps or (
                    output_path.is_file() and output_path.stat().st_size
                ):
                    video_duration = get_video_duration(
                        output_path,
                        ffmpeg_path or imageio_ffmpeg.get_ffmpeg_exe(),
                    )
                if (
                    video_duration is not None
                    and video_duration > MAX_TTS_VIDEO_DURATION_SECONDS
                ):
                    print(
                        f"Skipping Cartesia TTS for {output_path.name}: "
                        f"video duration ({_format_duration(video_duration)}) exceeds "
                        f"the 3-minute limit."
                    )
                else:
                    generate_transcript_audio(
                        english_transcript_path,
                        output_directory,
                    )
            except Exception as error:
                failures.append((index, error))
                print(f"Failed Cartesia processing {index}: {error}")
        else:
            print(f"Skipping Cartesia TTS (not selected): {english_transcript_path}")

        if "timestamped_transcript" in selected_steps:
            try:
                download_transcript(
                    url,
                    transcript_path,
                    authorized=True,
                    language=transcript_language,
                    timestamped=True,
                )
            except Exception as error:
                failures.append((index, error))
                print(f"Failed timestamped transcript {index}: {error}")

    if failures:
        failed_items = ", ".join(str(index) for index, _ in failures)
        raise RuntimeError(f"Failed to process URL(s): {failed_items}")

    return completed


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Download authorized YouTube URLs from a text file and package them as MP4 without resizing."
    )
    parser.add_argument(
        "--links",
        type=Path,
        default=DEFAULT_LINKS_FILE,
        help=f"Text file with one YouTube URL per line (default: {DEFAULT_LINKS_FILE})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIRECTORY,
        help=(
            "Output folder for videos and transcripts "
            f"(default: {DEFAULT_OUTPUT_DIRECTORY})"
        ),
    )
    parser.add_argument(
        "--authorized",
        action="store_true",
        required=True,
        help="Confirm you own or have permission to download this video",
    )
    parser.add_argument(
        "--transcript-language",
        choices=("en", "hi"),
        help="Transcript language (en or hi); if omitted, you will be asked before downloads start",
    )
    parser.add_argument(
        "--start-number",
        type=int,
        default=1,
        help="Starting number for video and caption filenames",
    )
    parser.add_argument(
        "--name-prefix",
        help="Prefix for video and caption filenames",
    )
    parser.add_argument(
        "--steps",
        help=(
            "Comma-separated pipeline steps: "
            + ", ".join(YOUTUBE_BATCH_STEPS)
            + " (defaults to all steps)"
        ),
    )
    args = parser.parse_args(argv)

    print(
        f"YouTube batch source: {Path(__file__).resolve()} | "
        f"Python: {Path(sys.executable).resolve()}"
    )
    transcript_language = args.transcript_language or prompt_transcript_language()
    outputs = download_links(
        args.links,
        args.output_dir,
        authorized=args.authorized,
        transcript_language=transcript_language,
        start_number=args.start_number,
        name_prefix=args.name_prefix,
        steps=(
            [step.strip() for step in args.steps.split(",") if step.strip()]
            if args.steps
            else None
        ),
    )
    print(f"Completed {len(outputs)} video(s) in {args.output_dir}")


def prompt_transcript_language():
    choices = {"1": "en", "2": "hi", "en": "en", "hi": "hi"}
    while True:
        print("Choose the transcript language for this download batch:")
        print("1. English")
        print("2. Hindi")
        choice = input("Enter 1 or 2 (or en/hi): ").strip().lower()
        if choice in choices:
            return choices[choice]
        print("Invalid choice. Please enter 1 (English) or 2 (Hindi).")


if __name__ == "__main__":
    main()