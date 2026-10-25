import json
import os
import re
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

from . import browser
from .transcript import split_transcript


CHATGPT_UI_ARTIFACT_LINES = {
    "...",
    "…",
    "show more",
    "latest response",
    "think",
    "copy",
    "read aloud",
    "chatgpt can make mistakes. check important info.",
    "chatgpt is ai and can make mistakes.",
}


def build_english_prompt(chunk, index, total):
    return (
        "Translate the Hindi transcript below into accurate, natural English. "
        "If the transcript is already in English, keep it in English and polish it "
        "without changing its meaning. Preserve every event, detail, name, relationship, "
        "and meaning. Do not summarize, add jokes, embellish, or omit anything. "
        "Remove every timestamp written in [hh:mm:ss] format; never include any "
        "timestamps in the result. Replace every reference to the video's original "
        "channel name with exactly Fact Monk. Use clear, simple spoken phrasing and "
        "natural punctuation suitable for text-to-speech. "
        "Return exactly one valid JSON object with this schema: "
        '{"script":"the complete English script"}. '
        "The script value must contain only words intended to be spoken aloud. "
        "Do not put the JSON inside Markdown fences, and do not add any text, "
        "labels, explanations, or ChatGPT interface content before or after it.\n\n"
        f"This is chunk {index} of {total}. Translate only this chunk.\n\n"
        f"HINDI TRANSCRIPT:\n{chunk}"
    )


def output_path_for(transcript_path):
    transcript_path = Path(transcript_path)
    return transcript_path.with_name(f"{transcript_path.stem}_english.txt")


def _extract_script(response):
    decoder = json.JSONDecoder()
    for index, character in enumerate(response):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(response, index)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(value, dict)
            and set(value) == {"script"}
            and isinstance(value["script"], str)
        ):
            return value["script"]
    raise ValueError("ChatGPT response did not contain a JSON object with a string script field")


def _clean_script(response):
    text = response.strip()
    text = re.sub(r"^```(?:text)?\s*\n|\n```$", "", text, flags=re.IGNORECASE)
    header = (
        r"(?:chatgpt\s+said|(?:(?:this is|here is|here's) )?"
        r"(?:the )?(?:updated )?(?:english )?transcript)"
    )
    text = re.sub(
        rf"(?im)^\s*{header}\s*:?\s*$\n?",
        "",
        text,
    )
    text = re.sub(rf"(?i)^\s*{header}\s*:\s*", "", text)
    text = re.sub(r"\[\d{2}:\d{2}:\d{2}\]", "", text)
    text = "\n".join(
        line.strip()
        for line in text.splitlines()
        if re.sub(r"\s+", " ", line).strip().casefold()
        not in CHATGPT_UI_ARTIFACT_LINES
    )
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _is_usable_translation(translation):
    text = translation.strip()
    if len(text) < 20:
        return False
    if any("\u0900" <= character <= "\u097f" for character in text):
        return False
    if re.search(r"\[\d{2}:\d{2}:\d{2}\]", text):
        return False
    if re.search(r"(?im)^\s*chatgpt\s+said\s*:", text):
        return False
    return True


def translate_transcript(page, transcript_path, output_path):
    source = Path(transcript_path).read_text(encoding="utf-8-sig").strip()
    if not source:
        raise ValueError(f"Transcript is empty: {transcript_path}")

    browser.start_new_chat(page, "YouTube-English")
    chunks = split_transcript(source)
    translated_chunks = []
    for index, chunk in enumerate(chunks, start=1):
        prompt = build_english_prompt(chunk, index, len(chunks))
        prompt_instructions = prompt.partition("HINDI TRANSCRIPT:")[0]
        prompt_instructions = " ".join(prompt_instructions.split())
        browser.log(
            f"Sending transcript chunk {index}/{len(chunks)}. "
            f"Prompt instructions: {prompt_instructions}",
            "YouTube-English",
        )
        translated = browser.send_prompt_for_translation(
            page,
            prompt,
            "YouTube-English",
        )
        translated = _clean_script(_extract_script(translated))
        if not _is_usable_translation(translated):
            raise ValueError(
                f"ChatGPT returned an empty or unusable English TTS script for "
                f"{Path(transcript_path).name}, chunk {index} of {len(chunks)}"
            )
        translated_chunks.append(translated)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=output_path.parent,
            prefix=f".{output_path.stem}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write("\n\n".join(translated_chunks) + "\n")
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    browser.log(f"Saved processed English TTS script: {output_path}", "YouTube-English")
    return output_path


def translate_file(transcript_path, output_path=None):
    transcript_path = Path(transcript_path)
    output_path = Path(output_path) if output_path else output_path_for(transcript_path)
    browser.log(
        f"Using translation code {Path(__file__).resolve()} "
        f"with Python {Path(sys.executable).resolve()}",
        "YouTube-English",
    )
    with sync_playwright() as playwright:
        context = browser.launch_persistent_context(playwright)
        try:
            page = context.new_page()
            return translate_transcript(page, transcript_path, output_path)
        finally:
            context.close()
