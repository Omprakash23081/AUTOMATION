"""Persistent control-plane records for paired local agents and media jobs."""
from __future__ import annotations

import os
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator


SCHEMA = """
CREATE TABLE IF NOT EXISTS local_agent_pairs (
    id TEXT PRIMARY KEY,
    pair_code_hash TEXT NOT NULL UNIQUE,
    token_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('Pending','Paired','Expired')),
    attempts INTEGER NOT NULL DEFAULT 0,
    user_id TEXT,
    workspace_id TEXT,
    agent_id TEXT,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS local_agents (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN ('Active','Revoked')),
    created_at TEXT NOT NULL,
    last_seen_at TEXT
);
CREATE INDEX IF NOT EXISTS local_agents_owner_idx
    ON local_agents(user_id, workspace_id, status);
CREATE TABLE IF NOT EXISTS local_agent_jobs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    agent_id TEXT,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('Queued','Running','Succeeded','Failed','Canceled')),
    stage TEXT NOT NULL DEFAULT 'Queued',
    progress INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    pinned_agent INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    output_location TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    heartbeat_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS local_agent_jobs_queue_idx
    ON local_agent_jobs(user_id, workspace_id, status, created_at);
"""

MAX_ACTIVE_LOCAL_AGENTS = 5


class _Connection:
    def __init__(self, connection: Any, postgres: bool):
        self._connection = connection
        self._postgres = postgres

    def execute(self, statement: str, parameters: tuple | list = ()):
        if self._postgres:
            statement = statement.replace("?", "%s")
        return self._connection.execute(statement, parameters)


