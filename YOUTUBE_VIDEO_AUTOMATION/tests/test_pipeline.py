import os
import tempfile
import unittest
from contextlib import nullcontext
from unittest.mock import MagicMock, Mock, mock_open, patch

from youtube_automation import downloader, pipeline
import requests


class PipelineModeTests(unittest.TestCase):
    def test_main_uses_requested_mode_without_prompting(self):
        with (
            patch.object(pipeline, "_run_pipeline") as run_pipeline,
            patch.object(
                pipeline,
                "choose_pipeline_mode",
                side_effect=AssertionError("interactive prompt should not run"),
            ),
            patch.object(
                pipeline.job_state,
                "pipeline_lock",
                return_value=nullcontext(),
            ),
            patch.object(pipeline.job_state, "record_metric"),
        ):
            pipeline.main(["--mode", "2"])

        run_pipeline.assert_called_once_with(2)

    def test_choose_pipeline_mode_reprompts_for_invalid_choice(self):
        choices = iter(["5", "3"])

        self.assertEqual(pipeline.choose_pipeline_mode(lambda _: next(choices)), 3)

    def test_download_only_modes_skip_transcription_stages(self):
        for mode, include_audio in ((1, False), (2, True)):
            with self.subTest(mode=mode):
                with (
                    patch.object(pipeline.downloader, "run") as download,
                    patch.object(pipeline.gemini_transcribe, "run_queue") as gemini,
                    patch.object(pipeline.chatgpt_transcribe, "run") as chatgpt,
                ):
                    pipeline._run_pipeline(mode)

                download.assert_called_once_with(include_audio=include_audio)
                gemini.assert_not_called()
                chatgpt.assert_not_called()

    def test_gemini_mode_skips_chatgpt_and_all_mode_runs_it(self):
        with (
            patch.object(pipeline.downloader, "run") as download,
            patch.object(pipeline.gemini_transcribe, "run_queue") as gemini,
            patch.object(pipeline.chatgpt_transcribe, "run") as chatgpt,
        ):
            pipeline._run_pipeline(3)

        download.assert_called_once()
        gemini.assert_called_once()
        chatgpt.assert_not_called()

        with (
            patch.object(pipeline.downloader, "run") as download,
            patch.object(pipeline.gemini_transcribe, "run_queue") as gemini,
            patch.object(pipeline.chatgpt_transcribe, "run") as chatgpt,
        ):
            pipeline._run_pipeline(4)

        download.assert_called_once()
        gemini.assert_called_once()
        chatgpt.assert_called_once()


