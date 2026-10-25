import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from local_agent.client import AgentApi, AgentApiError, pair_agent
from local_agent.credentials import CredentialStore
from local_agent.runner import AgentRunner
from local_agent.video_sync import VideoProcessingCancelled


class LocalAgentTests(unittest.TestCase):
    def test_pairing_retries_network_errors_then_saves_token(self):
        calls = {"status": 0}
        starts = {"count": 0}

        def transport(method, path, body, token):
            if path == "/api/agent/pair/start":
                starts["count"] += 1
                if starts["count"] == 1:
                    raise ConnectionError("pair response temporarily lost")
                return {"pairCode": "0123456789ABCDEF"}
            calls["status"] += 1
            if calls["status"] == 1:
                raise ConnectionError("temporary offline")
            self.assertEqual(token, "test-pair-token")
            return {"status": "Paired"}

        saved = []
        api = AgentApi(
            "https://cutroom7.netlify.app",
            transport=transport,
        )
        result = pair_agent(
            api,
            "Test PC",
            saved.append,
            wait=lambda _: None,
            token_factory=lambda: "test-pair-token",
            timeout=10,
        )

        self.assertEqual(result, "test-pair-token")
        self.assertEqual(saved, ["test-pair-token"])
        self.assertEqual(starts["count"], 2)
        self.assertEqual(calls["status"], 2)

    def test_runner_recovers_after_network_loss_and_completes_job(self):
        class FakeApi:
            def __init__(self):
                self.claims = 0
                self.updates = []

            def claim(self):
                self.claims += 1
                if self.claims == 1:
                    raise ConnectionError("temporary offline")
                if self.claims == 2:
                    return {
                        "job": {
                            "id": "0123456789abcdef0123456789abcdef",
                            "payload": {"jobType": "agent_self_test", "message": "test"},
                        }
                    }
                return {"job": None}

            def update(self, job_id, **values):
                self.updates.append((job_id, values))
                return {"ok": True}

            def heartbeat(self, job_id=None):
                return {"ok": True, "cancelRequested": False}

        waits = {"count": 0}

        def stop_after_completion(_):
            waits["count"] += 1
            if waits["count"] == 2:
                raise StopIteration

        api = FakeApi()
        runner = AgentRunner(api, wait=stop_after_completion, heartbeat_interval=60)
        with self.assertRaises(StopIteration):
            runner.run_forever()

        statuses = [values.get("status") for _, values in api.updates]
        self.assertEqual(statuses, ["Running", "Succeeded"])
        self.assertEqual(api.claims, 3)

    def test_runner_stops_on_revoked_credentials(self):
        class UnauthorizedApi:
            def claim(self):
                raise AgentApiError(401, "revoked")

        runner = AgentRunner(UnauthorizedApi(), wait=lambda _: None)
        with self.assertRaisesRegex(RuntimeError, "rejected or revoked"):
            runner.run_forever()

    def test_runner_reports_video_progress_and_local_export(self):
        class FakeApi:
            def __init__(self):
                self.updates = []

            def claim(self):
                return {
                    "job": {
                        "id": "0123456789abcdef0123456789abcdef",
                        "attempt": 1,
                        "payload": {
                            "jobType": "video_sync",
                            "videoRef": "1" * 32,
                            "audioRef": "2" * 32,
                            "outputRef": "3" * 32,
                        },
                    }
                }

            def update(self, job_id, **values):
                self.updates.append(values)
                return {"ok": True}

            def heartbeat(self, job_id=None):
                return {"ok": True, "cancelRequested": False}

        references = Mock()
        api = FakeApi()
        runner = AgentRunner(
            api,
            references=references,
            video_processor=lambda job_id, attempt, payload, store, cancel, progress: (
                progress(50, "Encoding local video") or Path("C:/Exports/result.mp4")
            ),
            heartbeat_interval=60,
        )

        self.assertTrue(runner.process_one())
        self.assertIn(50, [update.get("progress") for update in api.updates])
        self.assertEqual(api.updates[-1]["status"], "Succeeded")
        self.assertEqual(api.updates[-1]["outputLocation"], str(Path("C:/Exports/result.mp4")))
        references.delete_video_job.assert_called_once()

    def test_runner_retries_a_transient_completion_update_without_rerendering(self):
        class FakeApi:
            def __init__(self):
                self.claims = 0
                self.completion_attempts = 0
                self.updates = []

            def claim(self):
                self.claims += 1
                if self.claims > 1:
                    return {"job": None}
                return {
                    "job": {
                        "id": "0123456789abcdef0123456789abcdef",
                        "attempt": 1,
                        "payload": {
                            "jobType": "video_sync",
                            "videoRef": "1" * 32,
                            "audioRef": "2" * 32,
                            "outputRef": "3" * 32,
                        },
                    }
                }

            def update(self, job_id, **values):
                if values.get("status") == "Succeeded":
                    self.completion_attempts += 1
                    if self.completion_attempts == 1:
                        raise ConnectionError("temporary network loss")
                self.updates.append(values)
                return {"ok": True}

            def heartbeat(self, job_id=None):
                return {"ok": True, "cancelRequested": False}

        api = FakeApi()
        references = Mock()
        output = Path("C:/Exports/result.mp4")
        runner = AgentRunner(
            api,
            wait=lambda _: None,
            references=references,
            video_processor=lambda *_: output,
            heartbeat_interval=60,
        )

        self.assertTrue(runner.process_one())
        self.assertEqual(api.completion_attempts, 2)
        self.assertEqual(api.updates[-1]["status"], "Succeeded")
        references.delete_video_job.assert_called_once()

    def test_runner_marks_canceled_video_job_and_cleans_file_references(self):
        class FakeApi:
            def __init__(self):
                self.updates = []

            def claim(self):
                return {
                    "job": {
                        "id": "0123456789abcdef0123456789abcdef",
                        "attempt": 1,
                        "payload": {
                            "jobType": "video_sync",
                            "videoRef": "1" * 32,
                            "audioRef": "2" * 32,
                            "outputRef": "3" * 32,
                        },
                    }
                }

            def update(self, job_id, **values):
                self.updates.append(values)
                return {"ok": True}

            def heartbeat(self, job_id=None):
                return {"ok": True, "cancelRequested": True}

        references = Mock()

        def cancelled(*args):
            raise VideoProcessingCancelled("user canceled")

        api = FakeApi()
        runner = AgentRunner(
            api, references=references, video_processor=cancelled, heartbeat_interval=60
        )

        self.assertTrue(runner.process_one())
        self.assertEqual(api.updates[-1]["status"], "Canceled")
        references.delete_video_job.assert_called_once()

    def test_runner_reports_video_failure_without_reporting_success(self):
        class FakeApi:
            def __init__(self):
                self.updates = []

            def claim(self):
                return {
                    "job": {
                        "id": "0123456789abcdef0123456789abcdef",
                        "attempt": 1,
                        "payload": {
                            "jobType": "video_sync",
                            "videoRef": "1" * 32,
                            "audioRef": "2" * 32,
                            "outputRef": "3" * 32,
                        },
                    }
                }

            def update(self, job_id, **values):
                self.updates.append(values)
                return {"ok": True}

            def heartbeat(self, job_id=None):
                return {"ok": True, "cancelRequested": False}

        references = Mock()
        api = FakeApi()
        runner = AgentRunner(
            api,
            references=references,
            video_processor=lambda *_: (_ for _ in ()).throw(RuntimeError("FFmpeg failed")),
            heartbeat_interval=60,
        )

        self.assertTrue(runner.process_one())
        self.assertEqual(api.updates[-1]["status"], "Failed")
        self.assertIn("Check the agent window", api.updates[-1]["error"])
        self.assertNotIn("Succeeded", [update["status"] for update in api.updates])
        references.delete_video_job.assert_called_once()

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI is only available on Windows.")
    def test_credential_file_is_dpapi_protected_and_restorable(self):
        token = "private-agent-token-not-stored-in-plaintext"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent-token.dpapi"
            store = CredentialStore(path)

            store.save(token)
            encrypted = path.read_bytes()

            self.assertNotIn(token.encode(), encrypted)
            self.assertEqual(store.load(), token)
            store.delete()
            self.assertIsNone(store.load())

    def test_api_rejects_non_https_remote_origin(self):
        with self.assertRaisesRegex(ValueError, "Use HTTPS"):
            AgentApi("http://cutroom.example.com")


if __name__ == "__main__":
    unittest.main()
