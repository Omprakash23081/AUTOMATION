# YouTube Video Automation

This project downloads videos and audio, processes them through Gemini and ChatGPT, and produces translated transcripts for TTS workflows.

## Structure

```text
youtube_automation/
   cartesia/tts.py     Cartesia audio generation
   chatgpt/browser.py  ChatGPT page interaction
   chatgpt/transcript.py
                                 Prompting and translation text processing
   chatgpt/transcribe.py
                                 Translation workflow and job state
   gemini/browser.py   Gemini upload and response interaction
   gemini/transcript.py
                                 Transcript response cleanup
   gemini/transcribe.py
                                 Transcription workflow and job state
   youtube_download/   Authorized video download and MP4 packaging
   whisper/transcribe.py
                                 Local Whisper transcription
   config.py           Application settings
   downloader.py       Media downloads
   pipeline.py         Workflow orchestration
   job_state.py        Locks, metrics, and job state
tests/                Behavior tests
links.txt             Input URLs
```

## Running

1. Create and activate a virtual environment.
2. Install dependencies:
   `pip install -r requirements.txt`
3. Set your environment variables in `.env`.
4. Start the UI with `streamlit run app.py`.
5. Or run the CLI pipeline with `python -m youtube_automation.pipeline`.

### Web UI

The Streamlit UI supports Kuaishou and YouTube batch processing plus the
VIDEO EDIT AUTOMATION workflow for synchronizing source video to narration in
any language.

- Open the app with `streamlit run app.py`
- Choose a platform and its processing options.
- Set the output folder in **Storage location**. The app creates it when needed.
  Kuaishou creates `videos`, `audio`, and `transcripts` subfolders; YouTube
  creates `videos`, `audio`, and `transcripts` subfolders.
- Set the maximum run time. If a job exceeds this limit, the app terminates the
  pipeline process and reports the timeout and any output captured so far.
- Start the job to see live console output and YouTube download progress in the
  browser. The final output remains visible when the job completes.
- Prepare `.env` and the matching input file (`links.txt` for Kuaishou or
  `ytlink.txt` for YouTube) before running.

Run Cartesia TTS with `python -m youtube_automation.cartesia.tts <transcript.txt>`.

### VIDEO EDIT AUTOMATION

Select **VIDEO EDIT AUTOMATION** in the UI and upload:

- The source video (MP4, MOV, MKV, or WebM).
- Its dubbed narration audio (MP3, WAV, M4A, AAC, FLAC, or OGG).
- A timestamped transcript of the source video's spoken content, in the video's
  language, as a `.txt`
  file with one short phrase per line, for example
  `[00:00:02] spoken words`.

Gemini transcribes the narration audio with timestamps. ChatGPT then compares
the meaning and timing of both transcripts, including when they are in different
languages, and returns JSON playback-speed recommendations for timestamp ranges
across the source video. The program parses and validates the ranges, then
FFmpeg first applies those speeds to the source video without its original
audio. It probes the retimed video's actual duration and calculates the final
playback factor as `edited video duration / narration duration` (for example,
`45 / 49`). A second FFmpeg pass applies that correction, adds the narration,
and trims the final output to the narration duration. If ChatGPT omits a final
video tail, it is kept at normal speed before the global correction. The MP4 is
saved to the selected output folder's `videos` subfolder. Gemini must be logged
in through the configured persistent browser profile, and ChatGPT must be
signed in through its configured persistent browser profile.
If Gemini timestamps the final phrase or phrases at or beyond the measured
audio duration, those phrases are merged with the final in-duration phrase so
their text is preserved and the rendered timeline remains within the audio.
The workflow reports an error if Gemini provides no timestamp inside the audio.
If the source-video transcript's final cue or cues are timestamped at or beyond
the measured video duration, their text is merged into the last in-duration cue
so the transcript remains usable without discarding words.
Cutroom keeps Gemini's transcript temporarily, keyed to the narration audio, so
a retry after a later alignment or rendering failure can reuse it. Cached
transcripts are parsed and checked for timestamps, audio-duration bounds, and
obvious Gemini commentary before reuse; invalid cache files are discarded and
Gemini is run again. The VIDEO EDIT UI cache is stored under
`H:\YOUTUBE_CLIP_AUTOMATION\data\automation\video_edit\gemini_cache`.
Delete the `.txt` file in that folder to force Gemini to transcribe that audio
again. The cache is removed after a successful synchronized-video export.
During VIDEO EDIT, the UI shows the current processing step and an overall
progress bar. Both FFmpeg encoding passes report live progress; detailed console
logs remain available in a collapsed expander after the run.

