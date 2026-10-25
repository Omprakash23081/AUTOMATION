import importlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import types
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
_data = str(Path(__file__).resolve().parents[1] / "data" / "test-suite")
Path(_data).mkdir(parents=True, exist_ok=True)
os.environ["CUTROOM_DATA"] = _data

import automation_integration as automation
import server


class AutomationInputTests(unittest.TestCase):
    def test_automation_home_prefers_project_copy_to_a_stale_sibling(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            project = root / "cutroom"
            local_automation = project / "YOUTUBE_VIDEO_AUTOMATION"
            sibling_automation = root / "YOUTUBE_VIDEO_AUTOMATION"
            (local_automation / "youtube_automation").mkdir(parents=True)
            (sibling_automation / "youtube_automation").mkdir(parents=True)

            with (
                patch.object(automation.server, "ROOT", project),
                patch.dict(os.environ, {"YOUTUBE_VIDEO_AUTOMATION_HOME": ""}),
            ):
                self.assertEqual(automation._automation_home(), local_automation)

    def test_automation_home_respects_explicit_environment_override(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            project = root / "cutroom"
            override = root / "custom-automation"
            (project / "YOUTUBE_VIDEO_AUTOMATION" / "youtube_automation").mkdir(parents=True)
            (override / "youtube_automation").mkdir(parents=True)

            with (
                patch.object(automation.server, "ROOT", project),
                patch.dict(os.environ, {"YOUTUBE_VIDEO_AUTOMATION_HOME": str(override)}),
            ):
                self.assertEqual(automation._automation_home(), override)

    def test_kuaishou_filename_includes_title_after_part_number(self):
        self.assertEqual(
            automation._kuaishou_part_filename(108, "Sample Video"),
            "PART-108_Sample_Video",
        )

    def test_download_progress_logs_update_the_job_percentage(self):
        self.assertEqual(
            automation._download_progress_from_log(
                "[Downloader][PART-61] Video download progress: 5 MB / 10 MB (50%) · 1 MB/s · ~5s remaining"
            ),
            50,
        )
        self.assertEqual(
            automation._download_progress_from_log(
                "[Downloader][PART-61] Starting Audio download: PART-61.m4a"
            ),
            0,
        )
        self.assertIsNone(
            automation._download_progress_from_log(
                "[Downloader][PART-61] Audio download progress: 10 MB downloaded · 1 MB/s · remaining unknown"
            )
        )

    def test_only_https_youtube_video_hosts_are_accepted(self):
        self.assertEqual(
            automation._validate_youtube_urls("https://www.youtube.com/watch?v=abc"),
            ["https://www.youtube.com/watch?v=abc"],
        )
        with self.assertRaisesRegex(ValueError, "Only HTTPS YouTube"):
            automation._validate_youtube_urls("https://example.com/video")
        with self.assertRaisesRegex(ValueError, "Only HTTPS YouTube"):
            automation._validate_youtube_urls("http://youtu.be/abc")

    def test_transcript_selection_stays_inside_the_scanned_folder(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            folder = Path(temporary_directory).resolve()
            source = folder / "transcript.txt"
            source.write_text("original transcript", encoding="utf-8")
            self.assertEqual(automation._read_text_file(folder, source.name), source)
            self.assertEqual(source.read_text(encoding="utf-8"), "original transcript")
            with self.assertRaisesRegex(ValueError, "Choose a .txt file"):
                automation._read_text_file(folder, str(source))
            with self.assertRaisesRegex(ValueError, "not available"):
                automation._read_text_file(folder, "missing.txt")

    def test_video_edit_upload_validation_rejects_empty_or_oversized_files(self):
        upload = Mock()
        upload.filename = "source.mp4"
        upload.stream = io.BytesIO(b"video")
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "source_video.mp4"
            with self.assertRaisesRegex(ValueError, "size limit"):
                automation._save_video_edit_upload(upload, destination, max_bytes=4)
            upload.filename = "../source.mp4"
            with self.assertRaisesRegex(ValueError, "valid filename"):
                automation._save_video_edit_upload(upload, destination, max_bytes=100)
            upload.filename = "source.mp4"
            upload.stream = io.BytesIO(b"")
            with self.assertRaisesRegex(ValueError, "empty"):
                automation._save_video_edit_upload(upload, destination, max_bytes=100)

    def test_video_edit_job_builds_sync_command_for_direct_uploads(self):
        job_id = uuid.uuid4().hex
        workspace_id = f"video-edit-test-{uuid.uuid4().hex}"
        command, working, output, env = automation._build_automation_job(
            workspace_id,
            job_id,
            {
                "workflow": "video_edit_sync",
                "videoFile": "source.mkv",
                "audioFile": "hindi.wav",
                "transcriptFile": "hindi.txt",
            },
        )
        try:
            self.assertEqual(command[1:4], ["-m", "youtube_automation.video_edit", "--video"])
            self.assertEqual(
                command[command.index("--video") + 1],
                str(working / "source_video.mkv"),
            )
            self.assertEqual(
                command[command.index("--audio") + 1],
                str(working / "hindi_audio.wav"),
            )
            self.assertEqual(
                command[command.index("--transcript") + 1],
                str(working / "audio_transcript.txt"),
            )
            self.assertEqual(
                command[command.index("--gemini-cache-dir") + 1],
                str(automation._workspace_output_root(workspace_id) / "temp" / "video_edit_gemini"),
            )
            self.assertEqual(
                Path(command[command.index("--output") + 1]),
                output / "videos" / "source_hindi_synchronized.mp4",
            )
            self.assertEqual(Path(env["PLAYWRIGHT_PROFILE_DIR"]).name, "gemini")
            with self.assertRaisesRegex(ValueError, "Chinese video"):
                automation._build_automation_job(
                    workspace_id,
                    uuid.uuid4().hex,
                    {
                        "workflow": "video_edit_sync",
                        "videoFile": "source.txt",
                        "audioFile": "hindi.wav",
                        "transcriptFile": "hindi.txt",
                    },
                )
        finally:
            shutil.rmtree(automation._workspace_output_root(workspace_id), ignore_errors=True)

    def test_automation_routes_are_registered_with_cutroom(self):
        routes = {rule.rule for rule in server.app.url_map.iter_rules()}
        self.assertIn("/api/automation", routes)
        self.assertIn("/api/automation/jobs", routes)
        self.assertIn("/api/automation/jobs/<job_id>/files/<path:relative_path>", routes)
        response = server.app.test_client().get("/automation.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("showAutomation", response.get_data(as_text=True))
        self.assertIn("Process logs:", response.get_data(as_text=True))
        index_response = server.app.test_client().get("/")
        self.assertEqual(index_response.status_code, 200)
        self.assertIn(
            "VIDEO EDIT AUTOMATION — sync Hindi dubbing",
            index_response.get_data(as_text=True),
        )
        index_html = index_response.get_data(as_text=True)
        self.assertIn('id="videoEditVideoFile" type="file"', index_html)
        self.assertIn('id="videoEditAudioFile" type="file"', index_html)
        self.assertIn('id="videoEditTranscriptFile" type="file"', index_html)
        self.assertNotIn('id="scanVideoEditFolder"', index_html)


class AutomationCredentialApiTests(unittest.TestCase):
    def setUp(self):
        self.original_auth_rate_limits = server.AUTH_RATE_LIMITS
        server.AUTH_RATE_LIMITS = {
            path: (1000, seconds, dimensions)
            for path, (_, seconds, dimensions) in self.original_auth_rate_limits.items()
        }
        self.client = server.app.test_client()
        self.email = f"{uuid.uuid4().hex}@example.test"
        signup = self.client.post(
            "/api/auth/register",
            json={
                "email": self.email,
                "password": "local-test-password-123",
                "displayName": "Automation Test",
            },
        )
        self.assertEqual(signup.status_code, 201, signup.json)
        self.workspace_id = signup.json["workspace"]["id"]

    def tearDown(self):
        if hasattr(self, "workspace_id"):
            with server.db() as con:
                con.execute("DELETE FROM automation_jobs WHERE workspace_id=?", (self.workspace_id,))
                con.execute("DELETE FROM workspace_automation_secrets WHERE workspace_id=?", (self.workspace_id,))
                con.execute("DELETE FROM workspace_automation_settings WHERE workspace_id=?", (self.workspace_id,))
                con.execute("DELETE FROM memberships WHERE workspace_id=?", (self.workspace_id,))
                con.execute("DELETE FROM workspaces WHERE id=?", (self.workspace_id,))
                con.execute("DELETE FROM users WHERE email=?", (self.email,))
            shutil.rmtree(automation._workspace_output_root(self.workspace_id), ignore_errors=True)
        server.AUTH_RATE_LIMITS = self.original_auth_rate_limits

    def test_credentials_are_encrypted_and_workspace_scoped(self):
        api_key = "test-kuaishou-backend-key"
        cartesia_key = "test-cartesia-secret-key-123456"
        encryption_key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {"CUTROOM_SECRET_ENCRYPTION_KEY": encryption_key}):
            csrf_token = self.client.get("/api/csrf").json["csrfToken"]
            response = self.client.put(
                "/api/automation/settings",
                headers={"X-CSRF-Token": csrf_token},
                json={"apiUrl": "https://api.example.test", "apiKey": api_key},
            )
            self.assertEqual(response.status_code, 200, response.json)
            response = self.client.put(
                "/api/automation/settings",
                headers={"X-CSRF-Token": csrf_token},
                json={"cartesiaApiKey": cartesia_key},
            )
            self.assertEqual(response.status_code, 200, response.json)
            with server.db() as con:
                row = con.execute(
                    "SELECT ciphertext FROM workspace_automation_secrets WHERE workspace_id=?",
                    (self.workspace_id,),
                ).fetchone()
            self.assertNotIn(cartesia_key, row["ciphertext"])
            self.assertNotIn(api_key, row["ciphertext"])
            self.assertEqual(
                automation._read_workspace_secrets(self.workspace_id),
                {"apiKey": api_key, "cartesiaApiKey": cartesia_key},
            )
            self.assertEqual(automation._read_workspace_api_url(self.workspace_id), "https://api.example.test")
            status = self.client.get("/api/automation")
            self.assertTrue(status.json["credentials"]["kuaishouConfigured"])
            self.assertTrue(status.json["credentials"]["cartesiaConfigured"])

    def test_video_edit_job_accepts_three_direct_file_uploads(self):
        csrf_token = self.client.get("/api/csrf").json["csrfToken"]
        captured = {}

        def start_job(*args):
            captured["args"] = args

        with patch.object(automation, "_run_automation_process", side_effect=start_job):
            response = self.client.post(
                "/api/automation/jobs",
                headers={"X-CSRF-Token": csrf_token},
                data={
                    "workflow": "video_edit_sync",
                    "timeoutMinutes": "30",
                    "video": (io.BytesIO(b"video bytes"), "chinese.mp4"),
                    "audio": (io.BytesIO(b"audio bytes"), "hindi.wav"),
                    "transcript": (
                        io.BytesIO("[00:00:00] नमस्ते".encode("utf-8")),
                        "hindi.txt",
                    ),
                },
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 202, response.json)
        args = captured["args"]
        working = args[3]
        self.assertEqual((working / "source_video.mp4").read_bytes(), b"video bytes")
        self.assertEqual((working / "hindi_audio.wav").read_bytes(), b"audio bytes")
        self.assertEqual(
            (working / "audio_transcript.txt").read_text(encoding="utf-8"),
            "[00:00:00] नमस्ते",
        )

    def test_video_edit_job_requires_all_three_direct_uploads(self):
        csrf_token = self.client.get("/api/csrf").json["csrfToken"]
        response = self.client.post(
            "/api/automation/jobs",
            headers={"X-CSRF-Token": csrf_token},
            data={
                "workflow": "video_edit_sync",
                "timeoutMinutes": "30",
                "video": (io.BytesIO(b"video"), "source.mp4"),
                "audio": (io.BytesIO(b"audio"), "hindi.wav"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400, response.json)
        self.assertIn("Upload the video, audio", response.json["error"])

    def test_kuaishou_url_saves_independently_but_key_is_required_to_run(self):
        csrf_token = self.client.get("/api/csrf").json["csrfToken"]
        with patch.dict(os.environ, {"CUTROOM_SECRET_ENCRYPTION_KEY": ""}):
            response = self.client.put(
                "/api/automation/settings",
                headers={"X-CSRF-Token": csrf_token},
                json={"apiUrl": "https://api.example.test/functions/v1/fetch-video"},
            )
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["kuaishouUrlConfigured"])
        self.assertFalse(response.json["kuaishouConfigured"])
        self.assertEqual(automation._read_workspace_api_url(self.workspace_id), "https://api.example.test/functions/v1/fetch-video")
        status = self.client.get("/api/automation")
        self.assertEqual(status.json["credentials"]["apiUrl"], "https://api.example.test/functions/v1/fetch-video")
        self.assertFalse(status.json["credentials"]["kuaishouKeyConfigured"])

    def test_kuaishou_job_uses_saved_url_and_optional_target_folder(self):
        csrf_token = self.client.get("/api/csrf").json["csrfToken"]
        api_key = "test-kuaishou-api-key"
        encryption_key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {"CUTROOM_SECRET_ENCRYPTION_KEY": encryption_key}):
            saved = self.client.put(
                "/api/automation/settings",
                headers={"X-CSRF-Token": csrf_token},
                json={"apiUrl": "https://api.example.test/functions/v1/fetch-video", "apiKey": api_key},
            )
        self.assertEqual(saved.status_code, 200, saved.json)
        home = Path(automation._automation_home())
        self.assertTrue((home / "youtube_automation").is_dir())
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory)
            job_id = uuid.uuid4().hex
            with (
                patch.object(automation, "_automation_home", return_value=home),
                patch.dict(os.environ, {"CUTROOM_SECRET_ENCRYPTION_KEY": encryption_key}),
            ):
                command, working, output, env = automation._build_automation_job(
                    self.workspace_id,
                    job_id,
                    {
                        "workflow": "kuaishou",
                        "urls": "https://www.kuaishou.com/short-video/test",
                        "mode": 2,
                        "videoTitle": "Sample Video",
                        "startPart": 108,
                        "targetFolder": str(target),
                    },
                )
            try:
                self.assertEqual(command[-2:], ["--mode", "2"])
                self.assertEqual(env["API_URL"], "https://api.example.test/functions/v1/fetch-video")
                self.assertEqual(env["API_KEY"], api_key)
                self.assertEqual(env["VIDEO_TITLE"], "Sample Video")
                self.assertEqual(env["START_PART"], "108")
                self.assertEqual(output, target)
                self.assertEqual(env["VIDEO_FOLDER"], str(output / "videos"))
                self.assertEqual(env["AUDIO_FOLDER"], str(output / "audio"))
                self.assertEqual((working / "links.txt").read_text(encoding="utf-8").strip(), "https://www.kuaishou.com/short-video/test")
            finally:
                shutil.rmtree(working, ignore_errors=True)

    def test_youtube_batch_job_passes_language_and_output_naming_options(self):
        job_id = uuid.uuid4().hex
        with (
            patch.object(automation, "_automation_interpreter", return_value=Path(sys.executable)),
            patch.object(
                automation,
                "_read_workspace_secrets",
                return_value={"cartesiaApiKey": "test-cartesia-api-key"},
            ),
        ):
            command, working, output, env = automation._build_automation_job(
                self.workspace_id,
                job_id,
                {
                    "workflow": "youtube_batch",
                    "urls": "https://youtu.be/abcdefghijk",
                    "language": "hi",
                    "namePrefix": "Episode 1",
                    "startNumber": 42,
                    "voiceCharacter": "other",
                    "voiceId": "  custom-cartesia-voice  ",
                },
            )
        try:
            self.assertEqual(
                command[-8:],
                [
                    "--transcript-language",
                    "hi",
                    "--start-number",
                    "42",
                    "--name-prefix",
                    "Episode_1",
                    "--steps",
                    "download,transcript,english,tts,timestamped_transcript",
                ],
            )
            self.assertEqual(env["CARTESIA_API_KEY"], "test-cartesia-api-key")
            self.assertEqual(env["CARTESIA_ENGLISH_VOICE_ID"], "custom-cartesia-voice")
            self.assertEqual(
                (working / "youtube-links.txt").read_text(encoding="utf-8"),
                "https://youtu.be/abcdefghijk\n",
            )
            self.assertTrue(output.is_dir())
        finally:
            shutil.rmtree(working, ignore_errors=True)
            shutil.rmtree(output, ignore_errors=True)

    def test_youtube_batch_job_keeps_configured_default_voice_when_no_override(self):
        job_id = uuid.uuid4().hex
        with (
            patch.object(automation, "_automation_interpreter", return_value=Path(sys.executable)),
            patch.object(
                automation,
                "_read_workspace_secrets",
                return_value={"cartesiaApiKey": "test-cartesia-api-key"},
            ),
            patch.dict(os.environ, {"CARTESIA_ENGLISH_VOICE_ID": "configured-default"}),
        ):
            command, working, output, env = automation._build_automation_job(
                self.workspace_id,
                job_id,
                {
                    "workflow": "youtube_batch",
                    "urls": "https://youtu.be/abcdefghijk",
                    "voiceCharacter": "",
                },
            )
        try:
            self.assertEqual(env["CARTESIA_ENGLISH_VOICE_ID"], "configured-default")
        finally:
            shutil.rmtree(working, ignore_errors=True)
            shutil.rmtree(output, ignore_errors=True)

    def test_youtube_batch_job_maps_named_voice_to_cartesia_id(self):
        expected_voices = {
            "kabir": "cb9c954d-bcaa-43ed-82bf-aeb5e88a3cb5",
            "rohan": "4877b818-c7fe-4c89-b1cf-eadf8e23da72",
            "denil": "47c38ca4-5f35-497b-b1a3-415245fb35e1",
            "jacqueline_female": "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc",
        }
        for character, expected_voice_id in expected_voices.items():
            with self.subTest(character=character):
                job_id = uuid.uuid4().hex
                with (
                    patch.object(automation, "_automation_interpreter", return_value=Path(sys.executable)),
                    patch.object(
                        automation,
                        "_read_workspace_secrets",
                        return_value={"cartesiaApiKey": "test-cartesia-api-key"},
                    ),
                ):
                    command, working, output, env = automation._build_automation_job(
                        self.workspace_id,
                        job_id,
                        {
                            "workflow": "youtube_batch",
                            "urls": "https://youtu.be/abcdefghijk",
                            "voiceCharacter": character,
                        },
                    )
                try:
                    self.assertEqual(
                        env["CARTESIA_ENGLISH_VOICE_ID"],
                        expected_voice_id,
                    )
                finally:
                    shutil.rmtree(working, ignore_errors=True)
                    shutil.rmtree(output, ignore_errors=True)

    def test_youtube_batch_job_rejects_invalid_voice_selection(self):
        job_id = uuid.uuid4().hex
        with (
            patch.object(automation, "_automation_interpreter", return_value=Path(sys.executable)),
            patch.object(
                automation,
                "_read_workspace_secrets",
                return_value={"cartesiaApiKey": "test-cartesia-api-key"},
            ),
        ):
            with self.assertRaisesRegex(ValueError, "valid Cartesia character voice"):
                automation._build_automation_job(
                    self.workspace_id,
                    job_id,
                    {
                        "workflow": "youtube_batch",
                        "urls": "https://youtu.be/abcdefghijk",
                        "voiceCharacter": "not-a-character",
                    },
                )
        shutil.rmtree(
            automation._workspace_output_root(self.workspace_id) / "jobs" / job_id,
            ignore_errors=True,
        )
        shutil.rmtree(
            automation._workspace_output_root(self.workspace_id) / "outputs" / job_id,
            ignore_errors=True,
        )

    def test_youtube_batch_job_requires_custom_id_for_other_voice(self):
        job_id = uuid.uuid4().hex
        with (
            patch.object(automation, "_automation_interpreter", return_value=Path(sys.executable)),
            patch.object(
                automation,
                "_read_workspace_secrets",
                return_value={"cartesiaApiKey": "test-cartesia-api-key"},
            ),
        ):
            with self.assertRaisesRegex(ValueError, "Enter a Cartesia voice ID"):
                automation._build_automation_job(
                    self.workspace_id,
                    job_id,
                    {
                        "workflow": "youtube_batch",
                        "urls": "https://youtu.be/abcdefghijk",
                        "voiceCharacter": "other",
                        "voiceId": " ",
                    },
                )
        shutil.rmtree(
            automation._workspace_output_root(self.workspace_id) / "jobs" / job_id,
            ignore_errors=True,
        )
        shutil.rmtree(
            automation._workspace_output_root(self.workspace_id) / "outputs" / job_id,
            ignore_errors=True,
        )

    def test_youtube_batch_job_uses_custom_output_folder(self):
        job_id = uuid.uuid4().hex
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory).resolve()
            with (
                patch.object(automation, "_automation_interpreter", return_value=Path(sys.executable)),
                patch.object(
                    automation,
                    "_read_workspace_secrets",
                    return_value={"cartesiaApiKey": "test-cartesia-api-key"},
                ),
            ):
                command, working, output, _ = automation._build_automation_job(
                    self.workspace_id,
                    job_id,
                    {
                        "workflow": "youtube_batch",
                        "urls": "https://youtu.be/abcdefghijk",
                        "targetFolder": str(target),
                    },
                )
            try:
                self.assertEqual(output, target)
                output_index = command.index("--output-dir")
                self.assertEqual(Path(command[output_index + 1]), target)
                self.assertEqual(command[-2:], ["--name-prefix", "YouTube"])
                self.assertEqual(
                    (working / "youtube-links.txt").read_text(encoding="utf-8"),
                    "https://youtu.be/abcdefghijk\n",
                )
            finally:
                shutil.rmtree(working, ignore_errors=True)
                shutil.rmtree(
                    automation._workspace_output_root(self.workspace_id) / "outputs" / job_id,
                    ignore_errors=True,
                )

    def test_youtube_batch_requires_saved_cartesia_key(self):
        job_id = uuid.uuid4().hex
        with patch.object(
            automation,
            "_read_workspace_secrets",
            return_value={},
        ):
            with self.assertRaisesRegex(ValueError, "Save a Cartesia API key"):
                automation._build_automation_job(
                    self.workspace_id,
                    job_id,
                    {
                        "workflow": "youtube_batch",
                        "urls": "https://youtu.be/abcdefghijk",
                    },
                )
        shutil.rmtree(
            automation._workspace_output_root(self.workspace_id) / "jobs" / job_id,
            ignore_errors=True,
        )
        shutil.rmtree(
            automation._workspace_output_root(self.workspace_id) / "outputs" / job_id,
            ignore_errors=True,
        )

    def test_youtube_batch_transcript_only_does_not_require_cartesia_key(self):
        job_id = uuid.uuid4().hex
        with (
            patch.object(automation, "_automation_interpreter", return_value=Path(sys.executable)),
            patch.object(automation, "_read_workspace_secrets", return_value={}),
        ):
            command, working, output, env = automation._build_automation_job(
                self.workspace_id,
                job_id,
                {
                    "workflow": "youtube_batch",
                    "urls": "https://youtu.be/abcdefghijk",
                    "steps": ["transcript"],
                    "voiceCharacter": "other",
                },
            )
        try:
            self.assertEqual(command[-2:], ["--steps", "transcript"])
            self.assertNotIn("CARTESIA_API_KEY", env)
        finally:
            shutil.rmtree(working, ignore_errors=True)
            shutil.rmtree(output, ignore_errors=True)

    def test_youtube_custom_output_manifest_limits_listed_files(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory).resolve()
            video = output / "videos" / "Episode1042.mp4"
            transcript = output / "transcripts" / "Episode1042.txt"
            audio = output / "audio" / "Episode1042_english.wav"
            unrelated = output / "private.txt"
            video.parent.mkdir()
            transcript.parent.mkdir()
            video.write_bytes(b"video")
            transcript.write_text("caption", encoding="utf-8")
            audio.parent.mkdir()
            audio.write_bytes(b"wav audio")
            unrelated.write_text("do not expose", encoding="utf-8")
            job_id = uuid.uuid4().hex
            payload = {
                "workflow": "youtube_batch",
                "targetFolder": str(output),
                "urls": "https://youtu.be/abcdefghijk",
                "namePrefix": "Episode",
                "startNumber": 1042,
            }
            with server.db() as con:
                con.execute(
                    "INSERT INTO automation_jobs(id,workspace_id,user_id,workflow,status,stage,progress,payload_json,logs,output_path,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        job_id,
                        self.workspace_id,
                        self.client.get("/api/auth/me").json["user"]["id"],
                        "youtube_batch",
                        "Succeeded",
                        "Completed",
                        100,
                        json.dumps(payload),
                        "",
                        str(
                            automation._workspace_output_root(self.workspace_id)
                            / "outputs"
                            / job_id
                        ),
                        server.now(),
                        server.now(),
                    ),
                )
            try:
                automation._record_youtube_output_manifest(job_id, self.workspace_id)
                listing = self.client.get(f"/api/automation/jobs/{job_id}/files")
                self.assertEqual(listing.status_code, 200, listing.json)
                self.assertEqual(
                    [item["path"] for item in listing.json["files"]],
                    [
                        "audio/Episode1042_english.wav",
                        "transcripts/Episode1042.txt",
                        "videos/Episode1042.mp4",
                    ],
                )
            finally:
                with server.db() as con:
                    con.execute("DELETE FROM automation_jobs WHERE id=?", (job_id,))

    def test_kuaishou_transcription_uses_browser_and_ignores_gemini_api_key(self):
        home = Path(automation._automation_home())
        job_id = uuid.uuid4().hex
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_root = Path(temporary_directory)
            with (
                patch.object(automation, "_automation_home", return_value=home),
                patch.object(automation, "_workspace_output_root", return_value=output_root),
                patch.dict(os.environ, {"GEMINI_API_KEY": "inherited-key-must-not-be-used"}),
                patch.object(automation, "_read_workspace_api_url", return_value="https://api.example.test/fetch"),
                patch.object(automation, "_read_workspace_secrets", return_value={"apiKey": "test-kuaishou-api-key"}),
            ):
                command, working, output, env = automation._build_automation_job(
                    self.workspace_id,
                    job_id,
                    {
                        "workflow": "kuaishou",
                        "urls": "https://www.kuaishou.com/short-video/test",
                        "mode": 3,
                    },
                )
            try:
                self.assertEqual(command[0], str(automation._automation_interpreter(home)))
                self.assertEqual(command[-2:], ["--mode", "3"])
                self.assertNotIn("GEMINI_API_KEY", env)
                self.assertEqual(
                    env["PLAYWRIGHT_PROFILE_DIR"],
                    str(home / "playwright_profile"),
                )
                self.assertEqual(
                    env["GEMINI_FALLBACK_PROFILE_DIR"],
                    str(output_root / "profiles" / "gemini"),
                )
                self.assertEqual(
                    env["CHATGPT_PROFILE_DIR"],
                    str(home / "playwright_profile"),
                )
                self.assertEqual(
                    env["CHATGPT_FALLBACK_PROFILE_DIR"],
                    str(output_root / "profiles" / "chatgpt"),
                )
            finally:
                shutil.rmtree(working, ignore_errors=True)
                shutil.rmtree(output, ignore_errors=True)

    def test_kuaishou_gemini_browser_mode_does_not_require_gemini_api_key(self):
        home = Path(automation._automation_home())
        with tempfile.TemporaryDirectory() as temporary_directory:
            with (
                patch.object(automation, "_automation_home", return_value=home),
                patch.object(
                    automation,
                    "_workspace_output_root",
                    return_value=Path(temporary_directory),
                ),
                patch.object(automation, "_read_workspace_api_url", return_value="https://api.example.test/fetch"),
                patch.object(automation, "_read_workspace_secrets", return_value={"apiKey": "test-kuaishou-api-key"}),
            ):
                command, working, output, env = automation._build_automation_job(
                    self.workspace_id,
                    uuid.uuid4().hex,
                    {
                        "workflow": "kuaishou",
                        "urls": "https://www.kuaishou.com/short-video/test",
                        "mode": 3,
                    },
                )
            try:
                self.assertEqual(command[-2:], ["--mode", "3"])
                self.assertNotIn("GEMINI_API_KEY", env)
            finally:
                shutil.rmtree(working, ignore_errors=True)
                shutil.rmtree(output, ignore_errors=True)

    def test_custom_kuaishou_output_is_downloadable_without_exposing_other_files(self):
        job_id = uuid.uuid4().hex
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory)
            output = target
            video = output / "videos" / "PART-61.mp4"
            video.parent.mkdir()
            video.write_bytes(b"test-video")
            audio = output / "audio" / "PART-61.m4a"
            audio.parent.mkdir()
            audio.write_bytes(b"test-audio")
            (target / "private.txt").write_text("must not be listed", encoding="utf-8")
            payload = json.dumps({
                "workflow": "kuaishou",
                "targetFolder": str(target),
                "outputFiles": ["videos/PART-61.mp4", "audio/PART-61.m4a"],
            })
            with server.db() as con:
                con.execute(
                    "INSERT INTO automation_jobs(id,workspace_id,user_id,workflow,status,stage,progress,payload_json,logs,output_path,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        job_id,
                        self.workspace_id,
                        self.client.get("/api/auth/me").json["user"]["id"],
                        "kuaishou",
                        "Succeeded",
                        "Completed",
                        100,
                        payload,
                        "",
                        str(output),
                        server.now(),
                        server.now(),
                    ),
                )
            listing = self.client.get(f"/api/automation/jobs/{job_id}/files")
            self.assertEqual(listing.status_code, 200, listing.json)
            self.assertEqual(
                [item["path"] for item in listing.json["files"]],
                ["audio/PART-61.m4a", "videos/PART-61.mp4"],
            )
            download = self.client.get(
                next(item["download"] for item in listing.json["files"] if item["path"] == "videos/PART-61.mp4")
            )
            self.assertEqual(download.status_code, 200)
            try:
                self.assertEqual(download.get_data(), b"test-video")
            finally:
                download.close()

    def test_kuaishou_output_manifest_uses_video_title_and_starting_part(self):
        job_id = uuid.uuid4().hex
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            video = output / "videos" / "PART-108_Sample_Video.mp4"
            video.parent.mkdir()
            video.write_bytes(b"test-video")
            audio = output / "audio" / "PART-108_Sample_Video.m4a"
            audio.parent.mkdir()
            audio.write_bytes(b"test-audio")
            payload = {
                "workflow": "kuaishou",
                "targetFolder": str(output),
                "urls": "https://www.kuaishou.com/short-video/test",
                "mode": 2,
                "startPart": 108,
                "videoTitle": "Sample Video",
            }
            with server.db() as con:
                con.execute(
                    "INSERT INTO automation_jobs(id,workspace_id,user_id,workflow,status,stage,progress,payload_json,logs,output_path,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        job_id,
                        self.workspace_id,
                        self.client.get("/api/auth/me").json["user"]["id"],
                        "kuaishou",
                        "Succeeded",
                        "Completed",
                        100,
                        json.dumps(payload),
                        "",
                        str(output),
                        server.now(),
                        server.now(),
                    ),
                )

            automation._record_kuaishou_output_manifest(
                job_id,
                self.workspace_id,
                output,
            )

            with server.db() as con:
                saved = con.execute(
                    "SELECT payload_json FROM automation_jobs WHERE id=?",
                    (job_id,),
                ).fetchone()
            self.assertEqual(
                json.loads(saved["payload_json"])["outputFiles"],
                ["videos/PART-108_Sample_Video.mp4", "audio/PART-108_Sample_Video.m4a"],
            )

    def test_sibling_downloader_sends_kuaishou_api_key_headers(self):
        home = Path(automation._automation_home())
        sys.path.insert(0, str(home))
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            env = {
                "PYTHON_DOTENV_DISABLED": "1",
                "API_URL": "https://api.example.test/functions/v1/fetch-video",
                "API_KEY": "test-kuaishou-api-key",
                "VIDEO_FOLDER": str(root / "videos"),
                "AUDIO_FOLDER": str(root / "audio"),
            }
            with patch.dict(os.environ, env, clear=False):
                config = importlib.import_module("youtube_automation.config")
                importlib.reload(config)
                module = types.ModuleType("youtube_automation._test_downloader")
                module.__package__ = "youtube_automation"
                source = (home / "youtube_automation" / "downloader.py").read_text(encoding="utf-8")
                exec(compile(source, str(home / "youtube_automation" / "downloader.py"), "exec"), module.__dict__)
                fake_session = Mock()
                fake_session.headers = {}
                response = Mock()
                response.json.return_value = {
                    "success": True,
                    "data": {"videoUrl": "https://cdn.example.test/video.mp4", "audioUrl": "https://cdn.example.test/audio.m4a"},
                }
                fake_session.post.return_value = response
                with patch.object(module.requests, "Session", return_value=fake_session):
                    result = module.fetch_video_data("https://v.kuaishou.com/test")
            self.assertEqual(
                fake_session.headers,
                {
                    "Content-Type": "application/json",
                    "apikey": "test-kuaishou-api-key",
                    "Authorization": "Bearer test-kuaishou-api-key",
                },
            )
            self.assertEqual(
                fake_session.post.call_args.args,
                ("https://api.example.test/functions/v1/fetch-video",),
            )
            self.assertEqual(
                fake_session.post.call_args.kwargs,
                {
                    "json": {"url": "https://v.kuaishou.com/test"},
                    "timeout": 60,
                },
            )
            self.assertEqual(result["videoUrl"], "https://cdn.example.test/video.mp4")

    def test_authorized_workspace_member_can_queue_local_automation_job(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            working = Path(temporary_directory) / "working"
            output = Path(temporary_directory) / "custom-output"
            csrf_token = self.client.get("/api/csrf").json["csrfToken"]
            with (
                patch.object(server, "APP_MODE", "combined"),
                patch.object(
                    automation,
                    "_build_automation_job",
                    return_value=(["test-command"], working, output, {}),
                ),
                patch.object(automation, "_run_automation_process") as run_process,
            ):
                response = self.client.post(
                    "/api/automation/jobs",
                    headers={"X-CSRF-Token": csrf_token},
                    json={"workflow": "whisper", "timeoutMinutes": 1},
                )
                self.assertEqual(response.status_code, 202, response.json)
                job_id = response.json["jobId"]
                thread = automation._threads[job_id]
                thread.join(timeout=5)
                automation._threads.pop(job_id, None)
            self.assertEqual(run_process.call_args.args[4], output)
            job = self.client.get(f"/api/automation/jobs/{job_id}")
            self.assertEqual(job.status_code, 200, job.json)
            self.assertEqual(job.json["job"]["status"], "Queued")


if __name__ == "__main__":
    unittest.main()
