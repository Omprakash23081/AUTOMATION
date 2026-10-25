"""Use semantic speed recommendations to synchronize video with narration."""

import argparse
from dataclasses import dataclass
import hashlib
import json
import re
import subprocess
import tempfile
import time
from collections import deque
from pathlib import Path

import imageio_ffmpeg

from . import config


class _LazyChatGPTBrowser:
    def __getattr__(self, name):
        from .chatgpt import browser

        return getattr(browser, name)


chatgpt_browser = _LazyChatGPTBrowser()


def sync_playwright():
    from playwright.sync_api import sync_playwright as playwright_factory

    return playwright_factory()


TRANSCRIPT_LINE_PATTERN = re.compile(
    r"^\s*\[(\d+):([0-5]\d):([0-5]\d(?:\.\d+)?)\]\s*(.*?)\s*$"
)
GEMINI_TIMESTAMP_PATTERN = re.compile(
    r"\[(\d+):([0-5]\d):([0-5]\d(?:\.\d+)?)\]"
)
GEMINI_META_TEXT_PATTERN = re.compile(
    r"\b(?:"
    r"(?:here|this|below)\s+is\s+(?:the\s+)?(?:full\s+)?transcript|"
    r"transcript\s+extracted\s+from|"
    r"i\s+have\s+(?:the\s+)?transcript|"
    r"would\s+you\s+like\s+me\s+to"
    r")\b",
    re.IGNORECASE,
)
GEMINI_VIDEO_EDIT_PROMPT = """
Extract the full transcript from the attached audio in the language spoken.
Do not translate or hallucinate. Return only the transcript, timestamped at
each phrase's start. Timestamps must be inside the audio, not at its end:
[HH:MM:SS] words

Put the transcript in one plain-text code block.
"""
CHATGPT_VIDEO_SPEED_PROMPT = """
You are given two timestamped transcripts describing the same video: the source
video transcript and its narration audio transcript. The transcripts may be in
different languages. Compare their meaning and timing and recommend playback speeds for
the SOURCE VIDEO timeline so its visual scenes fit the narration.

Return only a JSON object. Each key must be a contiguous source-video time range
from 0 seconds through the exact video duration, formatted
"[HH:MM:SS]-[HH:MM:SS]". Each value must be a string such as "0.75X", where
0.5X means half normal playback speed (slower) and 1.5X means one-and-a-half
times normal speed (faster). Include every part of the full video, even time
after the last transcript line. Ranges must not overlap or leave gaps. Choose
moderate speeds, normally between 0.5X and 2.0X, and use meaning—not exact word
matching—to match source scenes to the narration. Respond with no prose or
markdown, only the JSON object.
"""


@dataclass(frozen=True)
class TimedLine:
    timestamp: float
    text: str


@dataclass(frozen=True)
class SceneTiming:
    source_start: float
    source_end: float
    target_start: float
    target_end: float
    freeze_frame: bool = False

    @property
    def source_duration(self):
        return self.source_end - self.source_start

    @property
    def target_duration(self):
        return self.target_end - self.target_start


def parse_timestamped_transcript(text, *, allow_silence=False):
    lines = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip():
            continue
        match = TRANSCRIPT_LINE_PATTERN.fullmatch(raw_line)
        if not match:
            raise ValueError(
                f"Transcript line {line_number} must use "
                "'[HH:MM:SS] spoken text' format"
            )
        hours, minutes, seconds, content = match.groups()
        content = content.strip()
        if not content or (allow_silence and content.casefold() == "[silence]"):
            continue
        timestamp = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        if lines and timestamp <= lines[-1].timestamp:
            raise ValueError(
                f"Transcript timestamps must increase; line {line_number} is out of order"
            )
        lines.append(TimedLine(timestamp, content))
    if not lines:
        raise ValueError("Transcript contains no timestamped spoken lines")
    return lines


