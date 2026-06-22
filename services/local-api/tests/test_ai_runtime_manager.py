from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any
from unittest.mock import patch

from laura.ai.docker_runtime import DockerAdapter
from laura.ai.runtime_manager import refresh_runtime, start_runtime, stop_runtime
from laura.ai.runtime_types import RuntimeHealth
from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, create_database


def _db(tmp_path: Path) -> Database:
    db = create_database(Settings(workspace_root=tmp_path, start_runner=False))
    db.migrate()
    return db


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true, "ready": true}')
            return
        if self.path == "/capabilities":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = {"effects": ["lipsync"], "metrics": ["sync_score"]}
            self.wfile.write(json.dumps(payload).encode())
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args: Any) -> None:  # noqa: ANN002
        return


def test_refresh_external_http_runtime_reads_health_and_capabilities(tmp_path: Path) -> None:
    db = _db(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        rt = repos.create_ai_runtime(
            db,
            kind="external_http",
            effect="lipsync",
            display_name="Fake Lipsync",
            base_url=f"http://127.0.0.1:{server.server_port}",
        )
        refreshed = refresh_runtime(db, rt["id"])
        assert refreshed["status"]["state"] == "ready"
        assert refreshed["status"]["ready"] is True
        assert refreshed["capabilities"]["effects"] == ["lipsync"]
    finally:
        server.shutdown()


class FakeDocker(DockerAdapter):
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self, runtime: dict[str, Any]) -> RuntimeHealth:
        self.started = True
        return RuntimeHealth(state="starting", ready=False, message="container started")

    def stop(self, runtime: dict[str, Any]) -> RuntimeHealth:
        self.stopped = True
        return RuntimeHealth(state="stopped", ready=False, message="container stopped")

    def logs(self, runtime: dict[str, Any], tail: int = 100) -> str:  # noqa: ARG002
        return "log line"


def test_start_and_stop_container_runtime_are_evented(tmp_path: Path) -> None:
    db = _db(tmp_path)
    docker = FakeDocker()
    rt = repos.create_ai_runtime(
        db,
        kind="container",
        effect="voice",
        display_name="Voice",
        container_image="laura-runtime-voice:local",
        container_name="laura-voice",
        port=8898,
    )

    started = start_runtime(db, rt["id"], docker=docker)
    assert docker.started is True
    assert started["status"]["state"] == "starting"

    stopped = stop_runtime(db, rt["id"], docker=docker)
    assert docker.stopped is True
    assert stopped["status"]["state"] == "stopped"

    events = repos.list_ai_runtime_events(db, rt["id"])
    assert [e["event_type"] for e in events[:2]] == ["stop", "start"]


def test_docker_adapter_handles_missing_cli_without_crashing() -> None:
    runtime = {
        "container_name": "laura-voice",
        "container_image": "laura-runtime-voice:local",
        "port": 8898,
    }
    adapter = DockerAdapter()

    with patch("laura.ai.docker_runtime.subprocess.run", side_effect=FileNotFoundError):
        started = adapter.start(runtime)
        stopped = adapter.stop(runtime)
        logs = adapter.logs(runtime)

    assert started == RuntimeHealth("error", False, "docker CLI not available")
    assert stopped == RuntimeHealth("error", False, "docker CLI not available")
    assert logs == "docker CLI not available"


def test_refresh_container_runtime_without_http_metadata_is_safe(tmp_path: Path) -> None:
    db = _db(tmp_path)
    rt = repos.create_ai_runtime(
        db,
        kind="container",
        effect="voice",
        display_name="Voice",
        container_image="laura-runtime-voice:local",
        container_name="laura-voice",
    )
    repos.update_ai_runtime_status(
        db,
        rt["id"],
        status={"state": "starting", "ready": False},
        capabilities={"effects": ["voice"]},
    )

    refreshed = refresh_runtime(db, rt["id"])

    assert refreshed["status"]["state"] == "unknown"
    assert refreshed["status"]["ready"] is False
    assert refreshed["status"]["message"] == (
        "laura-voice has no base_url or port; HTTP health is unavailable"
    )
    assert refreshed["capabilities"] == {"effects": ["voice"]}
