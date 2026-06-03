"""CORS for the desktop renderer — it's a separate web origin from this loopback
service and sends X-Laura-Token, so every call is CORS-preflighted. Regression guard
for the renderer being unable to reach the API (found via the running app's console)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.main import create_app


def _client(tmp_path: Path) -> TestClient:
    client = TestClient(create_app(Settings(workspace_root=tmp_path, start_runner=False)))
    client.__enter__()
    return client


def test_preflight_allows_vite_dev_origin(tmp_path: Path) -> None:
    client = _client(tmp_path)
    try:
        resp = client.options(
            "/projects",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-laura-token,content-type",
            },
        )
        assert resp.status_code == 200
        assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"
    finally:
        client.__exit__(None, None, None)


def test_get_includes_cors_header_for_file_origin(tmp_path: Path) -> None:
    client = _client(tmp_path)
    try:  # packaged renderer loads via file:// -> Origin "null"
        resp = client.get("/healthz", headers={"Origin": "null"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "null"
    finally:
        client.__exit__(None, None, None)


def test_unknown_origin_gets_no_cors_grant(tmp_path: Path) -> None:
    client = _client(tmp_path)
    try:  # a random web page must not be granted access (browser would block it)
        resp = client.get("/healthz", headers={"Origin": "https://evil.example.com"})
        assert resp.status_code == 200
        assert "access-control-allow-origin" not in resp.headers
    finally:
        client.__exit__(None, None, None)