def parse_gemini_audio_transcript(response):
    filtered_lines = []
    for raw_line in response.splitlines():
        timestamps = list(GEMINI_TIMESTAMP_PATTERN.finditer(raw_line))
        for index, timestamp_match in enumerate(timestamps):
            content_end = (
                timestamps[index + 1].start()
                if index + 1 < len(timestamps)
                else len(raw_line)
            )
            content = raw_line[timestamp_match.end():content_end].strip()
            content = re.sub(r"[*_`]", "", content).strip()
            content = content.strip("-• \t")
            if content:
                timestamp = timestamp_match.group(0)
                filtered_lines.append(f"{timestamp} {content}")

    filtered_response = "\n".join(filtered_lines)
    try:
        return parse_timestamped_transcript(
            filtered_response,
            allow_silence=True,
        )
    except ValueError as error:
        preview = " ".join(response.split())[:240]
        raise ValueError(
            "Gemini returned no usable timestamped speech. Untimestamped "
            "paragraphs and commentary were discarded; no transcript was invented. "
            f"Parser detail: {error}. Response preview: {ascii(preview)}"
        ) from error


def validate_gemini_audio_transcript(response, audio_duration):
    meta_match = GEMINI_META_TEXT_PATTERN.search(response)
    if meta_match:
        preview = " ".join(response.split())[:240]
        raise ValueError(
            "Gemini response contains assistant commentary instead of only "
            f"spoken audio: {ascii(preview)}"
        )
    audio_lines = parse_gemini_audio_transcript(response)
    return normalize_audio_transcript_end(audio_lines, audio_duration)


def normalize_audio_transcript_end(audio_lines, audio_duration):
    return _normalize_transcript_end(
        audio_lines,
        audio_duration,
        "Gemini",
        "audio",
    )


def normalize_source_transcript_end(source_lines, video_duration):
    return _normalize_transcript_end(
        source_lines,
        video_duration,
        "Source-video",
        "video",
    )


def _normalize_transcript_end(lines, media_duration, transcript_source, media_name):
    if lines[-1].timestamp < media_duration:
        return lines

    last_valid_index = next(
        (
            index
            for index in range(len(lines) - 1, -1, -1)
            if lines[index].timestamp < media_duration
        ),
        None,
    )
    if last_valid_index is None:
        raise ValueError(
            f"{transcript_source} transcript has no timestamp before the "
            f"measured {media_name} duration; the transcript cannot be "
            f"placed on the {media_name} timeline."
        )

    tail_text = " ".join(
        line.text for line in lines[last_valid_index:]
    )
    merged_line = TimedLine(lines[last_valid_index].timestamp, tail_text)
    return [*lines[:last_valid_index], merged_line]


VIDEO_RANGE_PATTERN = re.compile(
    r"^\s*\[(\d+):([0-5]\d):([0-5]\d(?:\.\d+)?)\]"
    r"\s*-\s*"
    r"\[(\d+):([0-5]\d):([0-5]\d(?:\.\d+)?)\]\s*$"
)
PLAYBACK_SPEED_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*[xX]\s*$")
MAX_RECOMMENDED_SPEED = 4.0


def build_speed_recommendation_prompt(
    source_lines,
    audio_lines,
    video_duration,
    audio_duration,
):
    if not source_lines or not audio_lines:
        raise ValueError("Both timestamped transcripts must contain spoken lines")
    if any(line.timestamp >= video_duration for line in source_lines):
        raise ValueError(
            "A source-video transcript timestamp is at or beyond the video duration."
        )
    if any(line.timestamp >= audio_duration for line in audio_lines):
        raise ValueError(
            "A narration transcript timestamp is at or beyond the audio duration."
        )
    source_text = "\n".join(
        f"[{_format_timestamp_for_range(line.timestamp)}] {line.text}"
        for line in source_lines
    )
    audio_text = "\n".join(
        f"[{_format_timestamp_for_range(line.timestamp)}] {line.text}"
        for line in audio_lines
    )
    prompt = (
        f"{CHATGPT_VIDEO_SPEED_PROMPT}\n"
        f"Exact source video duration: "
        f"{_format_timestamp_for_range(video_duration)}\n"
        f"Exact narration audio duration: "
        f"{_format_timestamp_for_range(audio_duration)}\n\n"
        f"SOURCE VIDEO TRANSCRIPT:\n{source_text}\n\n"
        f"NARRATION AUDIO TRANSCRIPT:\n{audio_text}"
    )
    if len(prompt) > config.CHATGPT_MAX_INPUT_CHARS:
        raise ValueError(
            "The transcripts are too long for one ChatGPT video-edit request "
            f"({len(prompt)} characters; limit "
            f"{config.CHATGPT_MAX_INPUT_CHARS})."
        )
    return prompt


