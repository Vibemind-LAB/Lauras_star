"""Tests for the scene-detection sidecar adapter + the ``detect_shots`` transnet seam.

A threaded stub worker stands in for the GPU container; the in-process TransNetV2 path is
monkeypatched so no torch/weights are needed.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

import laura.analysis.shots as shots
import laura.analysis.sidecar as sidecar
from laura.analysis.types import ShotResult


class _ScenesHandler(BaseHTTPRequestHandler):
    scenes_status = 200
    scenes_body: dict[str, Any] = {"shots": []}

    def log_message(self, *args: Any) -> None:  # silence
        pass

    def _send(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        self._send(200, {"status": "ok"}) if self.path == "/healthz" else self._send(404, {})

    def do_POST(self) -> None:  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self._send(self.scenes_status, self.scenes_body)


@pytest.fixture
def scenes_worker() -> Iterator[tuple[str, type[Any]]]:
    handler = type("H", (_ScenesHandler,), {})
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", handler
    finally:
        server.shutdown()
        thread.join(timeout=2)


@pytest.fixture
def video(tmp_path: Path) -> Path:
    p = tmp_path / "v.mp4"
    p.write_bytes(b"\x00\x00fakevideo")
    return p


def test_detect_shots_via_sidecar(scenes_worker: tuple[str, type[Any]], video: Path) -> None:
    url, handler = scenes_worker
    handler.scenes_body = {
        "shots": [
            {"src_in_frame": 0, "src_out_frame_exclusive": 30, "method": "transnetv2"},
            {"src_in_frame": 30, "src_out_frame_exclusive": 75, "method": "transnetv2"},
        ]
    }
    out = sidecar.detect_shots_via_sidecar(video, url=url)
    assert all(isinstance(s, ShotResult) for s in out)
    assert [(s.src_in_frame, s.src_out_frame_exclusive) for s in out] == [(0, 30), (30, 75)]
    assert out[0].method == "transnetv2"


def test_seam_uses_sidecar_when_healthy(
    scenes_worker: tuple[str, type[Any]], video: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, handler = scenes_worker
    handler.scenes_body = {"shots": [{"src_in_frame": 0, "src_out_frame_exclusive": 12}]}
    monkeypatch.setenv("LAURA_ANALYSIS_URL", url)
    out = shots.detect_shots(video, detector="transnet")
    assert [(s.src_in_frame, s.src_out_frame_exclusive) for s in out] == [(0, 12)]


def test_seam_sidecar_error_falls_back_in_process(
    scenes_worker: tuple[str, type[Any]], video: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, handler = scenes_worker
    handler.scenes_status = 500  # /healthz still ok → sidecar chosen, then /scenes fails
    monkeypatch.setenv("LAURA_ANALYSIS_URL", url)
    sentinel = [ShotResult(src_in_frame=0, src_out_frame_exclusive=9, method="transnetv2")]
    monkeypatch.setattr("laura.analysis.transnet.detect_shots_transnet", lambda *a, **k: sentinel)
    assert shots.detect_shots(video, detector="transnet") is sentinel


def test_seam_no_sidecar_uses_in_process(video: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAURA_ANALYSIS_URL", raising=False)
    sentinel = [ShotResult(src_in_frame=0, src_out_frame_exclusive=5, method="transnetv2")]
    monkeypatch.setattr("laura.analysis.transnet.detect_shots_transnet", lambda *a, **k: sentinel)
    assert shots.detect_shots(video, detector="transnet") is sentinel
