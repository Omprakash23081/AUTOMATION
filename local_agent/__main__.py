from __future__ import annotations

import argparse
import sys

from local_agent.client import AgentApi, AgentApiError, pair_agent
from local_agent.credentials import CredentialStore
from local_agent.references import LocalFileReferences
from local_agent.runner import AgentRunner


def _submit_video_job(api: AgentApi, references: LocalFileReferences) -> int:
    try:
        from tkinter import TclError, Tk, filedialog
    except ImportError as exc:
        raise RuntimeError("This Python installation does not include Windows file dialogs.") from exc

    try:
        window = Tk()
        window.withdraw()
        window.attributes("-topmost", True)
        video_path = filedialog.askopenfilename(
            title="Select source video",
            filetypes=[("Video files", "*.mp4 *.mov *.mkv *.webm *.m4v")],
        )
        if not video_path:
            print("Video job canceled; no files were submitted.")
            return 0
        audio_path = filedialog.askopenfilename(
            title="Select narration audio",
            filetypes=[("Audio files", "*.mp3 *.wav *.m4a *.aac *.flac *.ogg")],
        )
        if not audio_path:
            print("Video job canceled; no files were submitted.")
            return 0
        output_directory = filedialog.askdirectory(
            title="Select local output folder",
            mustexist=True,
        )
        if not output_directory:
            print("Video job canceled; no files were submitted.")
            return 0
    except TclError as exc:
        raise RuntimeError(f"Could not open the Windows file picker: {exc}") from exc
    finally:
        if "window" in locals():
            window.destroy()

    payload = references.create_video_job(video_path, audio_path, output_directory)
    try:
        result = api.submit_video_job(payload)
    except Exception:
        references.delete_video_job(payload)
        raise
    job = result.get("job")
    if not isinstance(job, dict) or not isinstance(job.get("id"), str):
        references.delete_video_job(payload)
        raise RuntimeError("The Cutroom API did not confirm the video job.")
    print(f"Video-sync job queued: {job['id']}")
    print("The paired local agent will render the video on this computer.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Cutroom Windows local agent.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    pair_parser = subparsers.add_parser("pair", help="Pair this computer with a Cutroom account.")
    pair_parser.add_argument("--api-url", required=True, help="Cutroom API origin, e.g. https://cutroom7.netlify.app")
    pair_parser.add_argument("--name", default="Windows local agent", help="Name shown in Cutroom account settings.")
    run_parser = subparsers.add_parser("run", help="Poll Cutroom and execute authorized local jobs.")
    run_parser.add_argument("--api-url", required=True, help="Cutroom API origin, e.g. https://cutroom7.netlify.app")
    run_parser.add_argument(
        "--browser",
        choices=("chrome", "edge", "firefox"),
        help="Use this local browser profile for YouTube authentication when needed.",
    )
    video_parser = subparsers.add_parser(
        "process-video",
        help="Select local video/audio files and queue a local video-sync render.",
    )
    video_parser.add_argument("--api-url", required=True, help="Cutroom API origin, e.g. https://cutroom7.netlify.app")
    args = parser.parse_args()
    credentials = CredentialStore()
    try:
        if args.command == "pair":
            api = AgentApi(args.api_url)
            pair_agent(api, args.name, credentials.save)
            print(f"Paired. Credentials are protected by Windows DPAPI at {credentials.path}.")
            return 0
        token = credentials.load()
        if not token:
            parser.error("No paired agent credential found. Run the pair command first.")
        api = AgentApi(args.api_url, token=token)
        if args.command == "process-video":
            return _submit_video_job(api, LocalFileReferences())
        print("Cutroom agent is running. Press Ctrl+C to stop.")
        AgentRunner(api, youtube_browser=getattr(args, "browser", None)).run_forever()
    except (AgentApiError, OSError, RuntimeError, TimeoutError, ValueError) as exc:
        if "credentials were rejected or revoked" in str(exc).lower():
            credentials.delete()
        print(f"Cutroom agent stopped: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCutroom agent stopped.")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
