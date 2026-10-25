import queue
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

import streamlit as st

from youtube_automation import config
from youtube_automation.youtube_download import downloader as youtube_downloader


PROJECT_ROOT = Path(__file__).resolve().parent
VIDEO_EDIT_GEMINI_CACHE_DIR = (
    PROJECT_ROOT.parent / "data" / "automation" / "video_edit" / "gemini_cache"
)
st.set_page_config(page_title="Video Automation Suite", page_icon="🎬", layout="wide")


def prepare_output_directory(location, platform):
    if not location.strip():
        raise ValueError("Choose an output folder before starting the pipeline.")

    output_directory = Path(location).expanduser()
    if not output_directory.is_absolute():
        output_directory = PROJECT_ROOT / output_directory
    output_directory = output_directory.resolve()

    subdirectories = (
        ("videos", "audio", "transcripts")
        if platform == "Kuaishou"
        else ("videos", "audio", "transcripts")
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    for name in subdirectories:
        (output_directory / name).mkdir(parents=True, exist_ok=True)
    return output_directory


def run_command(
    platform,
    command,
    output_directory,
    timeout_minutes,
    environment,
    progress_callback=None,
):
    environment = environment.copy()
    environment["PYTHONUNBUFFERED"] = "1"

    try:
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as error:
        return {
            "platform": platform,
            "status": "failed",
            "error": f"Could not start the pipeline: {error}",
            "output": "",
            "output_directory": str(output_directory),
        }

    output_lines = []
    visible_lines = deque(maxlen=80)
    output_queue = queue.Queue()

    def record_line(line):
        text = line.rstrip()
        is_pipeline_progress = text.startswith(
            ("YouTube download: ", "MP4 packaging: ", "MP4 packaging in progress")
        )
        if is_pipeline_progress:
            if visible_lines and visible_lines[-1].startswith(
                ("YouTube download: ", "MP4 packaging: ", "MP4 packaging in progress")
            ):
                visible_lines[-1] = text
            else:
                visible_lines.append(text)
            if output_lines and output_lines[-1].startswith(
                ("YouTube download: ", "MP4 packaging: ", "MP4 packaging in progress")
            ):
                output_lines[-1] = line
            else:
                output_lines.append(line)
            return

        visible_lines.append(text)
        output_lines.append(line)

    def read_output():
        for line in process.stdout:
            output_queue.put(line)
        output_queue.put(None)

    reader = threading.Thread(target=read_output, name="pipeline-output-reader", daemon=True)
    reader.start()
    if progress_callback:
        progress_callback("Pipeline started. Waiting for output...")

    deadline = time.monotonic() + timeout_minutes * 60
    timed_out = False
    reader_finished = False
    while not reader_finished:
        if process.poll() is None and time.monotonic() >= deadline:
            timed_out = True
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

        try:
            line = output_queue.get(timeout=0.1)
        except queue.Empty:
            if process.poll() is not None and not reader.is_alive():
                break
            continue

        if line is None:
            reader_finished = True
            continue

        record_line(line)
        if progress_callback:
            progress_callback("\n".join(visible_lines))

    process.wait()
    reader.join()
    output = "".join(output_lines).strip()
    if timed_out:
        status = "failed"
        error = f"Pipeline exceeded the {timeout_minutes}-minute time limit."
    elif process.returncode:
        status = "failed"
        error = f"Pipeline exited with code {process.returncode}."
    else:
        status = "success"
        error = None

    return {
        "platform": platform,
        "status": status,
        "error": error,
        "output": output,
        "output_directory": str(output_directory),
    }


def _parse_video_edit_progress(output):
    steps = {}
    progress = 0
    detail = "Waiting for video-edit progress..."
    for line in output.splitlines():
        step_match = re.match(
            r"VIDEO EDIT STEP: (\d+)/(\d+)\|(.+)",
            line.strip(),
        )
        if step_match:
            step_number, total_steps, description = step_match.groups()
            steps[int(step_number)] = (int(total_steps), description)
            continue

        progress_match = re.match(
            r"VIDEO EDIT PROGRESS: (\d+)\|(.+)",
            line.strip(),
        )
        if progress_match:
            progress = max(0, min(100, int(progress_match.group(1))))
            detail = progress_match.group(2)
    return {
        "steps": steps,
        "progress": progress,
        "detail": detail,
    }


def run_kuaishou_pipeline(mode, location, timeout_minutes, progress_callback=None):
    try:
        output_directory = prepare_output_directory(location, "Kuaishou")
        environment = os.environ.copy()
        video_directory = output_directory / "videos"
        audio_directory = output_directory / "audio"
        transcript_directory = output_directory / "transcripts"
        environment.update(
            {
                "VIDEO_FOLDER": str(video_directory),
                "AUDIO_FOLDER": str(audio_directory),
                "TRANSCRIPT_FOLDER": str(transcript_directory),
                "MANIFEST_PATH": str(transcript_directory / "manifest.json"),
                "FAILED_JOBS_PATH": str(transcript_directory / "failed_parts.txt"),
                "METRICS_PATH": str(transcript_directory / "metrics.jsonl"),
                "LOCK_PATH": str(transcript_directory / "pipeline.lock"),
            }
        )
    except (OSError, ValueError) as error:
        st.session_state.pipeline_result = {
            "platform": "Kuaishou",
            "status": "failed",
            "error": str(error),
            "output": "",
            "output_directory": str(location),
        }
        return

    st.session_state.pipeline_result = run_command(
        "Kuaishou",
        [
            sys.executable,
            "-m",
            "youtube_automation.pipeline",
            "--mode",
            str(mode),
        ],
        output_directory,
        timeout_minutes,
        environment,
        progress_callback,
    )


def run_youtube_pipeline(
    transcript_language,
    location,
    timeout_minutes,
    progress_callback=None,
):
    try:
        output_directory = prepare_output_directory(location, "YouTube")
    except (OSError, ValueError) as error:
        st.session_state.pipeline_result = {
            "platform": "YouTube",
            "status": "failed",
            "error": str(error),
            "output": "",
            "output_directory": str(location),
        }
        return

    links_file = PROJECT_ROOT / "ytlink.txt"
    st.session_state.pipeline_result = run_command(
        "YouTube",
        [
            sys.executable,
            "-m",
            "youtube_automation.youtube_download.downloader",
            "--authorized",
            "--links",
            str(links_file),
            "--output-dir",
            str(output_directory),
            "--transcript-language",
            transcript_language,
        ],
        output_directory,
        timeout_minutes,
        os.environ.copy(),
        progress_callback,
    )


def run_video_edit_pipeline(
    video_upload,
    audio_upload,
    transcript_upload,
    location,
    timeout_minutes,
    progress_callback=None,
):
    output_directory = Path(location).expanduser()
    try:
        output_directory = prepare_output_directory(location, "VIDEO EDIT AUTOMATION")
        if not video_upload or not audio_upload or not transcript_upload:
            raise ValueError(
                "Upload the source video, narration audio, and a timestamped "
                "transcript of the source video's spoken content."
            )

        extensions = (
            Path(video_upload.name).suffix.lower(),
            Path(audio_upload.name).suffix.lower(),
            Path(transcript_upload.name).suffix.lower(),
        )
        if extensions[0] not in {".mp4", ".mov", ".mkv", ".webm"}:
            raise ValueError("Source video must be MP4, MOV, MKV, or WebM.")
        if extensions[1] not in {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}:
            raise ValueError("Narration audio must be MP3, WAV, M4A, AAC, FLAC, or OGG.")
        if extensions[2] != ".txt":
            raise ValueError("The timestamped source transcript must be a .txt file.")

        video_stem = Path(video_upload.name).stem
        safe_video_stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", video_stem).strip(" ._")
        output_path = (
            output_directory
            / "videos"
            / f"{safe_video_stem or 'video'}_synchronized.mp4"
        )
        with tempfile.TemporaryDirectory(prefix="video_edit_inputs_") as temporary_dir:
            input_directory = Path(temporary_dir)
            uploads = (
                (video_upload, input_directory / f"source_video{extensions[0]}"),
                (audio_upload, input_directory / f"narration_audio{extensions[1]}"),
                (transcript_upload, input_directory / "source_transcript.txt"),
            )
            for upload, path in uploads:
                upload.seek(0)
                with path.open("wb") as target:
                    shutil.copyfileobj(upload, target)

            st.session_state.pipeline_result = run_command(
                "VIDEO EDIT AUTOMATION",
                [
                    sys.executable,
                    "-m",
                    "youtube_automation.video_edit",
                    "--video",
                    str(uploads[0][1]),
                    "--audio",
                    str(uploads[1][1]),
                    "--transcript",
                    str(uploads[2][1]),
                    "--output",
                    str(output_path),
                    "--gemini-cache-dir",
                    str(VIDEO_EDIT_GEMINI_CACHE_DIR),
                ],
                output_directory,
                timeout_minutes,
                os.environ.copy(),
                progress_callback,
            )
    except (OSError, ValueError) as error:
        st.session_state.pipeline_result = {
            "platform": "VIDEO EDIT AUTOMATION",
            "status": "failed",
            "error": str(error),
            "output": "",
            "output_directory": str(output_directory),
        }


st.title("Video Automation Suite")

col1, col2 = st.columns([1, 2])
with col2:
    st.subheader("Output")
    live_output = st.empty()

with st.sidebar:
    st.header("Pipeline controls")
    platform = st.selectbox(
        "Pipeline",
        ["Kuaishou", "YouTube", "VIDEO EDIT AUTOMATION"],
        index=0,
    )

    if platform == "Kuaishou":
        mode = st.selectbox(
            "Processing mode",
            [
                (1, "Download video only"),
                (2, "Download video and audio"),
                (3, "Download video + audio + Gemini"),
                (4, "Run all stages"),
            ],
            format_func=lambda item: item[1],
        )
        button_label = "Start Kuaishou pipeline"
        default_output_directory = Path(config.VIDEO_FOLDER).parent
    elif platform == "YouTube":
        transcript_language = st.selectbox(
            "Transcript language",
            [("en", "English"), ("hi", "Hindi")],
            format_func=lambda item: item[1],
        )
        mode = None
        button_label = "Start YouTube batch"
        default_output_directory = youtube_downloader.DEFAULT_OUTPUT_DIRECTORY
    else:
        video_upload = st.file_uploader(
            "Source video",
            type=["mp4", "mov", "mkv", "webm"],
            key="video_edit_source_video",
        )
        audio_upload = st.file_uploader(
            "Dubbed narration audio",
            type=["mp3", "wav", "m4a", "aac", "flac", "ogg"],
            key="video_edit_hindi_audio",
        )
        transcript_upload = st.file_uploader(
            "Timestamped source-video transcript (.txt)",
            type=["txt"],
            key="video_edit_hindi_transcript",
            help=(
                "Use one line per phrase in the source video's language, "
                "formatted like [00:00:02] spoken words. ChatGPT will match "
                "its meaning to the narration transcript and recommend "
                "source-video playback speeds."
            ),
        )
        mode = None
        button_label = "Start VIDEO EDIT AUTOMATION"
        default_output_directory = PROJECT_ROOT / "outputs" / "video_edit"

    st.subheader("Storage location")
    output_location = st.text_input(
        "Folder for generated files",
        value=str(default_output_directory),
        key=f"output_location_{platform.lower()}",
        help=(
            "Batch workflows create videos, audio, and transcripts subfolders. "
            "VIDEO EDIT AUTOMATION saves the synchronized MP4 in the videos subfolder."
        ),
    )
    timeout_minutes = st.number_input(
        "Maximum run time (minutes)",
        min_value=1,
        max_value=1440,
        value=60,
        step=5,
        help="The app stops waiting and terminates the pipeline process when this limit is reached.",
    )
    if st.button(button_label, use_container_width=True, type="primary"):
        if platform == "VIDEO EDIT AUTOMATION":
            with st.status("Starting video editing...", expanded=True) as status_panel:
                progress_bar = st.progress(0, text="Preparing video edit...")
                step_display = st.empty()
                progress_state = {"steps": {}}

                def show_video_edit_progress(output):
                    snapshot = _parse_video_edit_progress(output)
                    progress_state["steps"].update(snapshot["steps"])
                    ordered_steps = sorted(progress_state["steps"].items())
                    if ordered_steps:
                        current_step = ordered_steps[-1][0]
                        total_steps, current_description = ordered_steps[-1][1]
                        status_panel.update(
                            label=(
                                f"Step {current_step}/{total_steps}: "
                                f"{current_description}"
                            ),
                            state="running",
                        )
                        step_lines = []
                        for step_number, (_, description) in ordered_steps:
                            marker = "✓" if step_number < current_step else "●"
                            step_lines.append(f"{marker} {description}")
                        step_display.markdown("\n\n".join(step_lines))
                    progress_bar.progress(
                        snapshot["progress"],
                        text=snapshot["detail"],
                    )

                run_video_edit_pipeline(
                    video_upload,
                    audio_upload,
                    transcript_upload,
                    output_location,
                    timeout_minutes,
                    progress_callback=show_video_edit_progress,
                )
                result = st.session_state.pipeline_result
                if result["status"] == "success":
                    progress_bar.progress(100, text="Video edit complete")
                    status_panel.update(
                        label="Video edit complete",
                        state="complete",
                    )
                else:
                    status_panel.update(
                        label=f"Video edit failed: {result['error']}",
                        state="error",
                    )
        else:
            with st.spinner(f"Running {platform} pipeline..."):
                if platform == "Kuaishou":
                    run_kuaishou_pipeline(
                        mode[0],
                        output_location,
                        timeout_minutes,
                        progress_callback=lambda output: live_output.code(
                            output,
                            language="text",
                        ),
                    )
                else:
                    run_youtube_pipeline(
                        transcript_language[0],
                        output_location,
                        timeout_minutes,
                        progress_callback=lambda output: live_output.code(
                            output,
                            language="text",
                        ),
                    )

    if st.button("Clear results", use_container_width=True):
        st.session_state.pop("pipeline_result", None)

result = st.session_state.get("pipeline_result")

with col1:
    st.subheader("Pipeline info")
    st.write("Selected platform:", platform)
    if platform == "Kuaishou":
        st.write("Selected mode:", mode[1])
    elif platform == "YouTube":
        st.write("Selected transcript language:", transcript_language[1])
    else:
        st.write("Selected workflow: Chinese video + narration audio sync")

    if result:
        st.write("Output folder:", result["output_directory"])
        if result["status"] == "success":
            st.success(f"{result['platform']} pipeline completed successfully.")
        else:
            st.error(f"{result['platform']} pipeline failed: {result['error']}")
    else:
        st.info("No pipeline run yet.")

with col2:
    if result:
        output = result["output"].strip() or "Pipeline finished without console output."
        if result["platform"] == "VIDEO EDIT AUTOMATION":
            snapshot = _parse_video_edit_progress(output)
            with st.expander("Video edit steps", expanded=True):
                for step_number, (_, description) in sorted(snapshot["steps"].items()):
                    st.write(f"✓ {description}")
                if snapshot["steps"]:
                    st.progress(100 if result["status"] == "success" else snapshot["progress"])
            with st.expander("Detailed logs"):
                st.code(output, language="text")
        else:
            st.code(output, language="text")
    else:
        st.code("No output yet. Select a pipeline and click Start.", language="text")

st.caption(
    "Workflows: Kuaishou batch, YouTube batch, and VIDEO EDIT AUTOMATION "
    "for synchronizing source video to narration audio."
)
