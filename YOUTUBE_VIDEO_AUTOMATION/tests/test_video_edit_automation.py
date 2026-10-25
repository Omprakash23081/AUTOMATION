import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, Mock, patch

from app import (
    VIDEO_EDIT_GEMINI_CACHE_DIR,
    _parse_video_edit_progress,
    run_video_edit_pipeline,
)
from youtube_automation import video_edit


class VideoEditAutomationTests(unittest.TestCase):
    def test_parses_timestamped_transcript_lines_and_milliseconds(self):
        lines = video_edit.parse_timestamped_transcript(
            "[00:00:01.500] मेरा नाम ओम है।\n"
            "[00:00:04] अब चलते हैं।\n"
        )

        self.assertEqual(
            [(line.timestamp, line.text) for line in lines],
            [(1.5, "मेरा नाम ओम है।"), (4.0, "अब चलते हैं।")],
        )

    def test_rejects_invalid_or_nonincreasing_timestamps(self):
        with self.assertRaisesRegex(ValueError, "must use"):
            video_edit.parse_timestamped_transcript("No timestamp here")
        with self.assertRaisesRegex(ValueError, "must increase"):
            video_edit.parse_timestamped_transcript(
                "[00:00:02] पहला वाक्य\n[00:00:01] दूसरा वाक्य"
            )

    def test_gemini_prompt_requests_a_brief_verbatim_timestamped_transcript(self):
        prompt = video_edit.GEMINI_VIDEO_EDIT_PROMPT.casefold()

        self.assertIn("extract the full transcript from the attached audio", prompt)
        self.assertIn("in the language spoken", prompt)
        self.assertIn("do not translate or hallucinate", prompt)
        self.assertIn("each phrase's start", prompt)
        self.assertIn("must be inside the audio, not at its end", prompt)
        self.assertIn("[hh:mm:ss] words", prompt)
        self.assertIn("one plain-text code block", prompt)
        self.assertLess(len(prompt), 300)

    def test_process_video_uses_chatgpt_speed_ranges_then_fits_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            video_path = Path(directory) / "source.mp4"
            audio_path = Path(directory) / "voice.wav"
            transcript_path = Path(directory) / "script.txt"
            output_path = Path(directory) / "synchronized.mp4"
            video_path.write_bytes(b"video")
            audio_path.write_bytes(b"audio")
            transcript_path.write_text(
                "[00:00:00] इतना भारी हवाई जहाज कैसे उड़ता है?\n"
                "[00:00:07] इंजन हवा खींचता है।\n"
                "[00:00:15] आखिरी शब्द।\n",
                encoding="utf-8",
            )
            with (
                patch.object(video_edit.imageio_ffmpeg, "get_ffmpeg_exe", return_value="ffmpeg"),
                patch.object(video_edit, "probe_duration", side_effect=[15, 12]),
                patch.object(
                    video_edit,
                    "transcribe_audio",
                    return_value=(
                        "```text\n"
                        "[00:00:00] How does the airplane fly?\n"
                        "[00:00:12] It uses thrust and lift.\n"
                        "```"
                    ),
                ),
                patch.object(
                    video_edit,
                    "recommend_video_speeds",
                    return_value=[(0, 7, 0.5), (7, 15, 0.75)],
                ) as recommend,
                patch.object(
                    video_edit,
                    "render_video",
                    return_value=output_path,
                ) as render,
            ):
                result = video_edit.process_video(
                    video_path,
                    audio_path,
                    transcript_path,
                    output_path,
                )

        self.assertEqual(result, output_path)
        recommend.assert_called_once()
        self.assertEqual(recommend.call_args.args[2:], (15, 12))
        self.assertEqual(
            [(line.timestamp, line.text) for line in recommend.call_args.args[0]],
            [
                (0, "इतना भारी हवाई जहाज कैसे उड़ता है?"),
                (7, "इंजन हवा खींचता है। आखिरी शब्द।"),
            ],
        )
        render.assert_called_once()
        plan = render.call_args.args[3]
        self.assertEqual([(scene.source_start, scene.source_end) for scene in plan],
                         [(0, 7), (7, 15)])
        self.assertAlmostEqual(plan[-1].target_end, 7 / 0.5 + 8 / 0.75)

    def test_process_video_reuses_gemini_response_after_speed_failure_then_cleans_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video_path = root / "source.mp4"
            audio_path = root / "voice.wav"
            transcript_path = root / "script.txt"
            output_path = root / "synchronized.mp4"
            cache_dir = root / "gemini-cache"
            video_path.write_bytes(b"video")
            audio_path.write_bytes(b"audio")
            transcript_path.write_text(
                "[00:00:00] Good morning\n",
                encoding="utf-8",
            )
            gemini_response = "```text\n[00:00:00] नमस्ते\n```"

            def recommend_after_retry(
                source_lines,
                audio_lines,
                video_duration,
                audio_duration,
            ):
                self.assertTrue(audio_lines)
                self.assertEqual(audio_duration, 12)
                if source_lines[0].text == "Good morning":
                    raise ValueError("ChatGPT did not return valid speed ranges")
                return [(0, video_duration, 1.0)]

            with (
                patch.object(video_edit.imageio_ffmpeg, "get_ffmpeg_exe", return_value="ffmpeg"),
                patch.object(video_edit, "probe_duration", side_effect=[15, 12, 15, 12]),
                patch.object(
                    video_edit,
                    "transcribe_audio",
                    return_value=gemini_response,
                ) as transcribe,
                patch.object(
                    video_edit,
                    "recommend_video_speeds",
                    side_effect=recommend_after_retry,
                ),
                patch.object(
                    video_edit,
                    "render_video",
                    return_value=output_path,
                ) as render,
            ):
                with self.assertRaisesRegex(ValueError, "did not return valid"):
                    video_edit.process_video(
                        video_path,
                        audio_path,
                        transcript_path,
                        output_path,
                        gemini_cache_dir=cache_dir,
                    )

                cache_files = list(cache_dir.glob("*.txt"))
                self.assertEqual(len(cache_files), 1)
                self.assertEqual(
                    cache_files[0].read_text(encoding="utf-8"),
                    gemini_response,
                )
                render.assert_not_called()
                transcript_path.write_text(
                    "[00:00:00] नमस्ते\n",
                    encoding="utf-8",
                )
                result = video_edit.process_video(
                    video_path,
                    audio_path,
                    transcript_path,
                    output_path,
                    gemini_cache_dir=cache_dir,
                )

            self.assertEqual(result, output_path)
            transcribe.assert_called_once_with(audio_path)
            self.assertEqual(list(cache_dir.glob("*.txt")), [])

    def test_speed_recommendation_prompt_includes_both_transcripts_and_durations(self):
        video_lines = video_edit.parse_timestamped_transcript(
            "[00:00:00] विमान उड़ता है।"
        )
        audio_lines = video_edit.parse_timestamped_transcript(
            "[00:00:00] The airplane flies."
        )

        prompt = video_edit.build_speed_recommendation_prompt(
            video_lines,
            audio_lines,
            49,
            53,
        )

        self.assertIn("different languages", prompt)
        self.assertIn("Exact source video duration: 00:00:49", prompt)
        self.assertIn("Exact narration audio duration: 00:00:53", prompt)
        self.assertIn("विमान उड़ता है", prompt)
        self.assertIn("The airplane flies", prompt)
        self.assertIn("full video", prompt)

    def test_video_edit_progress_parser_extracts_steps_and_latest_progress(self):
        snapshot = _parse_video_edit_progress(
            "VIDEO EDIT STEP: 2/7|Checking media durations\n"
            "VIDEO EDIT PROGRESS: 8|Durations checked\n"
            "VIDEO EDIT STEP: 3/7|Transcribing narration\n"
            "VIDEO EDIT PROGRESS: 12|Transcribing narration"
        )

        self.assertEqual(
            snapshot,
            {
                "steps": {
                    2: (7, "Checking media durations"),
                    3: (7, "Transcribing narration"),
                },
                "progress": 12,
                "detail": "Transcribing narration",
            },
        )

    def test_parses_speed_ranges_with_extra_chatgpt_text(self):
        response = (
            "Here is the JSON:\n"
            '```json\n{"[00:00:00]-[00:00:02]":"0.5X",'
            '"[00:00:02]-[00:00:04]":"0.75X"}\n```'
        )

        self.assertEqual(
            video_edit.parse_speed_recommendations(response, 4),
            [(0, 2, 0.5), (2, 4, 0.75)],
        )

    def test_video_edit_uses_video_specific_speed_response_detector(self):
        source_lines = video_edit.parse_timestamped_transcript(
            "[00:00:00] विमान उड़ता है।"
        )
        audio_lines = video_edit.parse_timestamped_transcript(
            "[00:00:00] The airplane flies."
        )
        page = SimpleNamespace()
        context = SimpleNamespace(pages=[page], close=Mock())
        response = '{"[00:00:00]-[00:00:10]":"0.75X"}'

        with (
            patch.object(video_edit, "sync_playwright") as playwright_factory,
            patch.object(
                video_edit.chatgpt_browser,
                "launch_persistent_context",
                return_value=context,
            ),
            patch.object(
                video_edit.chatgpt_browser,
                "start_new_chat",
            ) as start_new_chat,
            patch.object(
                video_edit,
                "_send_speed_prompt",
                return_value=response,
            ) as send_speed_prompt,
        ):
            segments = video_edit.recommend_video_speeds(
                source_lines,
                audio_lines,
                10,
                10,
            )

        start_new_chat.assert_called_once_with(page, "VIDEO-EDIT")
        send_speed_prompt.assert_called_once_with(
            page,
            ANY,
            10,
        )
        self.assertEqual(segments, [(0, 10, 0.75)])
        context.close.assert_called_once()
        playwright_factory.assert_called_once()

    def test_extracts_speed_json_even_when_prompt_text_is_collapsed(self):
        page_text = (
            "Choose moderate speeds. Show more\n"
            '{"[00:00:00]-[00:00:04]":"0.75X",'
            '"[00:00:04]-[00:00:10]":"1.00X"}\n'
            "Copy Read aloud"
        )

        response = video_edit._extract_complete_speed_json(page_text, 10)

        self.assertEqual(
            video_edit.parse_speed_recommendations(response, 10),
            [(0, 4, 0.75), (4, 10, 1.0)],
        )

    def test_does_not_accept_partial_speed_json_as_completed_response(self):
        page_text = '{"[00:00:00]-[00:00:04]":"0.75X"}'

        self.assertEqual(
            video_edit._extract_complete_speed_json(page_text, 10),
            "",
        )

    def test_accepts_rounded_final_timestamp_for_fractional_video_duration(self):
        page_text = '{"[00:00:00]-[00:00:10]":"0.75X"}'

        response = video_edit._extract_complete_speed_json(page_text, 10.4)

        self.assertTrue(response)
        self.assertEqual(
            video_edit.parse_speed_recommendations(response, 10.4),
            [(0, 10, 0.75), (10, 10.4, 1.0)],
        )

    def test_video_edit_reads_rendered_json_and_waits_for_stable_nonstreaming_text(self):
        partial_json = '{"[00:00:00]-[00:00:04]":"0.75X"'
        completed_json = (
            '{"[00:00:00]-[00:00:04]":"0.75X",'
            '"[00:00:04]-[00:00:10]":"1.00X"}'
        )
        state = {"text": "Previous conversation", "elapsed": 0}
        input_box = Mock()
        send_button = Mock()
        send_button.click.side_effect = lambda **_: state.update(text=partial_json)
        page = Mock()

        def locator(selector):
            if selector == "button[data-testid='send-button']":
                return SimpleNamespace(
                    last=SimpleNamespace(
                        is_visible=lambda: True,
                        click=send_button.click,
                    )
                )
            text = state["text"]
            return SimpleNamespace(
                count=lambda: 1,
                last=SimpleNamespace(inner_text=lambda: text),
            )

        page.locator.side_effect = locator

        def wait_for_timeout(milliseconds):
            state["elapsed"] += milliseconds / 1000
            state["text"] = completed_json

        page.wait_for_timeout.side_effect = wait_for_timeout

        with (
            patch.object(video_edit.chatgpt_browser, "find_chat_input", return_value=input_box),
            patch.object(
                video_edit,
                "_video_edit_response_is_streaming",
                side_effect=[True, False, False, False],
            ),
            patch.object(video_edit.config, "CHATGPT_RESPONSE_TIMEOUT_SECONDS", 10),
            patch.object(
                video_edit.time,
                "monotonic",
                side_effect=lambda: state["elapsed"],
            ),
        ):
            response = video_edit._send_speed_prompt(page, "speed prompt", 10)

        self.assertEqual(response, completed_json)
        input_box.fill.assert_called_once_with("speed prompt")
        send_button.click.assert_called_once_with(timeout=15000)
        self.assertEqual(page.wait_for_timeout.call_count, 3)

    def test_adds_normal_speed_for_uncovered_video_tail(self):
        self.assertEqual(
            video_edit.parse_speed_recommendations(
                '{"[00:00:00]-[00:00:02]":"0.5X"}',
                4,
            ),
            [(0, 2, 0.5), (2, 4, 1.0)],
        )

    def test_ignores_trailing_zero_length_range_at_video_duration(self):
        response = (
            '{"[00:00:00]-[00:00:04]":"0.8X",'
            '"[00:00:04]-[00:00:08]":"0.8X",'
            '"[00:00:08]-[00:00:13]":"0.8X",'
            '"[00:00:13]-[00:00:17]":"0.75X",'
            '"[00:00:17]-[00:00:23]":"0.75X",'
            '"[00:00:23]-[00:00:28]":"0.8X",'
            '"[00:00:28]-[00:00:31]":"0.75X",'
            '"[00:00:31]-[00:00:35]":"0.65X",'
            '"[00:00:35]-[00:00:38]":"0.6X",'
            '"[00:00:38]-[00:00:38]":"0.5X"}'
        )

        segments = video_edit.parse_speed_recommendations(response, 38)

        self.assertEqual(len(segments), 9)
        self.assertEqual(segments[-1], (35, 38, 0.6))

    def test_ignores_zero_length_boundary_and_fills_unreported_video_tail(self):
        segments = video_edit.parse_speed_recommendations(
            '{"[00:00:00]-[00:00:38]":"0.8X",'
            '"[00:00:38]-[00:00:38]":"0.5X"}',
            38.2,
        )

        self.assertEqual(segments, [(0, 38, 0.8), (38, 38.2, 1.0)])

    def test_rejects_zero_length_range_inside_video(self):
        with self.assertRaisesRegex(ValueError, "invalid or out-of-video"):
            video_edit.parse_speed_recommendations(
                '{"[00:00:00]-[00:00:02]":"0.8X",'
                '"[00:00:02]-[00:00:02]":"0.5X",'
                '"[00:00:02]-[00:00:04]":"0.8X"}',
                4,
            )

    def test_rejects_speed_ranges_with_gaps_or_bad_speeds(self):
        with self.assertRaisesRegex(ValueError, "contiguous"):
            video_edit.parse_speed_recommendations(
                '{"[00:00:00]-[00:00:02]":"0.5X",'
                '"[00:00:03]-[00:00:04]":"0.75X"}',
                4,
            )
        with self.assertRaisesRegex(ValueError, "Invalid playback speed"):
            video_edit.parse_speed_recommendations(
                '{"[00:00:00]-[00:00:04]":"fast"}',
                4,
            )

    def test_global_fit_factor_uses_measured_intermediate_video_duration(self):
        plan = video_edit.build_scene_plan_from_speeds(
            [(0, 20, 0.5), (20, 45, 0.75)],
        )
        measured_duration = 45
        audio_duration = 49

        self.assertAlmostEqual(plan[-1].target_end, 20 / 0.5 + 25 / 0.75)
        self.assertAlmostEqual(
            video_edit.calculate_global_speed_factor(
                measured_duration,
                audio_duration,
            ),
            45 / 49,
        )
        self.assertIn(
            "setpts=1.088888889*(PTS-STARTPTS)",
            video_edit.build_global_fit_filter(
                measured_duration,
                audio_duration,
            ),
        )

    def test_gemini_transcript_parser_filters_untimestamped_paragraphs(self):
        transcript = video_edit.parse_gemini_audio_transcript(
            "Here is the transcript:\n"
            "```text\n"
            "[00:00:01] Bonjour, comment allez-vous ?\n"
            "This untimestamped explanation must be ignored.\n"
            "[00:00:02] Je vais bien.\n"
            "```"
        )

        self.assertEqual(
            [(line.timestamp, line.text) for line in transcript],
            [
                (1.0, "Bonjour, comment allez-vous ?"),
                (2.0, "Je vais bien."),
            ],
        )

    def test_gemini_transcript_parser_splits_multiple_timestamped_phrases_on_one_line(self):
        transcript = video_edit.parse_gemini_audio_transcript(
            "Transcript: [00:00:01] Bonjour. [00:00:02] Je vais bien."
        )

        self.assertEqual(
            [line.text for line in transcript],
            ["Bonjour.", "Je vais bien."],
        )

    def test_gemini_transcript_parser_rejects_response_without_timestamped_speech(self):
        with self.assertRaisesRegex(
            ValueError,
            "returned no usable timestamped speech[\\s\\S]*Response preview: "
            "'Please provide audio in Hindi'",
        ):
            video_edit.parse_gemini_audio_transcript(
                "Please provide audio in Hindi"
            )

    def test_normalizes_rounded_final_gemini_timestamp_at_audio_end(self):
        audio_lines = video_edit.parse_timestamped_transcript(
            "[00:00:00] The introduction\n"
            "[00:00:48] The final\n"
            "[00:00:49] phrase"
        )

        normalized = video_edit.normalize_audio_transcript_end(
            audio_lines,
            48.9,
        )

        self.assertEqual(
            [(line.timestamp, line.text) for line in normalized],
            [
                (0, "The introduction"),
                (48, "The final phrase"),
            ],
        )

    def test_merges_all_final_gemini_lines_beyond_audio_end(self):
        audio_lines = video_edit.parse_timestamped_transcript(
            "[00:00:00] The introduction\n"
            "[00:00:45] The ending begins\n"
            "[00:00:50] The final phrase\n"
            "[00:00:52.700] The last words"
        )

        normalized = video_edit.normalize_audio_transcript_end(
            audio_lines,
            48.9,
        )

        self.assertEqual(
            [(line.timestamp, line.text) for line in normalized],
            [
                (0, "The introduction"),
                (
                    45,
                    "The ending begins The final phrase The last words",
                ),
            ],
        )

    def test_rejects_transcript_with_no_timestamp_before_audio_end(self):
        audio_lines = video_edit.parse_timestamped_transcript(
            "[00:00:49] The only phrase"
        )

        with self.assertRaisesRegex(ValueError, "no timestamp before"):
            video_edit.normalize_audio_transcript_end(audio_lines, 48.9)

    def test_normalizes_source_transcript_lines_at_or_beyond_video_end(self):
        source_lines = video_edit.parse_timestamped_transcript(
            "[00:00:00] Opening scene\n"
            "[00:00:38] Final line begins\n"
            "[00:00:39] Final words"
        )

        normalized = video_edit.normalize_source_transcript_end(
            source_lines,
            38.7,
        )

        self.assertEqual(
            [(line.timestamp, line.text) for line in normalized],
            [
                (0, "Opening scene"),
                (38, "Final line begins Final words"),
            ],
        )

    def test_rejects_source_transcript_with_no_timestamp_before_video_end(self):
        source_lines = video_edit.parse_timestamped_transcript(
            "[00:00:39] Only spoken line"
        )

        with self.assertRaisesRegex(ValueError, "no timestamp before"):
            video_edit.normalize_source_transcript_end(source_lines, 38.7)

    def test_video_filter_retimes_scenes_then_concatenates_them(self):
        graph = video_edit.build_video_filter(
            [
                video_edit.SceneTiming(0, 3, 0, 4),
                video_edit.SceneTiming(3, 12, 4, 10),
            ]
        )

        self.assertIn("setpts=1.333333333*(PTS-STARTPTS)", graph)
        self.assertIn("setpts=0.666666667*(PTS-STARTPTS)", graph)
        self.assertIn(
            "[v0][v1]concat=n=2:v=1:a=0[vconcat]",
            graph,
        )
        self.assertIn(
            "[vconcat]tpad=stop_mode=clone:stop_duration=1,"
            "trim=duration=10.000000,setpts=PTS-STARTPTS[outv]",
            graph,
        )

    def test_gemini_transcript_validation_rejects_assistant_commentary(self):
        response = (
            "[00:00:00] नमस्ते\n"
            "[00:00:10] Mere is the transcript extracted from the audio "
            "with timestamps"
        )

        with self.assertRaisesRegex(ValueError, "assistant commentary"):
            video_edit.validate_gemini_audio_transcript(response, 12)

    def test_invalid_cached_gemini_transcript_is_discarded_and_retranscribed(self):
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "voice.wav"
            cache_dir = Path(directory) / "gemini-cache"
            audio_path.write_bytes(b"audio")
            cache_path = video_edit._audio_cache_path(audio_path, cache_dir)
            cache_path.parent.mkdir(parents=True)
            cache_path.write_text(
                "[00:00:00] नमस्ते\n"
                "[00:00:10] This is the transcript extracted from the audio",
                encoding="utf-8",
            )
            fresh_response = (
                "```text\n"
                "[00:00:00] नमस्ते\n"
                "[00:00:05] आपका स्वागत है।\n"
                "```"
            )

            with patch.object(
                video_edit,
                "transcribe_audio",
                return_value=fresh_response,
            ) as transcribe:
                response, returned_cache_path = video_edit._load_or_transcribe_audio(
                    audio_path,
                    cache_dir,
                    8,
                )

            transcribe.assert_called_once_with(audio_path)
            self.assertEqual(response, fresh_response)
            self.assertEqual(returned_cache_path, cache_path)
            self.assertEqual(
                cache_path.read_text(encoding="utf-8"),
                fresh_response,
            )

    def test_invalid_fresh_gemini_transcript_is_not_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "voice.wav"
            cache_dir = Path(directory) / "gemini-cache"
            audio_path.write_bytes(b"audio")

            with patch.object(
                video_edit,
                "transcribe_audio",
                return_value="[00:00:00] Here is the transcript",
            ):
                with self.assertRaisesRegex(ValueError, "assistant commentary"):
                    video_edit._load_or_transcribe_audio(
                        audio_path,
                        cache_dir,
                        8,
                    )

            self.assertFalse(cache_dir.exists())

    def test_render_video_retimes_then_measures_and_fits_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video_path = root / "source.mp4"
            audio_path = root / "narration.wav"
            output_path = root / "synchronized.mp4"
            video_path.write_bytes(b"source")
            audio_path.write_bytes(b"audio")
            commands = []

            def fake_ffmpeg(command, **kwargs):
                self.assertEqual(kwargs["stdout"], video_edit.subprocess.PIPE)
                self.assertEqual(kwargs["stderr"], video_edit.subprocess.STDOUT)
                commands.append(command)
                Path(command[-1]).write_bytes(b"rendered")
                return SimpleNamespace(
                    stdout=io.StringIO(
                        "out_time_us=N/A\n"
                        "speed=1.0x\n"
                        "progress=continue\n"
                        "out_time_us=45000000\n"
                        "speed=1.0x\n"
                        "progress=end\n"
                    ),
                    wait=Mock(return_value=0),
                    poll=Mock(return_value=0),
                )

            with (
                patch.object(
                    video_edit.subprocess,
                    "Popen",
                    side_effect=fake_ffmpeg,
                ),
                patch.object(
                    video_edit,
                    "probe_duration",
                    return_value=45,
                ) as probe,
                patch.object(
                    video_edit,
                    "_report_video_edit_progress",
                ) as report_progress,
            ):
                result = video_edit.render_video(
                    video_path,
                    audio_path,
                    output_path,
                    [
                        video_edit.SceneTiming(0, 45, 0, 45),
                    ],
                    "ffmpeg",
                    49,
                )

            self.assertEqual(result.read_bytes(), b"rendered")
            self.assertEqual(len(commands), 2)
            self.assertIn("-an", commands[0])
            self.assertNotIn(str(audio_path), commands[0])
            self.assertEqual(
                commands[1][commands[1].index("-i") + 1],
                commands[0][-1],
            )
            self.assertIn(str(audio_path), commands[1])
            fit_filter = commands[1][commands[1].index("-filter_complex") + 1]
            self.assertIn("setpts=1.088888889*(PTS-STARTPTS)", fit_filter)
            self.assertIn("trim=duration=49.000000", fit_filter)
            self.assertEqual(
                commands[1][commands[1].index("-t") + 1],
                "49.000000",
            )
            probe.assert_called_once()
            progress_updates = [
                call.args
                for call in report_progress.call_args_list
            ]
            self.assertIn((65, "Retiming source video: complete"), progress_updates)
            self.assertIn((98, "Final video and audio render: complete"), progress_updates)

    def test_ffmpeg_progress_always_stops_child_if_progress_callback_raises(self):
        process_state = {"running": True}
        ffmpeg_process = SimpleNamespace(
            stdout=io.StringIO(
                "out_time_us=1000000\n"
                "progress=continue\n"
            ),
            poll=lambda: None if process_state["running"] else 0,
            terminate=lambda: process_state.update(running=False),
            wait=Mock(return_value=0),
            kill=Mock(),
        )
        with (
            patch.object(
                video_edit.subprocess,
                "Popen",
                return_value=ffmpeg_process,
            ),
            patch.object(
                video_edit,
                "_report_video_edit_progress",
                side_effect=RuntimeError("progress callback failed"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "progress callback failed"):
                video_edit._run_ffmpeg_with_progress(
                    ["ffmpeg", "output.mp4"],
                    10,
                    "Test render",
                    0,
                    100,
                )

        self.assertFalse(process_state["running"])
        ffmpeg_process.wait.assert_called_once_with(timeout=5)
        self.assertTrue(ffmpeg_process.stdout.closed)

    def test_probe_duration_reads_ffmpeg_duration_metadata(self):
        with patch.object(
            video_edit.subprocess,
            "run",
            return_value=type("Result", (), {"stderr": "Duration: 00:02:10.250"})(),
        ):
            self.assertEqual(video_edit.probe_duration("ffmpeg.exe", "audio.wav"), 130.25)

    def test_upload_workflow_saves_inputs_temporarily_and_invokes_pipeline(self):
        video = io.BytesIO(b"video data")
        video.name = "sample.mp4"
        audio = io.BytesIO(b"audio data")
        audio.name = "voice.wav"
        transcript = io.BytesIO("[00:00:00] नमस्ते".encode("utf-8"))
        transcript.name = "voice.txt"

        with tempfile.TemporaryDirectory() as directory:
            output_directory = Path(directory) / "output"

            def run_command(*args):
                command = args[1]
                input_paths = [
                    Path(command[command.index("--video") + 1]),
                    Path(command[command.index("--audio") + 1]),
                    Path(command[command.index("--transcript") + 1]),
                ]
                self.assertEqual([path.read_bytes() for path in input_paths], [
                    b"video data",
                    b"audio data",
                    "[00:00:00] नमस्ते".encode("utf-8"),
                ])
                self.assertEqual(args[0], "VIDEO EDIT AUTOMATION")
                self.assertEqual(
                    Path(command[command.index("--gemini-cache-dir") + 1]),
                    VIDEO_EDIT_GEMINI_CACHE_DIR,
                )
                return {
                    "platform": args[0],
                    "status": "success",
                    "error": None,
                    "output": "Saved Hindi-synchronized video",
                    "output_directory": str(output_directory),
                }

            state = SimpleNamespace()
            with (
                patch("app.st.session_state", state),
                patch("app.run_command", side_effect=run_command),
            ):
                run_video_edit_pipeline(
                    video,
                    audio,
                    transcript,
                    str(output_directory),
                    60,
                )

            self.assertEqual(state.pipeline_result["platform"], "VIDEO EDIT AUTOMATION")
            self.assertEqual(state.pipeline_result["status"], "success")


if __name__ == "__main__":
    unittest.main()