Add one authorized YouTube URL per line to `ytlink.txt`. Blank lines and lines
starting with `#` are ignored. Then run the batch downloader; videos
are saved under the selected output folder's `videos` subfolder, and transcripts
under its `transcripts` subfolder. The selected folder's final name is used as
the filename prefix. For example, selecting
`H:\YOU TUBE\EXPLAIN AGAIN\Reborn to End It All` creates
`videos\Reborn to End It All001.mp4` and
`transcripts\Reborn to End It All001.txt`, followed by `002`, `003`, and so on.
The original video resolution and aspect ratio are preserved; videos are
packaged as MP4 without resizing or video re-encoding:
Transcripts are fetched from YouTube using the `youtube-transcript-api` Python
package; no YouTube API key or local audio transcription is needed for this downloader.
Before downloading, the batch asks whether you want English or Hindi
transcripts. English is the default when running with
`--transcript-language en`; use `--transcript-language hi` for Hindi or omit
the option to choose interactively. It fetches captions in the selected
language. If English is selected but only Hindi captions are available, Hindi
is saved in the normal `.txt` file. Every downloaded or existing English or
Hindi transcript is then sent to ChatGPT: Hindi is translated to English,
English is polished for speech, timestamps are removed, and references to the
original channel name are changed to `Fact Monk`. The processed voice-over
script is saved as a sibling `*_english.txt` file. Once that English script is
saved, the source transcript remains available until Cartesia finishes. If
Cartesia audio is generated, the source `.txt` is replaced with a fresh
timestamped YouTube transcript; if TTS is skipped for a video over three
minutes, the Hindi source transcript is deleted as before.
ChatGPT is instructed to return a JSON object with a `script` string field. The
pipeline parses that JSON and saves only the field's spoken script, ignoring any
text outside the JSON object.
ChatGPT must be logged in through the configured
persistent browser profile. YouTube can rate-limit caption requests, in which
case the downloader reports the affected URL.
The YouTube downloader uses yt-dlp's default extras, including the JavaScript
challenge solver required for full YouTube download support. If upgrading an
existing environment, run `pip install -r requirements.txt`.
The initial transcript used for translation contains spoken text without
timestamps. After Cartesia audio is generated, the batch fetches YouTube
captions again and replaces the matching `.txt` file with timestamped lines in
`[HH:MM:SS] spoken text` format for the VIDEO EDIT AUTOMATION workflow. Cues
within the same second are combined so timestamps remain strictly increasing.
Videos longer than three minutes do not generate audio and therefore keep the
existing non-timestamped transcript behavior. English captions are preferred
by default; if they are unavailable, Hindi captions are used when available.
Transcript text is cleaned of markup, WebVTT metadata, and consecutive
duplicate or overlapping text.

After ChatGPT saves the processed English voice-over script, the batch generates
speech from that script with Cartesia and saves the WAV file in the output
folder's `audio` subfolder only when the source video is three minutes or shorter.
Videos longer than three minutes still have their captions and English script
processed, but Cartesia TTS is skipped. Set `CARTESIA_API_KEY` in `.env`. The defaults are
model `sonic-3.6`, voice `cb9c954d-bcaa-43ed-82bf-aeb5e88a3cb5` (Kabir),
speed `1`, and volume `1.2`. In the Cutroom YouTube workflow, choose Kabir,
Rohan, Denil, or Jacqueline (Female), or choose **Other** and enter a Cartesia
voice ID. Leaving the character selector on its default uses the configured
default voice. This applies to both English-caption and Hindi-caption batches.

```powershell
python -m youtube_automation.youtube_download.downloader --authorized
```

Use `--links PATH` or `--output-dir PATH` to select a different list or output folder.

Run local Whisper transcription with `python -m youtube_automation.whisper.transcribe`.

Run the behavior tests with `python -m unittest discover -s tests -v`. They cover
pipeline modes, video-only downloads, transfer resuming, and Gemini chat setup.
