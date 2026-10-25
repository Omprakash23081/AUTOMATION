import importlib.util
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from local_agent.references import LocalFileReferences
from local_agent.video_sync import VideoProcessingCancelled, process_video_sync


class LocalVideoTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database = self.root / "agent-files.sqlite3"
        self.video = self.root / "source.mp4"
        self.audio = self.root / "narration.wav"
        self.output = self.root / "exports"
        self.output.mkdir()
        self.video.write_bytes(b"video input")
        self.audio.write_bytes(b"audio input")
        self.references = LocalFileReferences(self.database)
        self.payload = self.references.create_video_job(
            self.video, self.audio, self.output
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_job_contains_only_opaque_local_file_references(self):
        self.assertEqual(set(self.payload), {"jobType", "videoRef", "audioRef", "outputRef"})
        self.assertNotIn(str(self.video), repr(self.payload))
        self.assertNotIn(str(self.output), repr(self.payload))
        resolved = self.references.resolve_video_job(self.payload)
        self.assertEqual(resolved["videoRef"], self.video.resolve())
        self.assertEqual(resolved["audioRef"], self.audio.resolve())
        self.assertEqual(resolved["outputRef"].parent, self.output.resolve())

    def test_missing_input_and_invalid_output_paths_are_rejected(self):
        with self.assertRaises(FileNotFoundError):
            self.references.create_video_job(
                self.root / "missing.mp4", self.audio, self.output
            )
        with self.assertRaises((FileNotFoundError, ValueError)):
            self.references.create_video_job(
                self.video, self.audio, self.root / "not-a-folder.txt"
            )
        with self.assertRaisesRegex(ValueError, "existing output folder"):
            self.references.create_video_job(self.video, self.audio, self.video)

    def test_forged_or_changed_references_are_rejected(self):
        forged = {**self.payload, "videoRef": "f" * 32}
        with self.assertRaisesRegex(ValueError, "not selected"):
            self.references.resolve_video_job(forged)
        with self.assertRaisesRegex(ValueError, "invalid"):
            self.references.resolve_video_job({**self.payload, "extra": "path"})

    def test_video_sync_uses_existing_renderer_and_validates_export(self):
        class FakeCancelled(Exception):
            pass

        calls = {"durations": [], "render": None, "render_count": 0}

        def render_video(video, audio, output, scene_plan, ffmpeg, duration, **kwargs):
            calls["render"] = (video, audio, output, scene_plan, ffmpeg, duration)
            calls["render_count"] += 1
            output.write_bytes(b"rendered mp4")
            kwargs["progress_callback"](50, "Rendering test output")
            return output

        fake_module = SimpleNamespace(
            imageio_ffmpeg=SimpleNamespace(get_ffmpeg_exe=lambda: "fake-ffmpeg"),
            probe_duration=lambda ffmpeg, path: (
                calls["durations"].append(Path(path)) or 5.0
            ),
            build_scene_plan_from_speeds=lambda ranges: ranges,
            render_video=render_video,
            ProcessingCancelled=FakeCancelled,
        )

        with patch("local_agent.video_sync._video_edit_module", return_value=fake_module):
            result = process_video_sync(
                "a" * 32,
                1,
                self.payload,
                self.references,
                threading.Event(),
                lambda *_: None,
            )

        self.assertTrue(result.is_file())
        self.assertGreater(result.stat().st_size, 0)
        self.assertEqual(calls["render"][0], self.video.resolve())
        self.assertEqual(calls["render"][1], self.audio.resolve())
        self.assertEqual(calls["render"][4], "fake-ffmpeg")
        self.assertEqual(calls["render"][5], 5.0)
        self.assertEqual(len(calls["durations"]), 3)
        with patch("local_agent.video_sync._video_edit_module", return_value=fake_module):
            recovered = process_video_sync(
                "a" * 32,
                2,
                self.payload,
                self.references,
                threading.Event(),
                lambda *_: None,
            )
        self.assertEqual(recovered, result)
        self.assertEqual(calls["render_count"], 1)

    def test_video_sync_cancellation_and_renderer_failure_propagate(self):
        class FakeCancelled(Exception):
            pass

        def render_video(*_args, **_kwargs):
            raise FakeCancelled("cancelled in FFmpeg")

        fake_module = SimpleNamespace(
            imageio_ffmpeg=SimpleNamespace(get_ffmpeg_exe=lambda: "fake-ffmpeg"),
            probe_duration=lambda *_: 5.0,
            build_scene_plan_from_speeds=lambda ranges: ranges,
            render_video=render_video,
            ProcessingCancelled=FakeCancelled,
        )
        with patch("local_agent.video_sync._video_edit_module", return_value=fake_module):
            with self.assertRaises(VideoProcessingCancelled):
                process_video_sync(
                    "a" * 32,
                    1,
                    self.payload,
                    self.references,
                    threading.Event(),
                    lambda *_: None,
                )
            fake_module.render_video = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("FFmpeg failed")
            )
            with self.assertRaisesRegex(RuntimeError, "FFmpeg failed"):
                process_video_sync(
                    "b" * 32,
                    1,
                    self.payload,
                    self.references,
                    threading.Event(),
                    lambda *_: None,
                )

    @unittest.skipUnless(
        importlib.util.find_spec("imageio_ffmpeg")
        and importlib.util.find_spec("dotenv"),
        "Install imageio-ffmpeg and python-dotenv for the real FFmpeg integration test.",
    )
    def test_real_ffmpeg_video_sync_creates_a_valid_local_export(self):
        from local_agent.video_sync import _video_edit_module

        video_edit = _video_edit_module()
        ffmpeg = video_edit.imageio_ffmpeg.get_ffmpeg_exe()
        video = self.root / "real-source.mp4"
        audio = self.root / "real-narration.wav"
        output = self.root / "real-exports"
        output.mkdir()
        subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=blue:s=64x64:r=10",
                "-t", "1", "-pix_fmt", "yuv420p", "-c:v", "libx264", str(video),
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
                "-t", "0.8", "-c:a", "pcm_s16le", str(audio),
            ],
            check=True,
            capture_output=True,
        )
        references = LocalFileReferences(self.root / "real-agent-files.sqlite3")
        payload = references.create_video_job(video, audio, output)

        result = process_video_sync(
            "d" * 32,
            1,
            payload,
            references,
            threading.Event(),
            lambda *_: None,
        )

        self.assertTrue(result.is_file())
        self.assertGreater(result.stat().st_size, 0)
        self.assertAlmostEqual(video_edit.probe_duration(ffmpeg, result), 0.8, delta=0.15)


if __name__ == "__main__":
    unittest.main()
