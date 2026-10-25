from contextlib import redirect_stdout
from io import StringIO
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from playwright.sync_api import TimeoutError
from yt_dlp.utils import DownloadError

from youtube_automation.chatgpt import english
from youtube_automation.cartesia import tts as cartesia_tts
from youtube_automation.youtube_download import downloader
from app import prepare_output_directory, run_command


class YoutubeDownloadTests(unittest.TestCase):
    def setUp(self):
        if self._testMethodName.startswith("test_video_duration_"):
            return
        duration_patch = patch.object(
            downloader,
            "get_video_duration",
            return_value=downloader.MAX_TTS_VIDEO_DURATION_SECONDS,
        )
        self.addCleanup(duration_patch.stop)
        duration_patch.start()

    @staticmethod
    def create_processed_script(transcript_path):
        output_path = english.output_path_for(transcript_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            "This is a processed English voice-over script.",
            encoding="utf-8",
        )
        return output_path

    def test_prepare_youtube_output_directory_creates_media_folders(self):
        with tempfile.TemporaryDirectory() as directory:
            output_directory = prepare_output_directory(
                str(Path(directory) / "Reborn to End It All"),
                "YouTube",
            )

            self.assertTrue((output_directory / "videos").is_dir())
            self.assertTrue((output_directory / "audio").is_dir())
            self.assertTrue((output_directory / "transcripts").is_dir())

    def test_format_selector_requires_1080p_without_lower_resolution_fallback(self):
        selector = downloader.YOUTUBE_FORMAT_SELECTOR

        self.assertIn("[height=1080]", selector)
        self.assertNotIn("[height<=1080]", selector)
        self.assertNotIn("bestvideo+bestaudio", selector)
        self.assertEqual(len(selector.split("/")), 3)

    def test_download_fails_clearly_when_1080p_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            youtube_dl = MagicMock()
            youtube_dl.__enter__.return_value = youtube_dl
            youtube_dl.extract_info.side_effect = DownloadError(
                "ERROR: Requested format is not available"
            )
            with (
                patch.object(downloader.imageio_ffmpeg, "get_ffmpeg_exe", return_value="ffmpeg.exe"),
                patch.object(
                    downloader,
                    "YoutubeDL",
                    return_value=youtube_dl,
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "1080p is not available.*No lower-resolution version",
                ):
                    downloader.download_video(
                        "https://youtu.be/example",
                        Path(directory) / "video.mp4",
                        authorized=True,
                    )

    def test_download_includes_ffmpeg_diagnostics_when_yt_dlp_conversion_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            youtube_dl = MagicMock()
            youtube_dl.__enter__.return_value = youtube_dl

            def fail_during_postprocessing(*args, **kwargs):
                options = youtube_dl_factory.call_args.args[0]
                options["logger"].debug("[debug] ffmpeg command line: ffmpeg ...")
                options["logger"].debug(
                    "[debug] [matroska] Could not write header: Invalid argument\n"
                    "[debug] Conversion failed!"
                )
                raise DownloadError("ERROR: Postprocessing: Conversion failed!")

            youtube_dl.extract_info.side_effect = fail_during_postprocessing
            with (
                patch.object(downloader.imageio_ffmpeg, "get_ffmpeg_exe", return_value="ffmpeg.exe"),
                patch.object(
                    downloader,
                    "YoutubeDL",
                    return_value=youtube_dl,
                ) as youtube_dl_factory,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "(?s)FFmpeg diagnostics:.*Could not write header: Invalid argument",
                ):
                    downloader.download_video(
                        "https://youtu.be/example",
                        Path(directory) / "video.mp4",
                        authorized=True,
                    )

    def test_download_progress_hook_reports_progress_without_duplicate_percentages(self):
        progress_hook = downloader._make_download_progress_hook()
        output = StringIO()

        with redirect_stdout(output):
            progress_hook(
                {
                    "status": "downloading",
                    "downloaded_bytes": 10 * 1024**2,
                    "total_bytes": 100 * 1024**2,
                    "speed": 2 * 1024**2,
                    "eta": 45,
                }
            )
            progress_hook(
                {
                    "status": "downloading",
                    "downloaded_bytes": 10 * 1024**2,
                    "total_bytes": 100 * 1024**2,
                    "speed": 2 * 1024**2,
                    "eta": 45,
                }
            )
            progress_hook({"status": "finished"})

        self.assertEqual(
            output.getvalue().splitlines(),
            [
                "YouTube download: 10% | 10.0 MiB / 100.0 MiB | 2.0 MiB/s | ETA 00:00:45",
            ],
        )
        self.assertIn("ETA 00:00:45\r", output.getvalue())
        self.assertNotIn("Download finished", output.getvalue())

    def test_ffmpeg_packaging_reports_progress_and_copies_audio(self):
        process = Mock()
        process.stdout = StringIO(
            "out_time_us=10000000\n"
            "speed=2.0x\n"
            "progress=continue\n"
            "out_time_us=20000000\n"
            "speed=2.5x\n"
            "progress=end\n"
        )
        process.wait.return_value = 0
        output = StringIO()

        with patch.object(downloader.subprocess, "Popen", return_value=process) as popen:
            with redirect_stdout(output):
                downloader.run_ffmpeg_with_progress(["ffmpeg"], 20)

        self.assertEqual(
            output.getvalue().splitlines(),
            [
                "MP4 packaging: 50% | 00:00:10 / 00:00:20 | 2.0x",
                "MP4 packaging: 100% | 00:00:20 / 00:00:20 | 2.5x",
            ],
        )
        self.assertEqual(popen.call_args.kwargs["stderr"], downloader.subprocess.STDOUT)
        self.assertEqual(process.wait.call_count, 1)

    def test_run_command_replaces_previous_download_progress_line(self):
        updates = []
        result = run_command(
            "YouTube",
            [
                sys.executable,
                "-u",
                "-c",
                "print('Downloading source'); print('YouTube download: 12%'); "
                "print('YouTube download: 13%'); print('Download finished')",
            ],
            tempfile.gettempdir(),
            1,
            os.environ.copy(),
            updates.append,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            result["output"].splitlines(),
            [
                "Downloading source",
                "YouTube download: 13%",
                "Download finished",
            ],
        )
        self.assertTrue(any("YouTube download: 13%" in update for update in updates))
        self.assertFalse(
            "YouTube download: 12%" in updates[-1]
        )

    def test_chatgpt_english_translation_uses_dedicated_prompt_and_output(self):
        page = Mock()
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "video.txt"
            output_path = english.output_path_for(source_path)
            source_path.write_text(
                "[00:00:03] यह एक लंबा हिंदी वाक्य है।",
                encoding="utf-8",
            )

            with (
                patch.object(english.browser, "start_new_chat") as start_new_chat,
                patch.object(
                    english.browser,
                    "send_prompt_for_translation",
                    return_value=(
                        '{"script":"This is a complete English translation of the Hindi '
                        'sentence."}'
                    ),
                ) as send_prompt,
                patch.object(english.browser, "log") as log,
                patch.object(english.os, "replace", wraps=os.replace) as replace,
            ):
                result = english.translate_transcript(page, source_path, output_path)

            self.assertEqual(result, output_path)
            self.assertTrue(source_path.exists())
            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                "This is a complete English translation of the Hindi sentence.\n",
            )
            replace.assert_called_once()
            self.assertTrue(source_path.exists())
            start_new_chat.assert_called_once_with(page, "YouTube-English")
            send_prompt.assert_called_once()
            prompt = send_prompt.call_args.args[1]
            self.assertEqual(send_prompt.call_args.args[2], "YouTube-English")
            self.assertTrue(
                prompt.startswith(
                    "Translate the Hindi transcript below into accurate, natural English."
                )
            )
            self.assertIn(
                "Remove every timestamp written in [hh:mm:ss] format; never include any "
                "timestamps in the result.",
                prompt,
            )
            self.assertIn(
                "Replace every reference to the video's original channel name with exactly "
                "Fact Monk.",
                prompt,
            )
            self.assertIn("text-to-speech", prompt)
            self.assertIn(
                "Return exactly one valid JSON object with this schema:",
                prompt,
            )
            self.assertIn(
                '{"script":"the complete English script"}',
                prompt,
            )
            self.assertIn("This is chunk 1 of 1. Translate only this chunk.", prompt)
            self.assertIn(
                "HINDI TRANSCRIPT:\n[00:00:03] यह एक लंबा हिंदी वाक्य है।",
                prompt,
            )
            self.assertIn("natural punctuation suitable for text-to-speech", prompt)
            self.assertNotIn("_hinglish", output_path.name)
            logged_messages = "\n".join(
                call.args[0] for call in log.call_args_list
            )
            self.assertIn("Prompt instructions:", logged_messages)
            self.assertIn("exactly Fact Monk", logged_messages)
            self.assertNotIn("यह एक लंबा हिंदी वाक्य है", logged_messages)

    def test_chatgpt_english_translation_submits_chunks_sequentially(self):
        page = Mock()
        events = []
        first_translation = '{"script":"This is the complete translation for the first chunk."}'
        second_translation = '{"script":"This is the complete translation for the second chunk."}'

        def send_chunk(page_arg, prompt, part_number):
            self.assertIs(page_arg, page)
            self.assertEqual(part_number, "YouTube-English")
            if len(events) == 0:
                events.append("first response completed")
                return first_translation
            self.assertEqual(events, ["first response completed"])
            self.assertNotIn(first_translation, prompt)
            events.append("second response completed")
            return second_translation

        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "video.txt"
            output_path = english.output_path_for(source_path)
            source_path.write_text("Hindi transcript source", encoding="utf-8")
            with (
                patch.object(english.browser, "start_new_chat"),
                patch.object(
                    english,
                    "split_transcript",
                    return_value=["first Hindi chunk", "second Hindi chunk"],
                ),
                patch.object(
                    english.browser,
                    "send_prompt_for_translation",
                    side_effect=send_chunk,
                ) as send_prompt,
            ):
                english.translate_transcript(page, source_path, output_path)

            self.assertEqual(send_prompt.call_count, 2)
            self.assertEqual(events, ["first response completed", "second response completed"])
            self.assertIn("chunk 1 of 2", send_prompt.call_args_list[0].args[1])
            self.assertIn("chunk 2 of 2", send_prompt.call_args_list[1].args[1])
            self.assertIn(
                "This is chunk 2 of 2. Translate only this chunk.",
                send_prompt.call_args_list[1].args[1],
            )
            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                "This is the complete translation for the first chunk.\n\n"
                "This is the complete translation for the second chunk.\n",
            )

    def test_chatgpt_english_translation_rejects_unusable_response_and_keeps_source(self):
        page = Mock()
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "video.txt"
            output_path = english.output_path_for(source_path)
            source_path.write_text("यह एक लंबा हिंदी transcript है।", encoding="utf-8")
            with (
                patch.object(english.browser, "start_new_chat"),
                patch.object(english, "split_transcript", return_value=["Hindi chunk"]),
                patch.object(
                    english.browser,
                    "send_prompt_for_translation",
                    return_value="Short",
                ),
            ):
                with self.assertRaisesRegex(
                    ValueError, "JSON object with a string script field"
                ):
                    english.translate_transcript(page, source_path, output_path)

            self.assertTrue(source_path.exists())
            self.assertFalse(output_path.exists())
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_chatgpt_english_translation_timeout_keeps_source_and_does_not_save_output(self):
        page = Mock()
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "video.txt"
            output_path = english.output_path_for(source_path)
            source_path.write_text("यह एक लंबा हिंदी transcript है।", encoding="utf-8")
            with (
                patch.object(english.browser, "start_new_chat"),
                patch.object(english, "split_transcript", return_value=["Hindi chunk"]),
                patch.object(
                    english.browser,
                    "send_prompt_for_translation",
                    side_effect=TimeoutError("ChatGPT response did not finish in time"),
                ),
            ):
                with self.assertRaisesRegex(TimeoutError, "did not finish in time"):
                    english.translate_transcript(page, source_path, output_path)

            self.assertTrue(source_path.exists())
            self.assertFalse(output_path.exists())
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_chatgpt_removes_bracketed_timestamps_from_the_saved_script(self):
        page = Mock()
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "video.txt"
            output_path = english.output_path_for(source_path)
            source_path.write_text("English caption transcript.", encoding="utf-8")
            with (
                patch.object(english.browser, "start_new_chat"),
                patch.object(
                    english.browser,
                    "send_prompt_for_translation",
                    return_value=(
                        "Latest response\n"
                        '```json\n{"script":"[00:00:03] Fact Monk brings you this amazing '
                        'story.\\n[00:01:12] Keep watching to see what happens."}\n```\n'
                        "ChatGPT can make mistakes. Check important info.\nThink"
                    ),
                ),
            ):
                english.translate_transcript(page, source_path, output_path)

            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                "Fact Monk brings you this amazing story.\n"
                "Keep watching to see what happens.\n",
            )

    def test_chatgpt_discards_text_outside_script_json(self):
        page = Mock()
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "video.txt"
            output_path = english.output_path_for(source_path)
            source_path.write_text("English caption transcript.", encoding="utf-8")
            with (
                patch.object(english.browser, "start_new_chat"),
                patch.object(
                    english.browser,
                    "send_prompt_for_translation",
                    return_value=(
                        "Latest response\n"
                        '{"script":"This is the complete spoken script with enough '
                        'words to pass script validation."}\n'
                        "ChatGPT can make mistakes. Check important info.\nShow more"
                    ),
                ),
            ):
                english.translate_transcript(page, source_path, output_path)

            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                "This is the complete spoken script with enough words to pass script validation.\n",
            )

    def test_chatgpt_rejects_response_without_script_json(self):
        with self.assertRaisesRegex(ValueError, "JSON object with a string script field"):
            english._extract_script("Latest response\nThis is just plain text.")

    def test_chatgpt_english_translation_reports_failure_without_retrying(self):
        page = Mock()
        context = MagicMock()
        context.new_page.return_value = page
        context.pages = [page]
        playwright = MagicMock()
        playwright_context = MagicMock()
        playwright_context.__enter__.return_value = playwright

        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "video.txt"
            source_path.write_text("यह एक लंबा हिंदी transcript है।", encoding="utf-8")
            with (
                patch.object(english, "sync_playwright", return_value=playwright_context),
                patch.object(english.browser, "launch_persistent_context", return_value=context),
                patch.object(
                    english,
                    "translate_transcript",
                    side_effect=TimeoutError("ChatGPT response timed out"),
                ) as translate,
            ):
                with self.assertRaisesRegex(TimeoutError, "timed out"):
                    english.translate_file(source_path)

            translate.assert_called_once_with(
                page,
                source_path,
                english.output_path_for(source_path),
            )
            self.assertTrue(source_path.exists())
            self.assertFalse(english.output_path_for(source_path).exists())
            context.close.assert_called_once_with()

    def test_chatgpt_browser_waits_for_new_response_to_stop_generating(self):
        messages = Mock()
        messages.count.return_value = 1
        response_texts = iter([
            "Partial English translation",
            "Complete English translation, finished.",
            "Complete English translation, finished.",
        ])
        messages.last.inner_text.side_effect = lambda: next(
            response_texts,"Complete English translation, finished."
        )
        page = Mock()
        elapsed = [0]
        page.wait_for_timeout.side_effect = lambda milliseconds: elapsed.__setitem__(
            0, elapsed[0] + milliseconds / 1000
        )
        waiting = Mock()
        with (
            patch.object(english.browser, "assistant_messages", return_value=messages),
            patch.object(
                english.browser,
                "_is_response_generating",
                side_effect=[True, True, False, False],
            ) as is_generating,
            patch.object(english.browser, "log") as log,
            patch.object(english.browser.time, "monotonic", side_effect=lambda: elapsed[0]),
        ):
            response = english.browser.wait_for_completed_response(
                page,
                previous_count=0,
                timeout_seconds=10,
                on_wait=waiting,
                stable_polls=2,
            )

        self.assertEqual(response, "Complete English translation, finished.")
        self.assertEqual(waiting.call_count, 4)
        self.assertEqual(page.wait_for_timeout.call_count, 3)
        self.assertEqual(is_generating.call_count, 4)
        self.assertIn(
            f"ChatGPT response received ({len('Complete English translation, finished.')} characters)",
            [call.args[0] for call in log.call_args_list],
        )

    def test_youtube_response_detection_falls_back_to_text_after_the_prompt(self):
        prompt = "Translate this transcript\nHINDI TRANSCRIPT:\nनमस्ते"
        response = "Hello, this is the complete English translation."
        page_text = Mock()
        page_text.inner_text.return_value = f"{prompt}\n\n{response}\n\nCopy"
        page = Mock()
        page.locator.side_effect = lambda selector: (
            page_text if selector == "main" else Mock(count=Mock(return_value=0))
        )

        with patch.object(
            english.browser,
            "ASSISTANT_MESSAGE_SELECTORS",
            ("[data-message-author-role='assistant']",),
        ):
            result = english.browser._latest_response_for_prompt(
                page,
                {"[data-message-author-role='assistant']": (0, "")},
                prompt,
            )

        self.assertEqual(result, response)

    def test_youtube_response_detection_uses_prompt_tail_when_prompt_is_truncated(self):
        prompt = (
            "Compare transcripts and return a JSON timing object.\n"
            "SOURCE VIDEO TRANSCRIPT:\n"
            "[00:00:00] Long source passage\n"
            "NARRATION AUDIO TRANSCRIPT:\n"
            "[00:00:00] Final narration words are unique and identify this request"
        )
        prompt_tail = " ".join(prompt.split()[-12:])
        response = '{"[00:00:00]-[00:00:10]":"0.75X"}'
        page_text = Mock()
        page_text.inner_text.return_value = (
            "Previous conversation content\n"
            f"{prompt_tail}\n\n{response}\n\nCopy"
        )
        page = Mock()
        page.locator.side_effect = lambda selector: (
            page_text if selector == "main" else Mock(count=Mock(return_value=0))
        )

        with patch.object(
            english.browser,
            "ASSISTANT_MESSAGE_SELECTORS",
            ("[data-message-author-role='assistant']",),
        ):
            result = english.browser._latest_response_for_prompt(
                page,
                {"[data-message-author-role='assistant']": (0, "")},
                prompt,
            )

        self.assertEqual(result, response)

    def test_youtube_response_detection_uses_new_assistant_selector_content(self):
        previous_response = "Previous assistant response."
        new_response = "This is the newly completed English translation."
        assistant_messages = Mock()
        assistant_messages.count.return_value = 2
        assistant_messages.last.inner_text.return_value = new_response
        page = Mock()
        page.locator.return_value = assistant_messages

        with patch.object(
            english.browser,
            "ASSISTANT_MESSAGE_SELECTORS",
            ("[data-message-author-role='assistant']",),
        ):
            result = english.browser._latest_response_for_prompt(
                page,
                {"[data-message-author-role='assistant']": (1, previous_response)},
                "Translate this transcript",
            )

        self.assertEqual(result, new_response)

    def test_chatgpt_browser_logs_response_state_while_waiting(self):
        messages = Mock()
        messages.count.return_value = 1
        messages.last.inner_text.return_value = "A completed response is visible."
        page = Mock()
        elapsed = [0]
        page.wait_for_timeout.side_effect = lambda milliseconds: elapsed.__setitem__(
            0, elapsed[0] + milliseconds / 1000
        )

        with (
            patch.object(english.browser, "assistant_messages", return_value=messages),
            patch.object(english.browser, "_is_response_generating", return_value=True),
            patch.object(english.browser, "log") as log,
            patch.object(english.browser.time, "monotonic", side_effect=lambda: elapsed[0]),
        ):
            with self.assertRaisesRegex(
                TimeoutError,
                f"assistant messages=1, latest response characters="
                f"{len('A completed response is visible.')}, "
                "generation indicator=visible",
            ):
                english.browser.wait_for_completed_response(
                    page,
                    previous_count=0,
                    timeout_seconds=16,
                )

        self.assertTrue(
            any(
                f"latest response characters={len('A completed response is visible.')}"
                in call.args[0]
                and "generation indicator=visible" in call.args[0]
                for call in log.call_args_list
            )
        )

    def test_chatgpt_browser_completed_response_times_out_without_new_assistant_message(self):
        messages = Mock()
        messages.count.return_value = 0
        absent_button = Mock()
        absent_button.last.is_visible.return_value = False
        page = Mock()
        page.locator.return_value = absent_button
        with (
            patch.object(english.browser, "assistant_messages", return_value=messages),
            patch.object(english.browser.time, "monotonic", side_effect=[0, 0.5, 1.1]),
        ):
            with self.assertRaisesRegex(TimeoutError, "did not finish within 1 seconds"):
                english.browser.wait_for_completed_response(
                    page,
                    previous_count=0,
                    timeout_seconds=1,
                )

    def test_english_translation_replaces_existing_output_without_cache_files(self):
        page = Mock()
        context = MagicMock()
        context.new_page.return_value = page
        playwright = MagicMock()
        playwright_context = MagicMock()
        playwright_context.__enter__.return_value = playwright

        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "video.txt"
            output_path = english.output_path_for(source_path)
            source_path.write_text("Hindi source transcript", encoding="utf-8")
            output_path.write_text("Old English translation.", encoding="utf-8")

            def write_translation(_page, _source, target):
                Path(target).write_text("Updated English translation.", encoding="utf-8")
                return Path(target)

            with (
                patch.object(english, "sync_playwright", return_value=playwright_context),
                patch.object(english.browser, "launch_persistent_context", return_value=context),
                patch.object(
                    english,
                    "translate_transcript",
                    side_effect=write_translation,
                ) as translate,
            ):
                result = english.translate_file(source_path)

            self.assertEqual(result, output_path)
            translate.assert_called_once_with(page, source_path, output_path)
            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                "Updated English translation.",
            )
            self.assertTrue(source_path.exists())
            self.assertFalse(list(Path(directory).glob(".*.metadata.json")))
            context.close.assert_called_once_with()

    def test_transcript_text_removes_webvtt_metadata_and_duplicate_cues(self):
        lines = downloader.filter_transcript_lines(
            [
                "WEBVTT",
                "00:00:01.000 --> 00:00:02.000",
                "<c>Hello &amp; welcome.</c>",
                "00:00:02.000 --> 00:00:03.000",
                "Hello &amp; welcome.",
                "00:00:03.000 --> 00:00:04.000",
                "Thanks for watching.",
            ]
        )
        self.assertEqual(
            lines,
            ["Hello & welcome.", "Thanks for watching."],
        )

    def test_filter_removes_markup_timestamps_duplicates_and_overlap(self):
        self.assertEqual(
            downloader.filter_transcript_lines(
                [
                    "<c>We are starting now.</c>",
                    "We are starting now.",
                    "starting now with the next topic.",
                    "[00:00:03.000] Here is the end.",
                ]
            ),
            [
                "We are starting now.",
                "with the next topic.",
                "[00:00:03.000] Here is the end.",
            ],
        )

    def test_extract_video_id_supports_common_youtube_urls(self):
        for url in (
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=playlist",
            "https://youtu.be/dQw4w9WgXcQ?si=tracking",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ",
            "https://www.youtube.com/embed/dQw4w9WgXcQ",
            "https://www.youtube.com/live/dQw4w9WgXcQ",
        ):
            with self.subTest(url=url):
                self.assertEqual(
                    downloader.extract_video_id(url),
                    "dQw4w9WgXcQ",
                )

    def test_extract_video_id_rejects_non_youtube_or_malformed_links(self):
        for url in (
            "https://example.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/not-a-video-id",
            "https://youtube.com/channel/example",
        ):
            with self.subTest(url=url):
                with self.assertRaisesRegex(ValueError, "valid YouTube video ID"):
                    downloader.extract_video_id(url)

    def test_downloads_and_filters_transcript_from_youtube_transcript_api(self):
        transcript = Mock(
            snippets=[
                Mock(start=1.2, text="First useful sentence."),
                Mock(start=2.4, text="First useful sentence."),
                Mock(start=3.8, text="useful sentence. The next point."),
            ]
        )
        api_instance = Mock()
        api_instance.fetch.return_value = transcript
        api_class = Mock(return_value=api_instance)

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "video.txt"
            with patch("youtube_transcript_api.YouTubeTranscriptApi", api_class):
                result = downloader.download_transcript(
                    "https://youtu.be/dQw4w9WgXcQ",
                    output_path,
                    authorized=True,
                )

            self.assertEqual(result, output_path)
            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                "First useful sentence.\n"
                "The next point.\n",
            )
            api_class.assert_called_once()
            self.assertIsInstance(
                api_class.call_args.kwargs["http_client"],
                downloader.TranscriptTimeoutSession,
            )
            api_instance.fetch.assert_called_once_with(
                "dQw4w9WgXcQ",
                languages=["en"],
            )

    def test_downloads_timestamped_transcript_and_groups_cues_by_second(self):
        transcript = Mock(
            snippets=[
                Mock(start=1.2, text="First useful sentence."),
                Mock(start=1.8, text="First useful sentence."),
                Mock(start=3.8, text="The next point."),
            ]
        )
        api_instance = Mock()
        api_instance.fetch.return_value = transcript

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "video.txt"
            with patch(
                "youtube_transcript_api.YouTubeTranscriptApi",
                return_value=api_instance,
            ):
                downloader.download_transcript(
                    "https://youtu.be/dQw4w9WgXcQ",
                    output_path,
                    authorized=True,
                    timestamped=True,
                )

            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                "[00:00:01] First useful sentence.\n"
                "[00:00:03] The next point.\n",
            )

    def test_batch_can_download_video_without_running_transcript_steps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            links = root / "links.txt"
            output = root / "output"
            links.write_text("https://youtu.be/dQw4w9WgXcQ\n", encoding="utf-8")
            video_path = output / "videos" / "output001.mp4"

            with (
                patch.object(downloader, "is_standard_output", return_value=False),
                patch.object(downloader, "download_video", return_value=video_path) as download_video,
                patch.object(downloader, "download_transcript") as download_transcript,
                patch.object(downloader, "process_transcript") as process_transcript,
                patch.object(downloader, "generate_transcript_audio") as generate_audio,
            ):
                result = downloader.download_links(
                    links,
                    output,
                    authorized=True,
                    steps=("download",),
                )

            self.assertEqual(result, [video_path])
            download_video.assert_called_once()
            download_transcript.assert_not_called()
            process_transcript.assert_not_called()
            generate_audio.assert_not_called()

    def test_tts_only_reuses_existing_english_script_without_downloading_video(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            links = root / "links.txt"
            output = root / "output"
            links.write_text("https://youtu.be/dQw4w9WgXcQ\n", encoding="utf-8")
            english_script = output / "transcripts" / "output001_english.txt"
            english_script.parent.mkdir(parents=True)
            english_script.write_text("Already translated script.", encoding="utf-8")

            with (
                patch.object(downloader, "download_video") as download_video,
                patch.object(downloader, "download_transcript") as download_transcript,
                patch.object(downloader, "process_transcript") as process_transcript,
                patch.object(downloader, "generate_transcript_audio") as generate_audio,
            ):
                downloader.download_links(
                    links,
                    output,
                    authorized=True,
                    steps=("tts",),
                )

            download_video.assert_not_called()
            download_transcript.assert_not_called()
            process_transcript.assert_not_called()
            generate_audio.assert_called_once_with(english_script, output)

    def test_timestamped_transcript_only_does_not_run_other_steps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            links = root / "links.txt"
            output = root / "output"
            links.write_text("https://youtu.be/dQw4w9WgXcQ\n", encoding="utf-8")

            with (
                patch.object(downloader, "download_video") as download_video,
                patch.object(downloader, "download_transcript") as download_transcript,
                patch.object(downloader, "process_transcript") as process_transcript,
                patch.object(downloader, "generate_transcript_audio") as generate_audio,
            ):
                downloader.download_links(
                    links,
                    output,
                    authorized=True,
                    steps=("timestamped_transcript",),
                )

            download_video.assert_not_called()
            process_transcript.assert_not_called()
            generate_audio.assert_not_called()
            download_transcript.assert_called_once_with(
                "https://youtu.be/dQw4w9WgXcQ",
                output / "transcripts" / "output001.txt",
                authorized=True,
                language="en",
                timestamped=True,
            )

    def test_tts_automatically_creates_missing_english_prerequisites(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            links = root / "links.txt"
            output = root / "output"
            links.write_text("https://youtu.be/dQw4w9WgXcQ\n", encoding="utf-8")

            def save_transcript(url, path, *, authorized, language, timestamped=False):
                Path(path).write_text("Transcript text.", encoding="utf-8")
                return Path(path)

            def create_english(path):
                result = Path(path).with_name(f"{Path(path).stem}_english.txt")
                result.write_text("Translated TTS script.", encoding="utf-8")
                return result

            with (
                patch.object(downloader, "download_video") as download_video,
                patch.object(downloader, "download_transcript", side_effect=save_transcript) as fetch,
                patch.object(downloader, "process_transcript", side_effect=create_english) as translate,
                patch.object(downloader, "generate_transcript_audio") as generate_audio,
            ):
                downloader.download_links(
                    links,
                    output,
                    authorized=True,
                    steps=("tts",),
                )

            download_video.assert_not_called()
            fetch.assert_called_once_with(
                "https://youtu.be/dQw4w9WgXcQ",
                output / "transcripts" / "output001.txt",
                authorized=True,
                language="en",
            )
            translate.assert_called_once_with(output / "transcripts" / "output001.txt")
            generate_audio.assert_called_once_with(
                output / "transcripts" / "output001_english.txt",
                output,
            )

    def test_reports_when_english_and_hindi_transcripts_are_unavailable(self):
        from youtube_transcript_api import NoTranscriptFound

        api_instance = Mock()
        api_instance.fetch.side_effect = [
            NoTranscriptFound("dQw4w9WgXcQ", ["en"], Mock()),
            NoTranscriptFound("dQw4w9WgXcQ", ["hi"], Mock()),
        ]

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "missing.txt"
            with patch(
                "youtube_transcript_api.YouTubeTranscriptApi",
                return_value=api_instance,
            ):
                with self.assertRaisesRegex(RuntimeError, "No English or Hindi captions"):
                    downloader.download_transcript(
                        "https://youtu.be/dQw4w9WgXcQ",
                        output_path,
                        authorized=True,
                    )
            self.assertEqual(
                api_instance.fetch.call_args_list[0].args,
                ("dQw4w9WgXcQ",),
            )
            self.assertEqual(
                [call.kwargs["languages"] for call in api_instance.fetch.call_args_list],
                [["en"], ["hi"]],
            )
            self.assertFalse(output_path.exists())

    def test_saves_hindi_caption_when_english_is_unavailable(self):
        from youtube_transcript_api import NoTranscriptFound

        api_instance = Mock()
        hindi_transcript = Mock(
            snippets=[Mock(start=12.5, text="यह मूल हिंदी प्रतिलेख है।")]
        )
        api_instance.fetch.side_effect = [
            NoTranscriptFound("dQw4w9WgXcQ", ["en"], Mock()),
            hindi_transcript,
        ]

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "video.txt"
            with patch(
                "youtube_transcript_api.YouTubeTranscriptApi",
                return_value=api_instance,
            ):
                downloader.download_transcript(
                    "https://youtu.be/dQw4w9WgXcQ",
                    output_path,
                    authorized=True,
                )

            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                "यह मूल हिंदी प्रतिलेख है।\n",
            )
            self.assertEqual(
                [call.kwargs["languages"] for call in api_instance.fetch.call_args_list],
                [["en"], ["hi"]],
            )

    def test_fetches_only_hindi_when_hindi_is_selected(self):
        api_instance = Mock()
        api_instance.fetch.return_value = Mock(
            snippets=[Mock(start=0, text="यह हिंदी में है।")]
        )

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "video.txt"
            with patch(
                "youtube_transcript_api.YouTubeTranscriptApi",
                return_value=api_instance,
            ):
                downloader.download_transcript(
                    "https://youtu.be/dQw4w9WgXcQ",
                    output_path,
                    authorized=True,
                    language="hi",
                )

            api_instance.fetch.assert_called_once_with(
                "dQw4w9WgXcQ",
                languages=["hi"],
            )
            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                "यह हिंदी में है।\n",
            )

    def test_rejects_unsupported_transcript_language(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "must be 'en' or 'hi'"):
                downloader.download_transcript(
                    "https://youtu.be/dQw4w9WgXcQ",
                    Path(directory) / "video.txt",
                    authorized=True,
                    language="fr",
                )

    def test_prompts_until_valid_transcript_language_is_chosen(self):
        with patch("builtins.input", side_effect=["invalid", "2"]):
            self.assertEqual(downloader.prompt_transcript_language(), "hi")

    def test_transcript_http_session_sets_a_finite_timeout(self):
        with patch.object(
            downloader.requests.Session,
            "request",
            return_value=Mock(),
        ) as request:
            session = downloader.TranscriptTimeoutSession()
            session.get("https://youtube.com")

        self.assertEqual(
            request.call_args.kwargs["timeout"],
            downloader.TRANSCRIPT_HTTP_TIMEOUT,
        )

    def test_cleans_existing_transcript_before_skipping_it(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            transcript_path = folder / "THE_MORTAL_WHO_SURVIVES_001.txt"
            transcript_path.write_text(
                "WEBVTT\n\n"
                "00:00:01.000 --> 00:00:02.000\n"
                "<c>Keep this line.</c>\n\n"
                "00:00:02.000 --> 00:00:03.000\n"
                "Keep this line.\n",
                encoding="utf-8",
            )

            result = downloader.clean_existing_transcript(transcript_path)

            self.assertEqual(result, transcript_path)
            self.assertEqual(
                transcript_path.read_text(encoding="utf-8"),
                "Keep this line.\n",
            )

    def test_preserves_bracketed_timestamps_for_chatgpt_to_remove(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript_path = Path(directory) / "timed.txt"
            transcript_path.write_text(
                "[00:00:01] <c>First sentence.</c>\n"
                "[00:00:03] Second sentence.\n",
                encoding="utf-8",
            )

            downloader.clean_existing_transcript(transcript_path)

            self.assertEqual(
                transcript_path.read_text(encoding="utf-8"),
                "[00:00:01] First sentence.\n"
                "[00:00:03] Second sentence.\n",
            )

    def test_keeps_existing_hindi_transcript_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript_path = Path(directory) / "hindi.txt"
            original = "यह हिंदी में है।\n"
            transcript_path.write_text(original, encoding="utf-8")
            downloader.clean_existing_transcript(transcript_path)

            self.assertEqual(
                transcript_path.read_text(encoding="utf-8"),
                original,
            )

    def test_mp4_packaging_copies_video_without_resizing(self):
        command = downloader.build_ffmpeg_command("ffmpeg", "source.mkv", "output.mp4")
        video_codec_index = command.index("-c:v") + 1
        self.assertEqual(command[video_codec_index], "copy")
        self.assertNotIn("-vf", command)
        self.assertNotIn("libx264", command)

    def test_video_duration_is_parsed_from_ffmpeg_metadata(self):
        result = Mock(stderr="Input #0, mov,mp4: Duration: 00:03:12.50, start: 0.0")
        with patch.object(downloader.subprocess, "run", return_value=result) as run:
            duration = downloader.get_video_duration("video.mp4", "ffmpeg.exe")

        self.assertEqual(duration, 192.5)
        run.assert_called_once_with(
            ["ffmpeg.exe", "-hide_banner", "-i", "video.mp4"],
            capture_output=True,
            text=True,
        )

    def test_video_duration_probe_fails_if_metadata_is_missing(self):
        with patch.object(
            downloader.subprocess,
            "run",
            return_value=Mock(stderr="No duration metadata"),
        ):
            with self.assertRaisesRegex(RuntimeError, "Could not determine video duration"):
                downloader.get_video_duration("video.mp4", "ffmpeg.exe")

    def test_requires_permission_confirmation_before_download(self):
        with patch.object(downloader, "YoutubeDL") as youtube_dl:
            with self.assertRaises(PermissionError):
                downloader.download_video("https://youtube.com/watch?v=test", "out.mp4", authorized=False)

        youtube_dl.assert_not_called()

    def test_downloads_urls_from_file_and_ignores_comments_and_blank_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            links_file = folder / "ytlink.txt"
            output_directory = folder / "Reborn to End It All"
            links_file.write_text(
                "# authorized videos\nhttps://youtu.be/first\n\n https://youtu.be/second \n",
                encoding="utf-8",
            )

            with patch.object(
                downloader,
                "download_video",
                side_effect=lambda url, path, *, authorized: Path(path),
            ) as download_video, patch.object(
                downloader, "download_transcript"
            ) as download_transcript, patch.object(
                downloader,
                "process_transcript",
                side_effect=self.create_processed_script,
            ), patch.object(downloader, "generate_transcript_audio"):
                outputs = downloader.download_links(links_file, output_directory, authorized=True)

            self.assertEqual(
                [call.args[0] for call in download_video.call_args_list],
                ["https://youtu.be/first", "https://youtu.be/second"],
            )
            self.assertEqual(
                [path.name for path in outputs],
                [
                    "Reborn to End It All001.mp4",
                    "Reborn to End It All002.mp4",
                ],
            )
            self.assertTrue(all(path.parent.name == "videos" for path in outputs))
            transcript_paths = [
                call.args[1] for call in download_transcript.call_args_list
            ]
            self.assertEqual(
                [path.name for path in transcript_paths],
                [
                    "Reborn to End It All001.txt",
                    "Reborn to End It All001.txt",
                    "Reborn to End It All002.txt",
                    "Reborn to End It All002.txt",
                ],
            )
            self.assertTrue(
                all(path.parent.name == "transcripts" for path in transcript_paths)
            )
            self.assertTrue((output_directory / "videos").is_dir())
            self.assertTrue((output_directory / "transcripts").is_dir())
            self.assertEqual(download_transcript.call_count, 4)
            self.assertEqual(
                [call.kwargs.get("timestamped", False) for call in download_transcript.call_args_list],
                [False, True, False, True],
            )

    def test_batch_download_applies_start_number_and_filename_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            links_file = folder / "ytlink.txt"
            output_directory = folder / "output"
            links_file.write_text(
                "https://youtu.be/first\nhttps://youtu.be/second\n",
                encoding="utf-8",
            )

            with (
                patch.object(
                    downloader,
                    "download_video",
                    side_effect=lambda _url, path, *, authorized: Path(path),
                ),
                patch.object(downloader, "download_transcript") as download_transcript,
                patch.object(
                    downloader,
                    "process_transcript",
                    side_effect=self.create_processed_script,
                ),
                patch.object(downloader, "generate_transcript_audio"),
            ):
                outputs = downloader.download_links(
                    links_file,
                    output_directory,
                    authorized=True,
                    start_number=42,
                    name_prefix="Episode",
                )

            self.assertEqual(
                [path.name for path in outputs],
                ["Episode042.mp4", "Episode043.mp4"],
            )
            self.assertEqual(
                [call.args[1].name for call in download_transcript.call_args_list],
                [
                    "Episode042.txt",
                    "Episode042.txt",
                    "Episode043.txt",
                    "Episode043.txt",
                ],
            )

    def test_youtube_batch_cli_passes_numbering_and_prefix_to_downloader(self):
        with patch.object(downloader, "download_links", return_value=[]) as download_links:
            with redirect_stdout(StringIO()):
                downloader.main([
                    "--links",
                    "links.txt",
                    "--output-dir",
                    "results",
                    "--authorized",
                    "--transcript-language",
                    "hi",
                    "--start-number",
                    "42",
                    "--name-prefix",
                    "Episode",
                ])

        download_links.assert_called_once_with(
            Path("links.txt"),
            Path("results"),
            authorized=True,
            transcript_language="hi",
            start_number=42,
            name_prefix="Episode",
            steps=None,
        )

    def test_youtube_batch_cli_passes_selected_steps(self):
        with patch.object(downloader, "download_links", return_value=[]) as download_links:
            with redirect_stdout(StringIO()):
                downloader.main([
                    "--links",
                    "links.txt",
                    "--output-dir",
                    "results",
                    "--authorized",
                    "--transcript-language",
                    "hi",
                    "--steps",
                    " transcript, tts ",
                ])

        self.assertEqual(download_links.call_args.kwargs["steps"], ["transcript", "tts"])

    def test_batch_download_skips_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            links_file = folder / "ytlink.txt"
            output_directory = folder / "downloads"
            links_file.write_text("https://youtu.be/first\n", encoding="utf-8")
            existing_output = output_directory / "videos" / "downloads001.mp4"
            existing_output.parent.mkdir(parents=True)
            existing_output.write_bytes(b"complete")

            with (
                patch.object(downloader, "is_standard_output", return_value=True),
                patch.object(downloader, "download_video") as download_video,
                patch.object(downloader, "download_transcript") as download_transcript,
                patch.object(
                    downloader,
                    "process_transcript",
                    side_effect=self.create_processed_script,
                ),
                patch.object(downloader, "generate_transcript_audio"),
            ):
                outputs = downloader.download_links(
                    links_file,
                    output_directory,
                    authorized=True,
                )

            download_video.assert_not_called()
            self.assertEqual(download_transcript.call_count, 2)
            self.assertEqual(outputs, [existing_output])
            self.assertEqual(
                download_transcript.call_args.args[1],
                output_directory / "transcripts" / "downloads001.txt",
            )

    def test_existing_hindi_transcript_is_processed_by_chatgpt_and_sent_to_cartesia(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            links_file = folder / "ytlink.txt"
            output_directory = folder / "downloads"
            links_file.write_text("https://youtu.be/first\n", encoding="utf-8")
            existing_video = output_directory / "videos" / "downloads001.mp4"
            existing_video.parent.mkdir(parents=True)
            existing_video.write_bytes(b"video")
            existing_transcript = (
                output_directory / "transcripts" / "downloads001.txt"
            )
            existing_transcript.parent.mkdir(parents=True)
            hindi_text = "यह हिंदी में है।\n"
            existing_transcript.write_text(hindi_text, encoding="utf-8")
            english_transcript = existing_transcript.with_name(
                "downloads001_english.txt"
            )

            def process_existing_transcript(path):
                english_transcript.write_text("Processed English script.", encoding="utf-8")
                return english_transcript

            def generate_audio_after_cleanup(path, _output_directory):
                self.assertEqual(path, english_transcript)
                self.assertTrue(path.is_file())
                self.assertTrue(existing_transcript.exists())

            with (
                patch.object(downloader, "is_standard_output", return_value=True),
                patch.object(downloader, "download_video") as download_video,
                patch.object(downloader, "download_transcript") as download_transcript,
                patch.object(
                    downloader,
                    "process_transcript",
                    side_effect=process_existing_transcript,
                ) as process_script,
                patch.object(
                    downloader,
                    "generate_transcript_audio",
                    side_effect=generate_audio_after_cleanup,
                ) as generate_audio,
            ):
                downloader.download_links(links_file, output_directory, authorized=True)

            download_video.assert_not_called()
            download_transcript.assert_called_once_with(
                "https://youtu.be/first",
                existing_transcript,
                authorized=True,
                language="en",
                timestamped=True,
            )
            process_script.assert_called_once_with(existing_transcript)
            self.assertTrue(existing_transcript.exists())
            generate_audio.assert_called_once_with(english_transcript, output_directory)

    def test_new_hindi_transcript_is_processed_by_chatgpt_and_sent_to_cartesia(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            links_file = folder / "ytlink.txt"
            output_directory = folder / "downloads"
            links_file.write_text("https://youtu.be/first\n", encoding="utf-8")
            existing_video = output_directory / "videos" / "downloads001.mp4"
            existing_video.parent.mkdir(parents=True)
            existing_video.write_bytes(b"video")
            transcript_path = output_directory / "transcripts" / "downloads001.txt"
            hindi_text = "यह हिंदी में है।\n"
            english_transcript = transcript_path.with_name(
                "downloads001_english.txt"
            )

            def save_hindi_transcript(url, path, *, authorized, language, timestamped=False):
                self.assertEqual(language, "en")
                Path(path).write_text(
                    "[00:00:01] यह हिंदी में है।\n" if timestamped else hindi_text,
                    encoding="utf-8",
                )
                return Path(path)

            def process_transcript(path):
                english_transcript.write_text("Processed English script.", encoding="utf-8")
                return english_transcript

            def generate_audio_after_cleanup(path, _output_directory):
                self.assertEqual(path, english_transcript)
                self.assertTrue(path.is_file())
                self.assertTrue(transcript_path.exists())

            with (
                patch.object(downloader, "is_standard_output", return_value=True),
                patch.object(downloader, "download_video") as download_video,
                patch.object(
                    downloader,
                    "download_transcript",
                    side_effect=save_hindi_transcript,
                ) as download_transcript,
                patch.object(
                    downloader,
                    "process_transcript",
                    side_effect=process_transcript,
                ) as process_script,
                patch.object(
                    downloader,
                    "generate_transcript_audio",
                    side_effect=generate_audio_after_cleanup,
                ) as generate_audio,
            ):
                downloader.download_links(links_file, output_directory, authorized=True)

            download_video.assert_not_called()
            process_script.assert_called_once_with(transcript_path)
            self.assertEqual(
                transcript_path.read_text(encoding="utf-8"),
                "[00:00:01] यह हिंदी में है।\n",
            )
            self.assertEqual(
                download_transcript.call_count,
                2,
            )
            generate_audio.assert_called_once_with(english_transcript, output_directory)

    def test_english_batch_processes_transcript_then_generates_cartesia_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            links_file = folder / "ytlink.txt"
            output_directory = folder / "downloads"
            links_file.write_text("https://youtu.be/first\n", encoding="utf-8")
            video_path = output_directory / "videos" / "downloads001.mp4"
            video_path.parent.mkdir(parents=True)
            video_path.write_bytes(b"video")
            transcript_path = output_directory / "transcripts" / "downloads001.txt"

            def save_english_transcript(url, path, *, authorized, language, timestamped=False):
                self.assertEqual(language, "en")
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text(
                    "[00:00:01] This is an English transcript.\n"
                    if timestamped
                    else "This is an English transcript.",
                    encoding="utf-8",
                )
                return Path(path)

            with (
                patch.object(downloader, "is_standard_output", return_value=True),
                patch.object(downloader, "download_video") as download_video,
                patch.object(
                    downloader,
                    "download_transcript",
                    side_effect=save_english_transcript,
                ),
                patch.object(
                    downloader,
                    "process_transcript",
                    side_effect=self.create_processed_script,
                ) as process_script,
                patch.object(downloader, "generate_transcript_audio") as generate_audio,
            ):
                downloader.download_links(links_file, output_directory, authorized=True)

            download_video.assert_not_called()
            processed_path = english.output_path_for(transcript_path)
            process_script.assert_called_once_with(transcript_path)
            generate_audio.assert_called_once_with(processed_path, output_directory)

    def test_batch_skips_tts_when_video_is_over_three_minutes(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            links_file = folder / "ytlink.txt"
            output_directory = folder / "downloads"
            links_file.write_text("https://youtu.be/first\n", encoding="utf-8")
            transcript_path = output_directory / "transcripts" / "downloads001.txt"
            over_limit_seconds = downloader.MAX_TTS_VIDEO_DURATION_SECONDS + 1

            with (
                patch.object(downloader, "download_video", return_value=Mock()),
                patch.object(downloader, "download_transcript"),
                patch.object(
                    downloader,
                    "process_transcript",
                    side_effect=self.create_processed_script,
                ) as process_script,
                patch.object(
                    downloader,
                    "get_video_duration",
                    return_value=over_limit_seconds,
                ),
                patch.object(downloader, "generate_transcript_audio") as generate_audio,
                patch("builtins.print") as print_output,
            ):
                downloader.download_links(links_file, output_directory, authorized=True)

            process_script.assert_called_once_with(transcript_path)
            generate_audio.assert_not_called()
            self.assertTrue(
                any(
                    "video duration (00:03:01) exceeds the 3-minute limit"
                    in str(call.args[0])
                    for call in print_output.call_args_list
                )
            )

    def test_hindi_batch_processes_transcript_and_generates_cartesia_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            links_file = folder / "ytlink.txt"
            output_directory = folder / "downloads"
            links_file.write_text("https://youtu.be/first\n", encoding="utf-8")
            existing_video = output_directory / "videos" / "downloads001.mp4"
            existing_video.parent.mkdir(parents=True)
            existing_video.write_bytes(b"video")
            transcript_path = output_directory / "transcripts" / "downloads001.txt"

            def save_hindi_transcript(
                url,
                path,
                *,
                authorized,
                language,
                timestamped=False,
            ):
                self.assertEqual(language, "hi")
                Path(path).write_text(
                    "[00:00:01] यह हिंदी में है।\n"
                    if timestamped
                    else "यह हिंदी में है।\n",
                    encoding="utf-8",
                )
                return Path(path)

            def generate_audio_after_cleanup(path, _output_directory):
                self.assertTrue(path.is_file())
                self.assertTrue(transcript_path.exists())

            with (
                patch.object(downloader, "is_standard_output", return_value=True),
                patch.object(downloader, "download_video") as download_video,
                patch.object(
                    downloader,
                    "download_transcript",
                    side_effect=save_hindi_transcript,
                ) as download_transcript,
                patch.object(
                    downloader,
                    "process_transcript",
                    side_effect=self.create_processed_script,
                ) as process_script,
                patch.object(
                    downloader,
                    "generate_transcript_audio",
                    side_effect=generate_audio_after_cleanup,
                ) as generate_audio,
            ):
                downloader.download_links(
                    links_file,
                    output_directory,
                    authorized=True,
                    transcript_language="hi",
                )

            download_video.assert_not_called()
            self.assertEqual(download_transcript.call_count, 2)
            processed_path = english.output_path_for(transcript_path)
            process_script.assert_called_once_with(transcript_path)
            generate_audio.assert_called_once_with(processed_path, output_directory)
            self.assertEqual(
                transcript_path.read_text(encoding="utf-8"),
                "[00:00:01] यह हिंदी में है।\n",
            )

    def test_batch_skips_existing_output_at_original_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            links_file = folder / "ytlink.txt"
            output_directory = folder / "downloads"
            links_file.write_text("https://youtu.be/first\n", encoding="utf-8")
            old_square_output = output_directory / "videos" / "downloads001.mp4"
            old_square_output.parent.mkdir(parents=True)
            old_square_output.write_bytes(b"old square video")

            with (
                patch.object(downloader, "is_standard_output", return_value=True),
                patch.object(
                    downloader,
                    "download_video",
                    return_value=old_square_output,
                ) as download_video,
                patch.object(downloader, "download_transcript"),
                patch.object(
                    downloader,
                    "process_transcript",
                    side_effect=self.create_processed_script,
                ),
                patch.object(downloader, "generate_transcript_audio"),
            ):
                downloader.download_links(links_file, output_directory, authorized=True)

            download_video.assert_not_called()

    def test_downloads_and_packages_video_without_transcoding_resolution(self):
        youtube_options = {}

        class FakeYoutubeDL:
            def __init__(self, options):
                self.options = options
                youtube_options.update(options)

            def __enter__(self):
                return self

            def __exit__(self, exception_type, exception, traceback):
                return False

            def extract_info(self, url, download):
                self.source_path = Path(
                    self.options["outtmpl"].replace("%(ext)s", "mkv")
                )
                self.source_path.write_bytes(b"source")
                return {"filepath": str(self.source_path)}

        command_used = []

        def fake_ffmpeg(command, **kwargs):
            command_used.extend(command)
            Path(command[-1]).write_bytes(b"converted")
            process = Mock()
            process.stdout = StringIO(
                "out_time_us=1000000\nspeed=1.0x\nprogress=continue\n"
                "out_time_us=2000000\nspeed=1.0x\nprogress=end\n"
            )
            process.wait.return_value = 0
            return process

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "video.mp4"
            with (
                patch.object(downloader, "YoutubeDL", FakeYoutubeDL),
                patch.object(downloader.imageio_ffmpeg, "get_ffmpeg_exe", return_value="ffmpeg.exe"),
                patch.object(downloader.subprocess, "Popen", side_effect=fake_ffmpeg),
            ):
                result = downloader.download_video(
                    "https://youtube.com/watch?v=test",
                    output_path,
                    authorized=True,
                )

            self.assertEqual(result, output_path)
            self.assertEqual(output_path.read_bytes(), b"converted")
            self.assertIn("copy", command_used)
            self.assertEqual(
                command_used[command_used.index("-c:a") + 1],
                "copy",
            )
            self.assertNotIn("-vf", command_used)
            self.assertNotIn("libx264", command_used)
            self.assertIn("-loglevel", command_used)
            self.assertIn("-nostats", command_used)
            self.assertEqual(youtube_options["concurrent_fragment_downloads"], 8)
            self.assertEqual(youtube_options["js_runtimes"], {"node": {}})
            self.assertEqual(
                downloader.DEFAULT_OUTPUT_DIRECTORY,
                Path(r"H:\YOU TUBE\THE MORTAL WHO SURVIVES"),
            )


class CartesiaTTSTests(unittest.TestCase):
    def test_audio_uses_cartesia_sdk_sonic_wav_settings(self):
        response = Mock()

        def write_audio(path):
            Path(path).write_bytes(b"wav bytes")

        response.write_to_file.side_effect = write_audio
        with tempfile.TemporaryDirectory() as directory:
            transcript_path = Path(directory) / "english.txt"
            output_path = Path(directory) / "audio" / "english.wav"
            transcript_path.write_text("Hello, this is spoken text.", encoding="utf-8")

            with patch.object(
                cartesia_tts,
                "Cartesia",
                return_value=Mock(tts=Mock(generate=Mock(return_value=response))),
            ) as cartesia_client:
                cartesia_tts.generate_audio(
                    transcript_path,
                    output_path,
                    "test-api-key",
                    cartesia_tts.DEFAULT_ENGLISH_VOICE_ID,
                    cartesia_tts.DEFAULT_MODEL_ID,
                    speed=1.0,
                    volume=1.2,
                )

            self.assertEqual(output_path.read_bytes(), b"wav bytes")
            cartesia_client.assert_called_once_with(api_key="test-api-key")
            cartesia_client.return_value.tts.generate.assert_called_once_with(
                model_id="sonic-3.6",
                transcript="Hello, this is spoken text.",
                voice="cb9c954d-bcaa-43ed-82bf-aeb5e88a3cb5",
                output_format={
                    "container": "wav",
                    "encoding": "pcm_s16le",
                    "sample_rate": 44100,
                },
                generation_config={"speed": 1, "volume": 1.2},
            )
            response.write_to_file.assert_called_once()

    def test_english_audio_uses_env_api_key_and_voice_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript_path = Path(directory) / "english.txt"
            output_path = Path(directory) / "english.wav"
            transcript_path.write_text("An English transcript.", encoding="utf-8")

            def write_wav(_transcript, target, *_args, **_kwargs):
                Path(target).write_bytes(b"wav")

            with (
                patch.dict(
                    os.environ,
                    {
                        "CARTESIA_API_KEY": "test-api-key",
                        "CARTESIA_ENGLISH_VOICE_ID": "custom-voice",
                        "CARTESIA_MODEL_ID": "sonic-3.6",
                    },
                    clear=True,
                ),
                patch.object(
                    cartesia_tts,
                    "generate_audio",
                    side_effect=write_wav,
                ) as generate_audio,
            ):
                result = cartesia_tts.generate_english_audio(transcript_path, output_path)
                cached_result = cartesia_tts.generate_english_audio(
                    transcript_path,
                    output_path,
                )
                os.environ["CARTESIA_ENGLISH_VOICE_ID"] = "next-voice"
                regenerated_result = cartesia_tts.generate_english_audio(
                    transcript_path,
                    output_path,
                )

            self.assertEqual(result, output_path)
            self.assertEqual(cached_result, output_path)
            self.assertEqual(regenerated_result, output_path)
            self.assertEqual(generate_audio.call_count, 2)
            generate_audio.assert_any_call(
                transcript_path,
                output_path,
                "test-api-key",
                "custom-voice",
                "sonic-3.6",
                speed=1.0,
                volume=1.2,
            )
            generate_audio.assert_called_with(
                transcript_path,
                output_path,
                "test-api-key",
                "next-voice",
                "sonic-3.6",
                speed=1.0,
                volume=1.2,
            )
            metadata = json.loads(
                output_path.with_name(".english.wav.metadata.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(metadata["speed"], 1.0)
            self.assertEqual(metadata["volume"], 1.2)

    def test_english_audio_requires_api_key(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript_path = Path(directory) / "english.txt"
            transcript_path.write_text("An English transcript.", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "CARTESIA_API_KEY"):
                    cartesia_tts.generate_english_audio(
                        transcript_path,
                        Path(directory) / "english.wav",
                    )

    def test_english_audio_regenerates_existing_unverified_wav(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript_path = Path(directory) / "english.txt"
            output_path = Path(directory) / "english.wav"
            transcript_path.write_text("Updated English transcript.", encoding="utf-8")
            output_path.write_bytes(b"old audio")

            def write_new_audio(_transcript, target, *_args, **_kwargs):
                Path(target).write_bytes(b"new audio")

            with (
                patch.dict(os.environ, {"CARTESIA_API_KEY": "test-api-key"}, clear=True),
                patch.object(
                    cartesia_tts,
                    "generate_audio",
                    side_effect=write_new_audio,
                ) as generate_audio,
            ):
                result = cartesia_tts.generate_english_audio(transcript_path, output_path)

            self.assertEqual(result, output_path)
            self.assertEqual(output_path.read_bytes(), b"new audio")
            generate_audio.assert_called_once()
            self.assertTrue(
                json.loads(
                    output_path.with_name(".english.wav.metadata.json").read_text(
                        encoding="utf-8"
                    )
                )["transcript_sha256"]
            )


if __name__ == "__main__":
    unittest.main()