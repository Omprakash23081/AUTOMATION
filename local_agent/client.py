from __future__ import annotations

import json
import secrets
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class AgentApiError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class AgentApi:
    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        transport: Callable[[str, str, dict[str, Any] | None, str | None], dict[str, Any]] | None = None,
    ):
        normalized = base_url.strip().rstrip("/")
        parsed = urlparse(normalized)
        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("The API URL must be an origin without a path.")
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Use HTTPS for the Cutroom API except when connecting to localhost.")
        self.base_url = normalized
        self.token = token
        self.transport = transport or self._http_transport

    def _http_transport(self, method: str, path: str, body: dict[str, Any] | None, token: str | None) -> dict[str, Any]:
        payload = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Accept": "application/json", "User-Agent": "Cutroom-Windows-Agent/0.1"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = "Bearer " + token
        request = Request(self.base_url + path, data=payload, headers=headers, method=method)
        try:
            with urlopen(request, timeout=20) as response:
                raw = response.read()
        except HTTPError as exc:
            try:
                data = json.loads(exc.read().decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                data = {}
            raise AgentApiError(exc.code, str(data.get("error") or f"API request failed ({exc.code}).")) from exc
        except URLError as exc:
            raise ConnectionError(f"Could not reach the Cutroom API: {exc.reason}") from exc
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("The Cutroom API returned an invalid response.") from exc
        if not isinstance(data, dict):
            raise RuntimeError("The Cutroom API response has an unexpected shape.")
        return data

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            return self.transport(method, path, body, self.token)
        except AgentApiError:
            raise
        except (ConnectionError, TimeoutError, OSError):
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not complete the local-agent API request: {exc}") from exc

    def start_pairing(self, name: str, token: str) -> dict[str, Any]:
        return self.request("POST", "/api/agent/pair/start", {"name": name, "token": token})

    def pairing_status(self) -> dict[str, Any]:
        return self.request("GET", "/api/agent/pair/status")

    def claim(self) -> dict[str, Any]:
        return self.request("POST", "/api/agent/runtime/jobs/claim", {})

    def submit_video_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/api/agent/runtime/jobs", payload)

    def heartbeat(self, job_id: str | None = None) -> dict[str, Any]:
        return self.request("POST", "/api/agent/runtime/heartbeat", {"jobId": job_id})

    def update(self, job_id: str, **values: Any) -> dict[str, Any]:
        return self.request("POST", f"/api/agent/runtime/jobs/{job_id}/status", values)


def pair_agent(
    api: AgentApi,
    name: str,
    save_token: Callable[[str], None],
    wait: Callable[[float], None] = time.sleep,
    token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(48),
    timeout: float = 300,
) -> str:
    token = token_factory()
    delay = 1.0
    for attempt in range(6):
        try:
            result = api.start_pairing(name, token)
            break
        except (ConnectionError, TimeoutError, OSError):
            if attempt == 5:
                raise
            wait(delay)
            delay = min(delay * 2, 30)
    code = result.get("pairCode")
    if not isinstance(code, str) or len(code) != 16:
        raise RuntimeError("The API did not return a valid pairing code.")
    print(f"Pairing code: {code}")
    print("Enter this code in Cutroom account settings within 5 minutes.")
    api.token = token
    deadline = time.monotonic() + timeout
    delay = 2.0
    while time.monotonic() < deadline:
        try:
            status = api.pairing_status()
        except (ConnectionError, TimeoutError, OSError):
            wait(delay)
            delay = min(delay * 2, 30)
            continue
        state = status.get("status")
        if state == "Paired":
            save_token(token)
            return token
        if state == "Expired":
            raise AgentApiError(410, "The pairing code expired. Run the pair command again.")
        if state == "Revoked":
            raise AgentApiError(410, "This agent was revoked. Pair this computer again.")
        wait(2)
    raise TimeoutError("Pairing timed out. Run the pair command again.")