def _parse_range_timestamp(hours, minutes, seconds):
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_speed_recommendations(response, video_duration):
    decoder = json.JSONDecoder()
    recommendations = None
    for index, character in enumerate(response):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(response, index)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            recommendations = value
            break
    if recommendations is None or not recommendations:
        raise ValueError(
            "ChatGPT did not return a non-empty JSON object of video "
            "time ranges and playback speeds."
        )

    segments = []
    expected_start = 0.0
    recommendation_items = list(recommendations.items())
    for index, (range_text, speed_text) in enumerate(recommendation_items):
        range_match = VIDEO_RANGE_PATTERN.fullmatch(range_text)
        if range_match is None:
            raise ValueError(
                f"Invalid video time range from ChatGPT: {range_text!r}"
            )
        start = _parse_range_timestamp(*range_match.groups()[:3])
        end = _parse_range_timestamp(*range_match.groups()[3:])
        if not isinstance(speed_text, str):
            raise ValueError(
                f"Playback speed for {range_text!r} must be a string such as '0.75X'."
            )
        speed_match = PLAYBACK_SPEED_PATTERN.fullmatch(speed_text)
        if speed_match is None:
            raise ValueError(
                f"Invalid playback speed for {range_text!r}: {speed_text!r}"
            )
        speed = float(speed_match.group(1))
        if not 0 < speed <= MAX_RECOMMENDED_SPEED:
            raise ValueError(
                f"Playback speed for {range_text!r} must be greater than 0 "
                f"and no more than {MAX_RECOMMENDED_SPEED:.1f}X."
            )
        if abs(start - expected_start) > 0.01:
            raise ValueError(
                "ChatGPT video ranges must start at 00:00:00 and be "
                "contiguous without gaps or overlaps."
            )
        if (
            end == start
            and index == len(recommendation_items) - 1
            and abs(start - expected_start) <= 0.01
            and start <= video_duration + 0.01
        ):
            continue
        if end <= start or end > video_duration + 0.01:
            raise ValueError(
                f"ChatGPT returned an invalid or out-of-video range: {range_text!r}"
            )
        end = min(end, video_duration)
        segments.append((start, end, speed))
        expected_start = end

    if expected_start < video_duration:
        segments.append((expected_start, video_duration, 1.0))
        expected_start = video_duration
    if abs(expected_start - video_duration) > 0.01:
        raise ValueError(
            "ChatGPT playback-speed ranges must cover the full video "
            f"(ended at {expected_start:.3f}s; video is {video_duration:.3f}s)."
        )
    return segments


def build_scene_plan_from_speeds(segments):
    if not segments:
        raise ValueError("At least one playback-speed range is required")
    plan = []
    target_start = 0.0
    for source_start, source_end, speed in segments:
        target_end = target_start + (source_end - source_start) / speed
        plan.append(
            SceneTiming(
                source_start,
                source_end,
                target_start,
                target_end,
            )
        )
        target_start = target_end
    return plan


def calculate_global_speed_factor(edited_video_duration, audio_duration):
    if edited_video_duration <= 0 or audio_duration <= 0:
        raise ValueError(
            "Edited video and narration audio durations must both be positive."
        )
    return edited_video_duration / audio_duration


def _extract_complete_speed_json(text, video_duration, excluded_text=""):
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, end_index = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict) or not value:
            continue
        last_key = next(reversed(value))
        last_range = VIDEO_RANGE_PATTERN.fullmatch(last_key)
        if last_range is None:
            continue
        final_end = _parse_range_timestamp(*last_range.groups()[3:])
        if final_end > video_duration + 0.01 or video_duration - final_end > 1:
            continue
        response = text[index:end_index]
        try:
            parse_speed_recommendations(response, video_duration)
        except ValueError:
            continue
        if response in excluded_text:
            continue
        return response
    return ""


def _rendered_response_texts(page):
    from playwright.sync_api import Error as PlaywrightError

    texts = []
    selectors = (
        "pre code.language-json",
        "pre code",
        "[data-message-author-role='assistant']",
        "[data-testid^='conversation-turn-'] .markdown",
        ".agent-turn .markdown",
        "main .markdown",
        "main",
        "body",
    )
    for selector in selectors:
        try:
            messages = page.locator(selector)
            count = messages.count()
            if count:
                text = messages.last.inner_text().strip()
            else:
                text = ""
        except PlaywrightError:
            continue
        if text:
            texts.append(text)
    return texts


