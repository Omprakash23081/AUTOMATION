from __future__ import annotations

import argparse
import json
from pathlib import Path


def run_whisper(source_directory: Path, output_directory: Path) -> None:
    from faster_whisper import WhisperModel

    audio_files = sorted(
        path for path in source_directory.iterdir()
        if path.is_file() and path.suffix.lower() in {".m4a", ".mp3", ".wav"}
    )
    if not audio_files:
        raise RuntimeError(f"No audio files were found in {source_directory}")

    output_directory.mkdir(parents=True, exist_ok=True)
    model = WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=4)
    for index, audio_path in enumerate(audio_files, start=1):
        output_path = output_directory / f"{audio_path.stem}.txt"
        if output_path.is_file() and output_path.stat().st_size:
            print(f"Skipping existing transcript: {output_path}", flush=True)
            continue
        print(f"Transcribing {index}/{len(audio_files)}: {audio_path.name}", flush=True)
        segments, info = model.transcribe(str(audio_path), beam_size=5)
        temporary_path = output_path.with_suffix(".txt.tmp")
        try:
            with temporary_path.open("w", encoding="utf-8", newline="\n") as stream:
                for segment in segments:
                    text = segment.text.strip()
                    if text:
                        stream.write(text + "\n")
            temporary_path.replace(output_path)
        finally:
            temporary_path.unlink(missing_ok=True)
        print(f"Detected language: {info.language}; saved {output_path}", flush=True)


def translate_english(source_file: Path, output_file: Path) -> None:
    from youtube_automation.chatgpt.english import translate_file

    translate_file(source_file, output_file)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    workflow = config.get("workflow")
    if workflow == "whisper":
        run_whisper(Path(config["sourceDirectory"]), Path(config["outputDirectory"]))
    elif workflow == "english_translation":
        translate_english(Path(config["sourceFile"]), Path(config["outputFile"]))
    else:
        raise ValueError("Unsupported automation runner workflow.")


if __name__ == "__main__":
    main()