class DownloaderModeTests(unittest.TestCase):
    def test_video_only_download_does_not_require_or_download_audio(self):
        with (
            patch.object(downloader.config, "VIDEO_TITLE", ""),
            patch.object(downloader, "fetch_video_data", return_value={"videoUrl": "video"}),
            patch.object(downloader, "download_media_with_refresh") as download_media,
            patch.object(downloader.job_state, "update_part"),
            patch.object(downloader.job_state, "record_metric"),
        ):
            result = downloader.process_part("source", 7, include_audio=False)

        self.assertTrue(result)
        download_media.assert_called_once_with(
            "source",
            "video",
            downloader.os.path.join(downloader.VIDEO_FOLDER, "PART-7.mp4"),
            "Video",
            7,
        )

    def test_download_uses_configured_video_title_in_filenames(self):
        with (
            patch.object(downloader.config, "VIDEO_TITLE", "THIRD SERIES"),
            patch.object(downloader, "fetch_video_data", return_value={"videoUrl": "video"}),
            patch.object(downloader, "download_media_with_refresh") as download_media,
            patch.object(downloader.job_state, "update_part"),
            patch.object(downloader.job_state, "record_metric"),
        ):
            result = downloader.process_part("source", 1, include_audio=False)

        self.assertTrue(result)
        self.assertEqual(
            download_media.call_args.args[2],
            downloader.os.path.join(downloader.VIDEO_FOLDER, "PART-1_THIRD_SERIES.mp4"),
        )

    def test_interrupted_download_resumes_from_partial_file(self):
        def response(status_code, headers, chunks):
            result = MagicMock()
            result.status_code = status_code
            result.headers = headers
            result.__enter__.return_value = result
            result.iter_content.return_value = chunks
            return result

        def interrupted_chunks():
            yield b"12345"
            raise requests.exceptions.ChunkedEncodingError("incomplete response")

        head_response = MagicMock()
        head_response.headers = {"content-length": "10"}
        head_response.__enter__.return_value = head_response
        first_response = response(
            200,
            {"content-length": "10"},
            interrupted_chunks(),
        )
        resumed_response = response(
            206,
            {"content-length": "5", "Content-Range": "bytes 5-9/10"},
            iter([b"67890"]),
        )
        session = Mock()
        session.head.return_value = head_response
        session.get.side_effect = [first_response, resumed_response]

        with tempfile.TemporaryDirectory() as folder:
            filename = os.path.join(folder, "video.mp4")
            with (
                patch.object(downloader, "get_session", return_value=session),
                patch.object(downloader._thread_state, "cdn_headers", {}, create=True),
                patch.object(downloader.config, "MIN_COMPLETE_FILE_SIZE", 5),
                patch.object(downloader.time, "sleep"),
            ):
                downloader.download_file("media-url", filename, "Video", retries=2)

            self.assertEqual(session.get.call_count, 2)
            self.assertEqual(
                session.get.call_args_list[1].kwargs["headers"]["Range"],
                "bytes=5-",
            )
            with open(filename, "rb") as file:
                self.assertEqual(file.read(), b"1234567890")

    def test_run_retries_failed_parts_and_raises_if_all_attempts_fail(self):
        with (
            patch(
                "builtins.open",
                mock_open(read_data="https://v.kuaishou.com/example\n"),
            ),
            patch.object(downloader.os.path, "exists", return_value=True),
            patch.object(downloader, "START_PART", 61),
            patch.object(downloader, "part_needs_download", return_value=True),
            patch.object(downloader.config, "DOWNLOAD_WORKERS", 1),
            patch.object(downloader, "process_part", return_value=False) as process_part,
        ):
            with self.assertRaisesRegex(RuntimeError, "failed after 3 attempts: PART-61"):
                downloader.run()

        self.assertEqual(process_part.call_count, downloader.MAX_RETRIES)

    def test_run_starts_from_configured_part_number(self):
        with (
            patch(
                "builtins.open",
                mock_open(read_data="https://v.kuaishou.com/example\n"),
            ),
            patch.object(downloader.os.path, "exists", return_value=True),
            patch.object(downloader, "START_PART", 1),
            patch.object(downloader, "part_needs_download", return_value=True),
            patch.object(downloader.config, "DOWNLOAD_WORKERS", 1),
            patch.object(downloader, "process_part", return_value=False) as process_part,
        ):
            with self.assertRaisesRegex(RuntimeError, "failed after 3 attempts: PART-1"):
                downloader.run()

        self.assertEqual(process_part.call_args.args[1], 1)


class ChatGPTTranscribeTests(unittest.TestCase):
    def test_translation_uses_prompt_aware_response_detection(self):
        transcribe = pipeline.chatgpt_transcribe
        page = Mock()
        translated = "Yeh ek poori kahani hai jo natural Hinglish mein likhi gayi hai."
        with tempfile.TemporaryDirectory() as folder:
            transcript_path = os.path.join(folder, "PART-1.txt")
            with open(transcript_path, "w", encoding="utf-8") as file:
                file.write("这是一个足够长的中文故事文本。")
            with (
                patch.object(transcribe.browser, "start_new_chat"),
                patch.object(transcribe.text, "split_transcript", return_value=["Chinese source"]),
                patch.object(
                    transcribe.browser,
                    "send_prompt_for_translation",
                    return_value=translated,
                ) as send_prompt,
                patch.object(transcribe.browser, "send_prompt") as old_send_prompt,
                patch.object(transcribe.job_state, "update_part"),
            ):
                self.assertTrue(transcribe.process_transcript(page, transcript_path, "1"))

            send_prompt.assert_called_once()
            self.assertEqual(send_prompt.call_args.args[0], page)
            self.assertEqual(send_prompt.call_args.args[2], "1")
            old_send_prompt.assert_not_called()
            with open(
                os.path.join(folder, "PART-1_hinglish.txt"),
                encoding="utf-8",
            ) as file:
                self.assertEqual(file.read(), translated)


if __name__ == "__main__":
    unittest.main()