def _video_edit_response_is_streaming(page):
    from playwright.sync_api import Error as PlaywrightError

    if chatgpt_browser._is_response_generating(page):
        return True
    for selector in (
        ".result-streaming",
        "[data-is-streaming='true']",
        "[data-streaming='true']",
    ):
        try:
            indicator = page.locator(selector).last
            if indicator.count() and indicator.is_visible():
                return True
        except PlaywrightError:
            continue
    return False


def _send_speed_prompt(page, prompt, video_duration):
    from playwright.sync_api import Error as PlaywrightError, TimeoutError

    baseline_texts = _rendered_response_texts(page)
    baseline_text = "\n".join(baseline_texts)
    chat_input = chatgpt_browser.find_chat_input(page)
    chat_input.fill(prompt)
    send_buttons = [
        page.locator("button[data-testid='send-button']"),
        page.locator("button[aria-label*='Send' i]"),
        page.get_by_role("button", name=re.compile("send", re.IGNORECASE)),
    ]
    send_button = None
    for candidate in send_buttons:
        try:
            if candidate.last.is_visible():
                send_button = candidate.last
                break
        except PlaywrightError:
            continue
    if send_button is not None:
        send_button.click(timeout=15000)
    else:
        chat_input.press("Enter")

    chatgpt_browser.log("Waiting for response", "VIDEO-EDIT")
    timeout_seconds = config.CHATGPT_RESPONSE_TIMEOUT_SECONDS
    deadline = time.monotonic() + timeout_seconds
    previous_response = ""
    stable_polls = 0
    poll_count = 0
    detected_characters = 0
    generating = False

    while time.monotonic() < deadline:
        poll_count += 1
        current_texts = _rendered_response_texts(page)
        response = ""
        for current_text in current_texts:
            response = _extract_complete_speed_json(
                current_text,
                video_duration,
                excluded_text=baseline_text,
            )
            if response:
                break
        generating = _video_edit_response_is_streaming(page)
        if response and response == previous_response and not generating:
            stable_polls += 1
        else:
            stable_polls = 0
        previous_response = response
        detected_characters = len(response) if response else max(
            (len(text) for text in current_texts),
            default=0,
        )

        if response and not generating and stable_polls >= 2:
            chatgpt_browser.log(
                f"ChatGPT response received ({detected_characters} characters)",
                "VIDEO-EDIT",
            )
            return response
        if poll_count % 4 == 0:
            status = (
                f"Waiting for response to finish ({detected_characters} "
                "characters detected)."
                if response
                else (
                    "Waiting for a complete video-speed JSON response "
                    f"({detected_characters} rendered characters detected)."
                )
            )
            chatgpt_browser.log(status, "VIDEO-EDIT")
        page.wait_for_timeout(1000)

    raise TimeoutError(
        "ChatGPT video-speed response timed out "
        f"(JSON characters={detected_characters}, "
        f"generation indicator={'visible' if generating else 'not visible'})."
    )


def build_global_fit_filter(video_duration, audio_duration):
    if video_duration <= 0 or audio_duration <= 0:
        raise ValueError(
            "Edited video and narration audio durations must both be positive."
        )
    pts_scale = audio_duration / video_duration
    return (
        f"[0:v]setpts={pts_scale:.9f}*(PTS-STARTPTS),"
        "tpad=stop_mode=clone:stop_duration=1,"
        f"trim=duration={audio_duration:.6f},"
        "setpts=PTS-STARTPTS[outv]"
    )


def recommend_video_speeds(source_lines, audio_lines, video_duration, audio_duration):
    prompt = build_speed_recommendation_prompt(
        source_lines,
        audio_lines,
        video_duration,
        audio_duration,
    )
    with sync_playwright() as playwright:
        context = chatgpt_browser.launch_persistent_context(playwright)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            chatgpt_browser.start_new_chat(page, "VIDEO-EDIT")
            response = _send_speed_prompt(page, prompt, video_duration)
        finally:
            context.close()
    return parse_speed_recommendations(response, video_duration)