class AgentStore:
    """Use PostgreSQL when configured, with an isolated SQLite store for local tests."""

    def __init__(self, database_url: str | None = None, sqlite_path: Path | None = None):
        self.database_url = database_url or os.environ.get("DATABASE_URL", "").strip()
        self.sqlite_path = sqlite_path or Path(os.environ.get("CUTROOM_DATA", "data")) / "agent-control.sqlite3"
        self._postgres = bool(self.database_url)
        self._schema_lock = threading.Lock()
        self._schema_ready = False

    @contextmanager
    def connection(self, immediate: bool = False) -> Generator[_Connection, None, None]:
        if self._postgres:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError("PostgreSQL support is missing; install backend requirements.") from exc
            dsn = self.database_url
            if dsn.startswith("postgres://"):
                dsn = "postgresql://" + dsn[len("postgres://"):]
            with psycopg.connect(dsn, row_factory=dict_row) as raw:
                yield _Connection(raw, True)
            return

        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        raw = sqlite3.connect(self.sqlite_path, timeout=30)
        raw.row_factory = sqlite3.Row
        try:
            if immediate:
                raw.execute("BEGIN IMMEDIATE")
            yield _Connection(raw, False)
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            statements = [statement.strip() for statement in SCHEMA.split(";") if statement.strip()]
            with self.connection() as con:
                for statement in statements:
                    con.execute(statement)
                if self._postgres:
                    columns = con.execute(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='local_agent_jobs' "
                        "AND table_schema=current_schema()"
                    ).fetchall()
                    has_pinned_agent = any(
                        row["column_name"] == "pinned_agent" for row in columns
                    )
                else:
                    columns = con.execute("PRAGMA table_info(local_agent_jobs)").fetchall()
                    has_pinned_agent = any(row["name"] == "pinned_agent" for row in columns)
                if not has_pinned_agent:
                    con.execute(
                        "ALTER TABLE local_agent_jobs ADD COLUMN "
                        "pinned_agent INTEGER NOT NULL DEFAULT 0"
                    )
            self._schema_ready = True

    def create_pair(self, pair: dict[str, Any]) -> None:
        self.ensure_schema()
        with self.connection(immediate=True) as con:
            con.execute("DELETE FROM local_agent_pairs WHERE expires_at<=? OR status='Expired'", (pair["created_at"],))
            existing_agent=con.execute(
                "SELECT id FROM local_agents WHERE token_hash=?",
                (pair["token_hash"],),
            ).fetchone()
            if existing_agent:
                raise RuntimeError("This agent token is already paired. Generate a new pairing token.")
            con.execute(
                "DELETE FROM local_agent_pairs WHERE token_hash=? AND status='Pending'",
                (pair["token_hash"],),
            )
            con.execute(
                "INSERT INTO local_agent_pairs(id,pair_code_hash,token_hash,name,status,expires_at,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    pair["id"], pair["pair_code_hash"], pair["token_hash"], pair["name"],
                    "Pending", pair["expires_at"], pair["created_at"],
                ),
            )

    def approve_pair(self, code_hash: str, user_id: str, workspace_id: str, agent_id: str, now: str) -> dict[str, Any] | None:
        self.ensure_schema()
        with self.connection(immediate=True) as con:
            statement = "SELECT * FROM local_agent_pairs WHERE pair_code_hash=?"
            if self._postgres:
                statement += " FOR UPDATE"
            pair = con.execute(statement, (code_hash,)).fetchone()
            if not pair or pair["status"] != "Pending" or pair["expires_at"] <= now or pair["attempts"] >= 5:
                return None
            if self._postgres:
                con.execute("SELECT pg_advisory_xact_lock(731947220)")
            active_agents = con.execute(
                "SELECT COUNT(*) AS total FROM local_agents WHERE status='Active'"
            ).fetchone()["total"]
            if active_agents >= MAX_ACTIVE_LOCAL_AGENTS:
                raise RuntimeError("The private local-agent tester limit has been reached.")
            con.execute("UPDATE local_agent_pairs SET attempts=attempts+1 WHERE id=?", (pair["id"],))
            con.execute(
                "UPDATE local_agent_pairs SET status='Paired',user_id=?,workspace_id=?,agent_id=? "
                "WHERE id=? AND status='Pending'",
                (user_id, workspace_id, agent_id, pair["id"]),
            )
            con.execute(
                "INSERT INTO local_agents(id,user_id,workspace_id,name,token_hash,status,created_at) "
                "VALUES(?,?,?,?,?,'Active',?)",
                (agent_id, user_id, workspace_id, pair["name"], pair["token_hash"], now),
            )
            approved = dict(pair)
            approved.update(
                status="Paired",
                attempts=pair["attempts"] + 1,
                user_id=user_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
            )
            return approved

    def agent_for_token(self, token_hash: str) -> dict[str, Any] | None:
        self.ensure_schema()
        with self.connection() as con:
            row = con.execute(
                "SELECT id,user_id,workspace_id,name,status,last_seen_at FROM local_agents WHERE token_hash=?",
                (token_hash,),
            ).fetchone()
        return dict(row) if row else None

    def pair_for_token(self, token_hash: str) -> dict[str, Any] | None:
        self.ensure_schema()
        with self.connection() as con:
            row = con.execute(
                "SELECT id,status,agent_id,expires_at FROM local_agent_pairs WHERE token_hash=?",
                (token_hash,),
            ).fetchone()
        return dict(row) if row else None

    def list_agents(self, user_id: str, workspace_id: str) -> list[dict[str, Any]]:
        self.ensure_schema()
        with self.connection() as con:
            rows = con.execute(
                "SELECT id,name,status,created_at,last_seen_at FROM local_agents "
                "WHERE user_id=? AND workspace_id=? ORDER BY created_at,id",
                (user_id, workspace_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def revoke_agent(self, agent_id: str, user_id: str, workspace_id: str, now: str) -> bool:
        self.ensure_schema()
        with self.connection() as con:
            result = con.execute(
                "UPDATE local_agents SET status='Revoked' WHERE id=? AND user_id=? "
                "AND workspace_id=? AND status='Active'",
                (agent_id, user_id, workspace_id),
            )
            if result.rowcount != 1:
                return False
            con.execute(
                "UPDATE local_agent_jobs SET status='Canceled',stage='Canceled',"
                "cancel_requested=1,error='The paired agent was revoked.',finished_at=?,updated_at=? "
                "WHERE agent_id=? AND status='Queued'",
                (now, now, agent_id),
            )
            con.execute(
                "UPDATE local_agent_jobs SET status='Canceled',stage='Canceled',progress=100,"
                "cancel_requested=1,error='The paired agent was revoked.',finished_at=?,updated_at=? "
                "WHERE agent_id=? AND status='Running'",
                (now, now, agent_id),
            )
        return True

    def create_job(self, job: dict[str, Any]) -> None:
        self.ensure_schema()
        with self.connection() as con:
            con.execute(
                "INSERT INTO local_agent_jobs(id,user_id,workspace_id,agent_id,pinned_agent,payload_json,status,stage,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,'Queued','Queued',?,?)",
                (
                    job["id"], job["user_id"], job["workspace_id"], job.get("agent_id"),
                    int(bool(job.get("pin_to_agent"))), job["payload_json"],
                    job["created_at"], job["created_at"],
                ),
            )

    def list_jobs(self, user_id: str, workspace_id: str) -> list[dict[str, Any]]:
        self.ensure_schema()
        with self.connection() as con:
            rows = con.execute(
                "SELECT id,status,stage,progress,attempts,max_attempts,error,output_location,"
                "created_at,started_at,finished_at,updated_at,payload_json FROM local_agent_jobs "
                "WHERE user_id=? AND workspace_id=? ORDER BY created_at DESC,id DESC LIMIT 100",
                (user_id, workspace_id),
            ).fetchall()
        jobs = []
        for row in rows:
            job = dict(row)
            try:
                payload = json.loads(job.pop("payload_json"))
            except (TypeError, json.JSONDecodeError):
                payload = {}
            job["project_id"] = payload.get("projectId")
            jobs.append(job)
        return jobs

    def get_job(self, job_id: str, user_id: str, workspace_id: str) -> dict[str, Any] | None:
        self.ensure_schema()
        with self.connection() as con:
            row = con.execute(
                "SELECT id,status,stage,progress,attempts,max_attempts,error,output_location,"
                "created_at,started_at,finished_at,updated_at FROM local_agent_jobs "
                "WHERE id=? AND user_id=? AND workspace_id=?",
                (job_id, user_id, workspace_id),
            ).fetchone()
        return dict(row) if row else None

    def runtime_job(self, job_id: str, agent_id: str) -> dict[str, Any] | None:
        self.ensure_schema()
        with self.connection() as con:
            row = con.execute(
                "SELECT id,user_id,workspace_id,payload_json,status FROM local_agent_jobs "
                "WHERE id=? AND agent_id=? AND status='Running' AND cancel_requested=0",
                (job_id, agent_id),
            ).fetchone()
        return dict(row) if row else None

    def heartbeat(self, agent_id: str, now: str) -> None:
        self.ensure_schema()
        with self.connection() as con:
            con.execute("UPDATE local_agents SET last_seen_at=? WHERE id=? AND status='Active'", (now, agent_id))

    def heartbeat_job(self, job_id: str | None, agent_id: str, now: str) -> bool:
        self.ensure_schema()
        with self.connection() as con:
            con.execute(
                "UPDATE local_agents SET last_seen_at=? WHERE id=? AND status='Active'",
                (now, agent_id),
            )
            if not job_id:
                return False
            con.execute(
                "UPDATE local_agent_jobs SET heartbeat_at=?,updated_at=? "
                "WHERE id=? AND agent_id=? AND status='Running'",
                (now, now, job_id, agent_id),
            )
        return self.cancellation_requested(job_id, agent_id)

    def claim_job(self, agent: dict[str, Any], now: str, stale_before: str) -> dict[str, Any] | None:
        self.ensure_schema()
        with self.connection(immediate=True) as con:
            con.execute(
                "UPDATE local_agent_jobs SET "
                "status=CASE WHEN cancel_requested=1 THEN 'Canceled' WHEN attempts<max_attempts THEN 'Queued' ELSE 'Failed' END,"
                "agent_id=CASE WHEN cancel_requested=1 OR pinned_agent=0 THEN NULL ELSE agent_id END,"
                "stage=CASE WHEN cancel_requested=1 THEN 'Canceled' WHEN attempts<max_attempts THEN 'Queued' ELSE 'Failed' END,"
                "error=CASE WHEN cancel_requested=1 THEN 'Canceled by the user.' "
                "WHEN attempts<max_attempts THEN NULL ELSE 'Agent disconnected after the retry limit.' END,"
                "finished_at=CASE WHEN cancel_requested=1 OR attempts>=max_attempts THEN ? ELSE NULL END,updated_at=? "
                "WHERE user_id=? AND workspace_id=? AND status='Running' AND heartbeat_at<?",
                (now, now, agent["user_id"], agent["workspace_id"], stale_before),
            )
            if self._postgres:
                row = con.execute(
                    "SELECT id,payload_json,attempts,max_attempts,cancel_requested FROM local_agent_jobs "
                    "WHERE user_id=? AND workspace_id=? AND status='Queued' AND attempts<max_attempts "
                    "AND cancel_requested=0 AND (agent_id IS NULL OR agent_id=?) "
                    "ORDER BY created_at,id LIMIT 1 FOR UPDATE SKIP LOCKED",
                    (agent["user_id"], agent["workspace_id"], agent["id"]),
                ).fetchone()
            else:
                row = con.execute(
                    "SELECT id,payload_json,attempts,max_attempts,cancel_requested FROM local_agent_jobs "
                    "WHERE user_id=? AND workspace_id=? AND status='Queued' AND attempts<max_attempts "
                    "AND cancel_requested=0 AND (agent_id IS NULL OR agent_id=?) "
                    "ORDER BY created_at,id LIMIT 1",
                    (agent["user_id"], agent["workspace_id"], agent["id"]),
                ).fetchone()
            if not row:
                return None
            con.execute(
                "UPDATE local_agent_jobs SET agent_id=?,status='Running',stage='Starting',"
                "progress=0,attempts=attempts+1,started_at=?,heartbeat_at=?,updated_at=? WHERE id=?",
                (agent["id"], now, now, now, row["id"]),
            )
            return {
                "id": row["id"],
                "payload_json": row["payload_json"],
                "attempts": row["attempts"] + 1,
                "max_attempts": row["max_attempts"],
            }

    def update_job(self, job_id: str, agent_id: str, values: dict[str, Any], now: str) -> bool:
        allowed = {"status", "stage", "progress", "error", "output_location", "finished_at"}
        if not values or set(values) - allowed:
            raise ValueError("Unsupported local-agent job update.")
        self.ensure_schema()
        assignments = [f"{key}=?" for key in values]
        parameters = [values[key] for key in values]
        assignments.extend(("heartbeat_at=?", "updated_at=?"))
        parameters.extend((now, now, job_id, agent_id))
        with self.connection() as con:
            result = con.execute(
                "UPDATE local_agent_jobs SET " + ",".join(assignments) +
                " WHERE id=? AND agent_id=? AND status='Running' AND cancel_requested=0",
                parameters,
            )
        return result.rowcount == 1

    def cancellation_requested(self, job_id: str, agent_id: str) -> bool:
        self.ensure_schema()
        with self.connection() as con:
            row = con.execute(
                "SELECT cancel_requested FROM local_agent_jobs WHERE id=? AND agent_id=?",
                (job_id, agent_id),
            ).fetchone()
        return bool(row and row["cancel_requested"])

    def complete_canceled_job(self, job_id: str, agent_id: str, now: str) -> bool:
        self.ensure_schema()
        with self.connection() as con:
            result = con.execute(
                "UPDATE local_agent_jobs SET status='Canceled',stage='Canceled',progress=100,"
                "error='Canceled by the user.',finished_at=?,heartbeat_at=?,updated_at=? "
                "WHERE id=? AND agent_id=? AND status='Running' AND cancel_requested=1",
                (now, now, now, job_id, agent_id),
            )
        return result.rowcount == 1

    def cancel_job(self, job_id: str, user_id: str, workspace_id: str, now: str) -> str | None:
        self.ensure_schema()
        with self.connection() as con:
            row = con.execute(
                "SELECT status FROM local_agent_jobs WHERE id=? AND user_id=? AND workspace_id=?",
                (job_id, user_id, workspace_id),
            ).fetchone()
            if not row:
                return None
            if row["status"] == "Queued":
                con.execute(
                    "UPDATE local_agent_jobs SET status='Canceled',stage='Canceled',cancel_requested=1,"
                    "finished_at=?,updated_at=? WHERE id=?",
                    (now, now, job_id),
                )
                return "Canceled"
            if row["status"] == "Running":
                con.execute(
                    "UPDATE local_agent_jobs SET cancel_requested=1,stage='Cancel requested',updated_at=? WHERE id=?",
                    (now, job_id),
                )
                return "Cancel requested"
            return row["status"]
