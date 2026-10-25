from __future__ import annotations

import threading
import tempfile
import time
from pathlib import Path
from typing import Callable

from local_agent.client import AgentApi, AgentApiError
from local_agent.references import LocalFileReferences
from local_agent.video_sync import VideoProcessingCancelled, process_video_sync
from local_agent.youtube_download import (
    YoutubeDownloadCancelled,
    download_youtube_source,
)


class AgentStatusUnavailable(ConnectionError):
    pass


class AgentRunner:
    def __init__(
        self,
        api: AgentApi,
        wait: Callable[[float], None] = time.sleep,
        heartbeat_interval: float = 20,
        references: LocalFileReferences | None = None,
        video_processor: Callable | None = None,
        youtube_browser: str | None = None,
        youtube_downloader: Callable = download_youtube_source,
    ):
        self.api = api
        self.wait = wait
        self.heartbeat_interval = heartbeat_interval
        self.references = references or LocalFileReferences()
        self.video_processor = video_processor or process_video_sync
        self.youtube_browser = youtube_browser
        self.youtube_downloader = youtube_downloader
        self._stop_heartbeat = threading.Event()
        self._cancel_requested = threading.Event()
        self._heartbeat_error: list[BaseException] = []

    def _heartbeat_loop(self, job_id: str) -> None:
        while not self._stop_heartbeat.wait(self.heartbeat_interval):
            try:
                result = self.api.heartbeat(job_id)
                if result.get("cancelRequested"):
                    self._cancel_requested.set()
                    return
            except AgentApiError as exc:
                if exc.status in {401, 403, 410}:
                    self._heartbeat_error.append(exc)
                    self._cancel_requested.set()
                    return
                self.wait(2)
            except (ConnectionError, TimeoutError, OSError):
                self.wait(2)

    def _required_update(self, job_id: str, **values) -> dict:
        delay = 1
        last_error = None
        for attempt in range(6):
            try:
                return self.api.update(job_id, **values)
            except AgentApiError as exc:
                if exc.status in {401, 403, 410} or (
                    exc.status < 500 and exc.status not in {408, 409, 429}
                ):
                    raise
                last_error = exc
                if exc.status == 409:
                    raise
            except (ConnectionError, TimeoutError, OSError) as exc:
                last_error = exc
            if attempt < 5:
                self.wait(delay)
                delay = min(delay * 2, 30)
        raise AgentStatusUnavailable(
            "Could not update the local job status after repeated API retries."
        ) from last_error

    def process_one(self) -> bool:
        claim = self.api.claim()
        job = claim.get("job")
        if job is None:
            return False
        if not isinstance(job, dict) or not isinstance(job.get("id"), str):
            raise RuntimeError("The Cutroom API returned an invalid claimed job.")
        job_id = job["id"]
        payload = job.get("payload")
        if not isinstance(payload, dict):
            self.api.update(job_id, status="Failed", stage="Invalid job", error="Job payload is invalid.")
            return True

        self._cancel_requested.clear()
        self._heartbeat_error.clear()
        self._stop_heartbeat.clear()
        heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            args=(job_id,),
            name="cutroom-agent-heartbeat",
            daemon=True,
        )
        heartbeat.start()
        try:
            job_type = payload.get("jobType")
            if job_type == "agent_self_test" and not (set(payload) - {"jobType", "message"}):
                self._required_update(
                    job_id, status="Running", stage="Running local self-test", progress=10
                )
                if self._cancel_requested.is_set():
                    self._required_update(
                        job_id, status="Canceled", stage="Canceled", progress=100
                    )
                    return True
                self._required_update(
                    job_id, status="Succeeded", stage="Self-test passed", progress=100
                )
            elif job_type == "video_sync" and set(payload) == {
                "jobType", "videoRef", "audioRef", "outputRef"
            }:
                self._required_update(
                    job_id,
                    status="Running",
                    stage="Checking local video files",
                    progress=2,
                )
                last_progress = {"percent": -1, "time": 0.0}

                def report_progress(percent: int, detail: str) -> None:
                    now = time.monotonic()
                    if (
                        percent < 100
                        and percent < last_progress["percent"] + 5
                        and now - last_progress["time"] < 3
                    ):
                        return
                    last_progress.update(percent=percent, time=now)
                    try:
                        self.api.update(
                            job_id,
                            status="Running",
                            stage=detail[:100],
                            progress=max(0, min(99, int(percent))),
                        )
                    except AgentApiError as exc:
                        if exc.status == 409:
                            self._cancel_requested.set()
                        elif exc.status in {401, 403, 410}:
                            self._heartbeat_error.append(exc)
                            self._cancel_requested.set()
                    except (ConnectionError, TimeoutError, OSError):
                        pass

                output_path = self.video_processor(
                    job_id,
                    job.get("attempt", 1),
                    payload,
                    self.references,
                    self._cancel_requested,
                    report_progress,
                )
                if self._cancel_requested.is_set():
                    self._required_update(
                        job_id, status="Canceled", stage="Canceled", progress=100
                    )
                    self.references.delete_video_job(payload)
                    return True
                self._required_update(
                    job_id,
                    status="Succeeded",
                    stage="Local video export complete",
                    progress=100,
                    outputLocation=str(output_path),
                )
                self.references.delete_video_job(payload)
            elif job_type == "youtube_download" and set(payload) == {
                "jobType", "projectId", "sourceUrl"
            }:
                self._required_update(
                    job_id,
                    status="Running",
                    stage="Downloading YouTube source on Windows",
                    progress=0,
                )
                last_progress = {"percent": -1, "time": 0.0}

                def report_youtube_progress(percent: int, stage: str) -> None:
                    now = time.monotonic()
                    if (
                        percent < 100
                        and percent < last_progress["percent"] + 5
                        and now - last_progress["time"] < 3
                    ):
                        return
                    last_progress.update(percent=percent, time=now)
                    try:
                        self.api.update(
                            job_id,
                            status="Running",
                            stage=stage[:100],
                            progress=max(0, min(99, int(percent))),
                        )
                    except AgentApiError as exc:
                        if exc.status == 409:
                            self._cancel_requested.set()
                        elif exc.status in {401, 403, 410}:
                            self._heartbeat_error.append(exc)
                            self._cancel_requested.set()
                    except (ConnectionError, TimeoutError, OSError):
                        pass

                with tempfile.TemporaryDirectory(prefix="cutroom_youtube_") as directory:
                    source_path = self.youtube_downloader(
                        payload["sourceUrl"],
                        Path(directory),
                        self.youtube_browser,
                        self._cancel_requested,
                        report_youtube_progress,
                    )
                    if self._cancel_requested.is_set():
                        raise YoutubeDownloadCancelled("The local YouTube download was canceled.")
                    self._required_update(
                        job_id,
                        status="Running",
                        stage="Uploading source to Cutroom",
                        progress=0,
                    )
                    last_progress.update(percent=-1,time=0.0)
                    self.api.upload_youtube_source(
                        job_id,
                        source_path,
                        lambda percent: report_youtube_progress(
                            percent, "Uploading source to Cutroom"
                        ),
                    )
                    if self._cancel_requested.is_set():
                        raise YoutubeDownloadCancelled("The local YouTube download was canceled.")
                self._required_update(
                    job_id,
                    status="Succeeded",
                    stage="YouTube source uploaded",
                    progress=100,
                    outputLocation="Source attached to the Cutroom project",
                )
            else:
                self.api.update(
                    job_id,
                    status="Failed",
                    stage="Unsupported job",
                    progress=100,
                    error="This agent build does not support the requested job type or payload.",
                )
        except Exception as exc:
            if isinstance(exc, (VideoProcessingCancelled, YoutubeDownloadCancelled)):
                self._required_update(
                    job_id, status="Canceled", stage="Canceled", progress=100
                )
                if payload.get("jobType") == "video_sync":
                    self.references.delete_video_job(payload)
            elif isinstance(exc, AgentStatusUnavailable):
                raise
            elif isinstance(exc, AgentApiError) and exc.status in {401, 403, 410}:
                raise
            elif isinstance(exc, AgentApiError) and exc.status == 409:
                try:
                    if self.api.heartbeat(job_id).get("cancelRequested"):
                        self._required_update(
                            job_id, status="Canceled", stage="Canceled", progress=100
                        )
                        if payload.get("jobType") == "video_sync":
                            self.references.delete_video_job(payload)
                        return True
                except (ConnectionError, TimeoutError, OSError):
                    pass
                self._report_failure(job_id, f"The Cutroom API rejected the job update: {exc}")
            else:
                if job_type == "video_sync":
                    print(f"Local video processing failed: {exc}")
                    message = (
                        str(exc)
                        if isinstance(exc, FileNotFoundError)
                        else "Local video processing failed. Check the agent window for details."
                    )
                elif job_type == "youtube_download":
                    message = str(exc)[:1000] or "The local YouTube download failed."
                else:
                    message = str(exc)[:1000] or exc.__class__.__name__
                failed = self._report_failure(job_id, message)
                if failed and job_type == "video_sync":
                    self.references.delete_video_job(payload)
        finally:
            self._stop_heartbeat.set()
            heartbeat.join(timeout=max(1, self.heartbeat_interval + 1))
        if self._heartbeat_error:
            raise self._heartbeat_error[0]
        return True

    def _report_failure(self, job_id: str, message: str) -> bool:
        delay = 1
        for attempt in range(6):
            try:
                self.api.update(
                    job_id,
                    status="Failed",
                    stage="Agent error",
                    progress=100,
                    error=message,
                )
                return True
            except AgentApiError as exc:
                if exc.status in {401, 403, 410}:
                    raise
                if exc.status == 409:
                    return False
            except (ConnectionError, TimeoutError, OSError):
                pass
            if attempt < 5:
                self.wait(delay)
                delay = min(delay * 2, 30)
        raise ConnectionError("Could not report the local job failure after repeated API retries.")

    def run_forever(self, poll_interval: float = 5) -> None:
        delay = 1
        while True:
            try:
                if self.process_one():
                    delay = 1
                else:
                    self.wait(poll_interval)
            except AgentApiError as exc:
                if exc.status in {401, 403, 410}:
                    raise RuntimeError(
                        "Agent credentials were rejected or revoked. Re-pair this computer."
                    ) from exc
                if exc.status < 500 and exc.status not in {408, 429}:
                    raise RuntimeError(f"The Cutroom API rejected an agent request: {exc}") from exc
                self.wait(delay)
                delay = min(delay * 2, 60)
            except (ConnectionError, TimeoutError, OSError):
                self.wait(delay)
                delay = min(delay * 2, 60)