def _format_timestamp_for_range(timestamp):
    milliseconds = round(timestamp * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    seconds_text = (
        f"{seconds:02d}"
        if milliseconds == 0
        else f"{seconds:02d}.{milliseconds:03d}"
    )
    return f"{hours:02d}:{minutes:02d}:{seconds_text}"


def build_speed_report(scene_plan):
    report = {}
    for scene in scene_plan:
        source_range = (
            f"[{_format_timestamp_for_range(scene.source_start)}]-"
            f"[{_format_timestamp_for_range(scene.source_end)}]"
        )
        if scene.freeze_frame:
            report[source_range] = "FREEZE_FRAME"
        else:
            speed = scene.source_duration / scene.target_duration
            report[source_range] = f"{speed:.2f}X"
    return report


def build_video_filter(scene_plan):
    if not scene_plan:
        raise ValueError("At least one scene is required")
    filters = []
    labels = []
    for index, scene in enumerate(scene_plan):
        label = f"v{index}"
        labels.append(f"[{label}]")
        if scene.freeze_frame:
            filters.append(
                "[0:v]trim=start=0:duration=0.04,setpts=PTS-STARTPTS,"
                f"tpad=stop_mode=clone:stop_duration={scene.target_duration:.6f},"
                f"trim=duration={scene.target_duration:.6f},setpts=PTS-STARTPTS[{label}]"
            )
        else:
            pts_scale = scene.target_duration / scene.source_duration
            filters.append(
                f"[0:v]trim=start={scene.source_start:.6f}:end={scene.source_end:.6f},"
                f"setpts={pts_scale:.9f}*(PTS-STARTPTS)[{label}]"
            )
    target_duration = scene_plan[-1].target_end
    filters.extend(
        [
            f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[vconcat]",
            "[vconcat]tpad=stop_mode=clone:stop_duration=1,"
            f"trim=duration={target_duration:.6f},"
            "setpts=PTS-STARTPTS[outv]",
        ]
    )
    return ";".join(filters)


def probe_duration(ffmpeg_path, media_path):
    result = subprocess.run(
        [str(ffmpeg_path), "-hide_banner", "-i", str(media_path)],
        capture_output=True,
        text=True,
    )
    match = re.search(
        r"\bDuration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)",
        result.stderr,
    )
    if not match:
        raise RuntimeError(f"Could not determine media duration: {media_path}")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def transcribe_audio(audio_path):
    from .gemini import browser as gemini_browser

    with sync_playwright() as playwright:
        args = ["--disable-blink-features=AutomationControlled"]
        if config.PLAYWRIGHT_CHROME_PROFILE:
            args.append(f"--profile-directory={config.PLAYWRIGHT_CHROME_PROFILE}")
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=config.PLAYWRIGHT_PROFILE_DIR,
            headless=config.PLAYWRIGHT_HEADLESS,
            channel="chrome",
            args=args,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            return gemini_browser.extract_audio_response(
                page,
                str(audio_path),
                "VIDEO-EDIT",
                prompt_text=GEMINI_VIDEO_EDIT_PROMPT,
            )
        finally:
            context.close()


def _report_video_edit_progress(percent, detail):
    bounded_percent = max(0, min(100, int(percent)))
    print(f"VIDEO EDIT PROGRESS: {bounded_percent}|{detail}", flush=True)


def _report_video_edit_step(step_number, total_steps, description):
    print(
        f"VIDEO EDIT STEP: {step_number}/{total_steps}|{description}",
        flush=True,
    )


def _run_ffmpeg_with_progress(
    command,
    duration,
    phase,
    progress_start,
    progress_end,
    cancel_event=None,
    progress_callback=None,
):
    progress_command = [
        *command[:-1],
        "-nostats",
        "-progress",
        "pipe:1",
        command[-1],
    ]
    process = subprocess.Popen(
        progress_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    progress_data = {}
    error_lines = deque(maxlen=20)
    last_percent = None

    try:
        for line in process.stdout:
            if cancel_event is not None and cancel_event.is_set():
                raise ProcessingCancelled("Video processing was canceled.")
            key, separator, value = line.rstrip().partition("=")
            if not separator:
                if line.strip():
                    error_lines.append(line.strip())
                continue
            progress_data[key] = value
            if key != "progress":
                continue

            output_time = progress_data.get("out_time_us")
            if duration > 0:
                try:
                    output_time_microseconds = int(output_time)
                except (TypeError, ValueError):
                    output_time_microseconds = None
                if output_time_microseconds is not None:
                    elapsed = min(
                        duration,
                        max(0, output_time_microseconds) / 1_000_000,
                    )
                    fraction = elapsed / duration
                    percent = round(
                        progress_start
                        + (progress_end - progress_start) * fraction
                    )
                    if percent != last_percent:
                        detail = (
                            f"{phase}: {percent}% "
                            f"({elapsed:.1f}s / {duration:.1f}s; "
                            f"{progress_data.get('speed', 'speed unavailable')})"
                        )
                        _report_video_edit_progress(percent, detail)
                        if progress_callback is not None:
                            progress_callback(percent, detail)
                        last_percent = percent
            if value == "end":
                _report_video_edit_progress(
                    progress_end,
                    f"{phase}: complete",
                )
                if progress_callback is not None:
                    progress_callback(progress_end, f"{phase}: complete")
            progress_data.clear()

        return_code = process.wait()
    finally:
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                if process.poll() is None:
                    raise
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if process.stdout is not None:
            process.stdout.close()

    if return_code:
        raise RuntimeError(
            f"FFmpeg {phase.lower()} failed: "
            f"{'; '.join(error_lines) or return_code}"
        )


class ProcessingCancelled(RuntimeError):
    pass


def _audio_cache_path(audio_path, cache_dir):
    digest = hashlib.sha256()
    with Path(audio_path).open("rb") as audio_file:
        for chunk in iter(lambda: audio_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return Path(cache_dir) / f"{digest.hexdigest()}.txt"


def _load_or_transcribe_audio(audio_path, cache_dir, audio_duration):
    cache_path = (
        _audio_cache_path(audio_path, cache_dir)
        if cache_dir is not None
        else None
    )

    if cache_path is not None and cache_path.is_file():
        try:
            cached_response = cache_path.read_text(encoding="utf-8")
            if cached_response.strip():
                validate_gemini_audio_transcript(cached_response, audio_duration)
            else:
                raise ValueError("cached transcript is empty")
        except (ValueError, UnicodeError) as error:
            print(
                f"Discarding invalid cached Gemini transcript "
                f"{cache_path}: {error}"
            )
            cache_path.unlink()
        else:
            if cached_response.strip():
                print(f"Reusing validated Gemini transcript: {cache_path}")
                return cached_response, cache_path

    response = transcribe_audio(audio_path)
    if not response or not response.strip():
        raise ValueError("Gemini returned an empty audio transcript.")
    validate_gemini_audio_transcript(response, audio_duration)
    if cache_path is None:
        return response, None

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=cache_path.parent,
            prefix=f"{cache_path.stem}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(response)
        temporary_path.replace(cache_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return response, cache_path


def render_video(
    video_path,
    audio_path,
    output_path,
    scene_plan,
    ffmpeg_path,
    audio_duration,
    cancel_event=None,
    progress_callback=None,
):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    intermediate_path = None
    output_temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output_path.parent,
            prefix=f".{output_path.stem}.retimed.",
            suffix=".mp4",
            delete=False,
        ) as temporary_file:
            intermediate_path = Path(temporary_file.name)
        with tempfile.NamedTemporaryFile(
            dir=output_path.parent,
            prefix=f".{output_path.stem}.synced.",
            suffix=".mp4",
            delete=False,
        ) as temporary_file:
            output_temporary_path = Path(temporary_file.name)

        retime_command = [
            str(ffmpeg_path),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video_path),
            "-filter_complex",
            build_video_filter(scene_plan),
            "-map",
            "[outv]",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(intermediate_path),
        ]
        _report_video_edit_step(5, 7, "Retiming source video")
        _report_video_edit_progress(40, "Retiming source video")
        _run_ffmpeg_with_progress(
            retime_command,
            scene_plan[-1].target_end,
            "Retiming source video",
            40,
            65,
            cancel_event=cancel_event,
            progress_callback=progress_callback,
        )
        if not intermediate_path.is_file() or intermediate_path.stat().st_size == 0:
            raise RuntimeError("FFmpeg did not create the retimed intermediate video")

        edited_duration = probe_duration(ffmpeg_path, intermediate_path)
        global_speed_factor = calculate_global_speed_factor(
            edited_duration,
            audio_duration,
        )
        print(
            f"Retimed video is {edited_duration:.3f}s; narration is "
            f"{audio_duration:.3f}s. Applying global playback factor "
            f"{global_speed_factor:.4f}x."
        )
        _report_video_edit_step(
            6,
            7,
            "Matching final duration and adding narration audio",
        )
        _report_video_edit_progress(
            70,
            "Matching final duration and adding narration audio",
        )
        final_fit_command = [
            str(ffmpeg_path),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(intermediate_path),
            "-i",
            str(audio_path),
            "-filter_complex",
            build_global_fit_filter(edited_duration, audio_duration),
            "-map",
            "[outv]",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-t",
            f"{audio_duration:.6f}",
            "-movflags",
            "+faststart",
            str(output_temporary_path),
        ]
        _run_ffmpeg_with_progress(
            final_fit_command,
            audio_duration,
            "Final video and audio render",
            70,
            98,
            cancel_event=cancel_event,
            progress_callback=progress_callback,
        )
        if (
            not output_temporary_path.is_file()
            or output_temporary_path.stat().st_size == 0
        ):
            raise RuntimeError("FFmpeg did not create the synchronized video")
        if cancel_event is not None and cancel_event.is_set():
            raise ProcessingCancelled("Video processing was canceled.")
        output_temporary_path.replace(output_path)
    finally:
        for temporary_path in (intermediate_path, output_temporary_path):
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
    return output_path


def process_video(
    video_path,
    audio_path,
    transcript_path,
    output_path,
    gemini_cache_dir=None,
):
    _report_video_edit_step(1, 7, "Validating input files and transcript")
    _report_video_edit_progress(0, "Validating input files and transcript")
    video_path = Path(video_path)
    audio_path = Path(audio_path)
    transcript_path = Path(transcript_path)
    for path, description in (
        (video_path, "source video"),
        (audio_path, "narration audio"),
        (transcript_path, "timestamped source transcript"),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"{description} not found: {path}")

    source_lines = parse_timestamped_transcript(
        transcript_path.read_text(encoding="utf-8-sig")
    )
    _report_video_edit_step(2, 7, "Checking source video and narration durations")
    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    video_duration = probe_duration(ffmpeg_path, video_path)
    audio_duration = probe_duration(ffmpeg_path, audio_path)
    source_lines = normalize_source_transcript_end(
        source_lines,
        video_duration,
    )
    _report_video_edit_progress(8, "Source video and narration durations checked")
    _report_video_edit_step(3, 7, "Transcribing narration with Gemini")
    _report_video_edit_progress(12, "Transcribing narration with Gemini")
    gemini_response, cache_path = _load_or_transcribe_audio(
        audio_path,
        gemini_cache_dir,
        audio_duration,
    )
    try:
        audio_lines = parse_gemini_audio_transcript(gemini_response)
    except ValueError:
        if cache_path is not None:
            cache_path.unlink(missing_ok=True)
        raise
    audio_lines = normalize_audio_transcript_end(
        audio_lines,
        audio_duration,
    )
    _report_video_edit_progress(25, "Narration transcript ready")
    _report_video_edit_step(
        4,
        7,
        "Matching transcripts and recommending video speeds with ChatGPT",
    )
    _report_video_edit_progress(
        28,
        "Matching transcripts and recommending video speeds with ChatGPT",
    )
    segments = recommend_video_speeds(
        source_lines,
        audio_lines,
        video_duration,
        audio_duration,
    )
    scene_plan = build_scene_plan_from_speeds(segments)
    print(
        "Video segment playback speeds:\n"
        f"{json.dumps(build_speed_report(scene_plan), ensure_ascii=False, indent=2)}"
    )
    _report_video_edit_progress(38, "Video speed plan ready")
    result_path = render_video(
        video_path,
        audio_path,
        output_path,
        scene_plan,
        ffmpeg_path,
        audio_duration,
    )
    if cache_path is not None:
        cache_path.unlink(missing_ok=True)
    print(f"Saved synchronized video: {result_path}")
    _report_video_edit_step(7, 7, "Synchronized video complete")
    _report_video_edit_progress(100, "Synchronized video complete")
    return result_path


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Synchronize a source video to semantically matched narration."
    )
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--transcript", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--gemini-cache-dir", type=Path)
    args = parser.parse_args(argv)
    process_video(
        args.video,
        args.audio,
        args.transcript,
        args.output,
        gemini_cache_dir=args.gemini_cache_dir,
    )


if __name__ == "__main__":
    main()
