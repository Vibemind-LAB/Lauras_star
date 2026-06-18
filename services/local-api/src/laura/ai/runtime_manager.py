from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..db import repos
from ..db.database import Database
from .docker_runtime import DockerAdapter
from .runtime_types import RuntimeHealth


def _get_json(url: str, timeout: float = 2.0) -> dict[str, Any]:
    request = Request(url, method="GET")
    with urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
    data = json.loads(raw or "{}")
    return data if isinstance(data, dict) else {}


def _base_url(runtime: dict[str, Any]) -> str | None:
    raw = runtime.get("base_url")
    if isinstance(raw, str) and raw:
        return raw.rstrip("/")
    port = runtime.get("port")
    if port is not None:
        return f"http://127.0.0.1:{int(port)}"
    return None


def refresh_runtime(
    db: Database,
    runtime_id: str,
    docker: DockerAdapter | None = None,
) -> dict[str, Any]:
    runtime = repos.get_ai_runtime(db, runtime_id)
    if runtime is None:
        raise ValueError(f"runtime not found: {runtime_id}")
    if runtime["kind"] == "stub":
        status = RuntimeHealth("ready", True, "stub runtime").to_json()
        capabilities = {"effects": [runtime["effect"]], "stub": True}
    else:
        base = _base_url(runtime)
        if base is None:
            status = RuntimeHealth("error", False, "runtime has no base_url or port").to_json()
            capabilities = runtime.get("capabilities", {})
        else:
            try:
                health = _get_json(f"{base}/healthz")
                caps = _get_json(f"{base}/capabilities")
                ready = bool(health.get("ready", health.get("ok", False)))
                status = RuntimeHealth(
                    "ready" if ready else "not_ready",
                    ready,
                    health.get("message") if isinstance(health.get("message"), str) else None,
                ).to_json()
                capabilities = caps
            except (
                HTTPError,
                OSError,
                TimeoutError,
                URLError,
                ValueError,
                json.JSONDecodeError,
            ) as exc:
                status = RuntimeHealth("unreachable", False, str(exc)).to_json()
                capabilities = runtime.get("capabilities", {})
    repos.update_ai_runtime_status(db, runtime_id, status=status, capabilities=capabilities)
    repos.create_ai_runtime_event(
        db,
        runtime_id=runtime_id,
        event_type="health",
        level="info" if status.get("ready") else "warning",
        message=str(status.get("message") or status.get("state")),
        payload=status,
    )
    updated = repos.get_ai_runtime(db, runtime_id)
    assert updated is not None
    return updated


def start_runtime(
    db: Database,
    runtime_id: str,
    docker: DockerAdapter | None = None,
) -> dict[str, Any]:
    runtime = repos.get_ai_runtime(db, runtime_id)
    if runtime is None:
        raise ValueError(f"runtime not found: {runtime_id}")
    if runtime["kind"] != "container":
        health = RuntimeHealth("ready", True, f"{runtime['kind']} runtime does not need start")
    else:
        health = (docker or DockerAdapter()).start(runtime)
    repos.update_ai_runtime_status(db, runtime_id, status=health.to_json())
    repos.create_ai_runtime_event(
        db,
        runtime_id=runtime_id,
        event_type="start",
        level="info" if health.state != "error" else "error",
        message=health.message or health.state,
        payload=health.to_json(),
    )
    updated = repos.get_ai_runtime(db, runtime_id)
    assert updated is not None
    return updated


def stop_runtime(
    db: Database,
    runtime_id: str,
    docker: DockerAdapter | None = None,
) -> dict[str, Any]:
    runtime = repos.get_ai_runtime(db, runtime_id)
    if runtime is None:
        raise ValueError(f"runtime not found: {runtime_id}")
    if runtime["kind"] != "container":
        health = RuntimeHealth("stopped", False, f"{runtime['kind']} runtime cannot be stopped")
    else:
        health = (docker or DockerAdapter()).stop(runtime)
    repos.update_ai_runtime_status(db, runtime_id, status=health.to_json())
    repos.create_ai_runtime_event(
        db,
        runtime_id=runtime_id,
        event_type="stop",
        level="info" if health.state != "error" else "error",
        message=health.message or health.state,
        payload=health.to_json(),
    )
    updated = repos.get_ai_runtime(db, runtime_id)
    assert updated is not None
    return updated
