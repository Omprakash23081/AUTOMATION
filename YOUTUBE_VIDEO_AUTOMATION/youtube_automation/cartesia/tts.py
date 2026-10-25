"""Generate a WAV file from a transcript with Cartesia TTS."""

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

from cartesia import Cartesia
from dotenv import load_dotenv


DEFAULT_VOICE_ID = "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"
DEFAULT_ENGLISH_VOICE_ID = "cb9c954d-bcaa-43ed-82bf-aeb5e88a3cb5"
DEFAULT_MODEL_ID = "sonic-3.6"
DEFAULT_SAMPLE_RATE = 44100

load_dotenv()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Convert a transcript into a Cartesia WAV file."
    )
    parser.add_argument("transcript", type=Path, help="Path to the transcript .txt file")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output WAV path (defaults to the transcript name with a .wav suffix)",
    )
    parser.add_argument(
        "--voice",
        default=os.getenv("CARTESIA_VOICE_ID", DEFAULT_VOICE_ID),
        help="Cartesia voice ID, or set CARTESIA_VOICE_ID",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("CARTESIA_MODEL_ID", DEFAULT_MODEL_ID),
        help="Cartesia model ID, or set CARTESIA_MODEL_ID",
    )
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--volume", type=float, default=1.0)
    parser.add_argument(
        "--play",
        action="store_true",
        help="Play the generated WAV after saving it on Windows",
    )
    return parser.parse_args()


def generate_audio(transcript_path, output_path, api_key, voice_id, model_id, speed, volume):
    transcript = transcript_path.read_text(encoding="utf-8").strip()
    if not transcript:
        raise ValueError(f"Transcript is empty: {transcript_path}")

    client = Cartesia(api_key=api_key)
    response = client.tts.generate(
        model_id=model_id,
        transcript=transcript,
        voice=voice_id,
        output_format={
            "container": "wav",
            "encoding": "pcm_s16le",
            "sample_rate": DEFAULT_SAMPLE_RATE,
        },
        generation_config={"speed": speed, "volume": volume},
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=output_path.parent,
            prefix=f"{output_path.stem}.",
            suffix=".wav",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
        response.write_to_file(str(temporary_path))
        temporary_path.replace(output_path)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def generate_english_audio(transcript_path, output_path):
    transcript_path = Path(transcript_path)
    output_path = Path(output_path)
    voice_id = os.getenv("CARTESIA_ENGLISH_VOICE_ID", DEFAULT_ENGLISH_VOICE_ID)
    model_id = os.getenv("CARTESIA_MODEL_ID", DEFAULT_MODEL_ID)
    transcript = transcript_path.read_text(encoding="utf-8").strip()
    if not transcript:
        raise ValueError(f"Transcript is empty: {transcript_path}")
    cache_metadata = {
        "transcript_sha256": hashlib.sha256(transcript.encode("utf-8")).hexdigest(),
        "voice_id": voice_id,
        "model_id": model_id,
        "speed": 1.1,
        "volume": 1.1,
        "sample_rate": DEFAULT_SAMPLE_RATE,
    }
    metadata_path = output_path.with_name(f".{output_path.name}.metadata.json")
    if output_path.is_file() and output_path.stat().st_size:
        try:
            existing_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            existing_metadata = None
        if existing_metadata == cache_metadata:
            print(f"Cartesia audio already exists: {output_path}")
            return output_path

    api_key = os.getenv("CARTESIA_API_KEY")
    if not api_key:
        raise RuntimeError("Set CARTESIA_API_KEY before generating YouTube speech")

    generate_audio(
        transcript_path,
        output_path,
        api_key,
        voice_id,
        model_id,
        speed=1.0,
        volume=1.2,
    )
    temporary_metadata_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=metadata_path.parent,
            prefix=f".{metadata_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_metadata_path = Path(temporary_file.name)
            json.dump(cache_metadata, temporary_file, sort_keys=True)
            temporary_file.write("\n")
        temporary_metadata_path.replace(metadata_path)
    finally:
        if temporary_metadata_path is not None:
            temporary_metadata_path.unlink(missing_ok=True)
    print(f"Saved Cartesia audio: {output_path}")
    return output_path


def generate_folder(
    transcript_folder,
    output_folder,
    api_key,
    voice_id=None,
    model_id=None,
    speed=1.0,
    volume=1.0,
):
    transcript_folder = Path(transcript_folder)
    output_folder = Path(output_folder)
    transcripts = sorted(transcript_folder.glob("PART-*_hinglish.txt"))
    pending = [
        (transcript, output_folder / f"{transcript.name[:-len('_hinglish.txt')]}.wav")
        for transcript in transcripts
    ]
    pending = [(transcript, output) for transcript, output in pending if not output.is_file()]
    if not pending:
        return 0
    if not api_key:
        raise RuntimeError("Set CARTESIA_API_KEY before generating audio")

    voice_id = voice_id or os.getenv("CARTESIA_VOICE_ID", DEFAULT_VOICE_ID)
    model_id = model_id or os.getenv("CARTESIA_MODEL_ID", DEFAULT_MODEL_ID)
    for transcript, output in pending:
        generate_audio(transcript, output, api_key, voice_id, model_id, speed, volume)
        print(f"Saved audio: {output}")
    return len(pending)


def play_audio(output_path):
    if sys.platform != "win32":
        raise RuntimeError("--play is only supported on Windows")

    import winsound

    winsound.PlaySound(str(output_path), winsound.SND_FILENAME)


def main():
    args = parse_args()
    api_key = os.getenv("CARTESIA_API_KEY")
    if not api_key:
        raise RuntimeError("Set CARTESIA_API_KEY before running this script")
    if not args.transcript.is_file():
        raise FileNotFoundError(f"Transcript not found: {args.transcript}")

    output_path = args.output or args.transcript.with_suffix(".wav")
    generate_audio(
        args.transcript,
        output_path,
        api_key,
        args.voice,
        args.model,
        args.speed,
        args.volume,
    )
    print(f"Saved audio: {output_path}")
    if args.play:
        play_audio(output_path)


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)