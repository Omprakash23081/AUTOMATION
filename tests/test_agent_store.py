import json
import tempfile
import unittest
from pathlib import Path

from backend.agent_store import AgentStore


class AgentStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = AgentStore(sqlite_path=Path(self.temp_dir.name) / "agent-control.sqlite3")
        self.timestamp = "2026-01-01T00:00:00+00:00"
        self.store.create_pair({
            "id": "pair-1",
            "pair_code_hash": "pair-hash",
            "token_hash": "token-hash",
            "name": "Test PC",
            "created_at": self.timestamp,
            "expires_at": "2026-01-01T00:05:00+00:00",
        })
        pair = self.store.approve_pair("pair-hash", "user-a", "workspace-a", "agent-a", self.timestamp)
        self.assertIsNotNone(pair)
        self.agent = self.store.agent_for_token("token-hash")

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_job(self, job_id, user_id, workspace_id):
        self.store.create_job({
            "id": job_id,
            "user_id": user_id,
            "workspace_id": workspace_id,
            "payload_json": json.dumps({"sourceType": "youtube", "sourceUrl": "https://youtu.be/abc123"}),
            "created_at": self.timestamp,
        })

    def test_agent_claims_only_jobs_for_its_user_and_workspace(self):
        self.make_job("job-owned", "user-a", "workspace-a")
        self.make_job("job-other", "user-b", "workspace-b")

        claimed = self.store.claim_job(
            self.agent, self.timestamp, "2025-12-31T23:59:00+00:00"
        )

        self.assertEqual(claimed["id"], "job-owned")
        duplicate = self.store.claim_job(
            self.agent, self.timestamp, "2025-12-31T23:59:00+00:00"
        )
        self.assertIsNone(duplicate)
        self.assertIsNone(self.store.get_job("job-owned", "user-b", "workspace-b"))

    def test_agent_submitted_jobs_are_bound_to_that_computer(self):
        with self.store.connection() as con:
            con.execute(
                "INSERT INTO local_agents(id,user_id,workspace_id,name,token_hash,status,created_at) "
                "VALUES(?,?,?,?,?,'Active',?)",
                ("agent-a2", "user-a", "workspace-a", "Second PC", "second-token", self.timestamp),
            )
        self.store.create_job({
            "id": "job-local-files",
            "user_id": "user-a",
            "workspace_id": "workspace-a",
            "agent_id": "agent-a",
            "payload_json": json.dumps({
                "jobType": "video_sync",
                "videoRef": "a" * 32,
                "audioRef": "b" * 32,
                "outputRef": "c" * 32,
            }),
            "created_at": self.timestamp,
        })
        other_agent = {**self.agent, "id": "agent-a2"}

        self.assertIsNone(
            self.store.claim_job(
                other_agent, self.timestamp, "2025-12-31T23:59:00+00:00"
            )
        )
        claimed = self.store.claim_job(
            self.agent, self.timestamp, "2025-12-31T23:59:00+00:00"
        )
        self.assertEqual(claimed["id"], "job-local-files")

    def test_pinned_video_job_recovers_only_on_the_selected_computer(self):
        with self.store.connection() as con:
            con.execute(
                "INSERT INTO local_agents(id,user_id,workspace_id,name,token_hash,status,created_at) "
                "VALUES(?,?,?,?,?,'Active',?)",
                ("agent-a2", "user-a", "workspace-a", "Second PC", "second-token", self.timestamp),
            )
        self.store.create_job({
            "id": "job-video-retry",
            "user_id": "user-a",
            "workspace_id": "workspace-a",
            "agent_id": "agent-a",
            "pin_to_agent": True,
            "payload_json": json.dumps({"jobType": "video_sync"}),
            "created_at": self.timestamp,
        })
        first = self.store.claim_job(
            self.agent, self.timestamp, "2025-12-31T23:59:00+00:00"
        )
        other_agent = {**self.agent, "id": "agent-a2"}

        self.assertIsNone(
            self.store.claim_job(
                other_agent, "2026-01-01T00:02:00+00:00", "2026-01-01T00:01:00+00:00"
            )
        )
        recovered = self.store.claim_job(
            self.agent, "2026-01-01T00:02:00+00:00", "2026-01-01T00:01:00+00:00"
        )
        self.assertEqual(first["attempts"], 1)
        self.assertEqual(recovered["id"], "job-video-retry")
        self.assertEqual(recovered["attempts"], 2)

    def test_stale_running_job_is_requeued_with_attempt_count(self):
        self.make_job("job-retry", "user-a", "workspace-a")
        first = self.store.claim_job(self.agent, self.timestamp, "2025-12-31T23:59:00+00:00")

        second = self.store.claim_job(
            self.agent, "2026-01-01T00:01:00+00:00", "2026-01-01T00:00:30+00:00"
        )

        self.assertEqual(first["attempts"], 1)
        self.assertEqual(second["id"], "job-retry")
        self.assertEqual(second["attempts"], 2)

    def test_duplicate_claim_is_blocked_and_disconnect_requeues_after_lease(self):
        self.make_job("job-single-claim", "user-a", "workspace-a")
        with self.store.connection() as con:
            con.execute(
                "INSERT INTO local_agents(id,user_id,workspace_id,name,token_hash,status,created_at) "
                "VALUES(?,?,?,?,?,'Active',?)",
                ("agent-a2", "user-a", "workspace-a", "Second PC", "second-token", self.timestamp),
            )
        first_claim = self.store.claim_job(
            self.agent, self.timestamp, "2025-12-31T23:59:00+00:00"
        )
        second_agent = {
            **self.agent,
            "id": "agent-a2",
        }
        second_claim = self.store.claim_job(
            second_agent, self.timestamp, "2025-12-31T23:59:00+00:00"
        )
        same_agent_duplicate = self.store.claim_job(
            self.agent, "2026-01-01T00:00:10+00:00", "2025-12-31T23:59:00+00:00"
        )
        recovered = self.store.claim_job(
            second_agent, "2026-01-01T00:02:00+00:00", "2026-01-01T00:01:00+00:00"
        )

        self.assertEqual(first_claim["id"], "job-single-claim")
        self.assertIsNone(second_claim)
        self.assertIsNone(same_agent_duplicate)
        self.assertEqual(recovered["id"], "job-single-claim")
        self.assertEqual(recovered["attempts"], 2)

    def test_private_tester_agent_limit_is_five(self):
        with self.store.connection() as con:
            for index in range(2, 6):
                con.execute(
                    "INSERT INTO local_agents(id,user_id,workspace_id,name,token_hash,status,created_at) "
                    "VALUES(?,?,?,?,?,'Active',?)",
                    (
                        f"agent-cap-{index}", "user-a", "workspace-a", "Tester PC",
                        f"token-cap-{index}", self.timestamp,
                    ),
                )
        self.store.create_pair({
            "id": "pair-over-cap",
            "pair_code_hash": "over-cap-hash",
            "token_hash": "over-cap-token",
            "name": "Sixth PC",
            "created_at": self.timestamp,
            "expires_at": "2026-01-01T00:05:00+00:00",
        })

        with self.assertRaisesRegex(RuntimeError, "tester limit"):
            self.store.approve_pair(
                "over-cap-hash", "user-a", "workspace-a", "agent-a5", self.timestamp
            )

    def test_cancel_marks_queued_job_terminal(self):
        self.make_job("job-cancel", "user-a", "workspace-a")

        status = self.store.cancel_job(
            "job-cancel", "user-a", "workspace-a", "2026-01-01T00:02:00+00:00"
        )

        self.assertEqual(status, "Canceled")
        self.assertIsNone(self.store.claim_job(
            self.agent, "2026-01-01T00:03:00+00:00", "2026-01-01T00:02:00+00:00"
        ))

    def test_cancelled_disconnected_job_is_not_retried(self):
        self.make_job("job-cancel-running", "user-a", "workspace-a")
        self.store.claim_job(self.agent, self.timestamp, "2025-12-31T23:59:00+00:00")
        self.assertEqual(
            self.store.cancel_job(
                "job-cancel-running", "user-a", "workspace-a", "2026-01-01T00:00:10+00:00"
            ),
            "Cancel requested",
        )

        claimed = self.store.claim_job(
            self.agent, "2026-01-01T00:02:00+00:00", "2026-01-01T00:01:00+00:00"
        )

        self.assertIsNone(claimed)
        job = self.store.get_job("job-cancel-running", "user-a", "workspace-a")
        self.assertEqual(job["status"], "Canceled")


if __name__ == "__main__":
    unittest.main()
