"""ComfyUIVideoGenerateBackend + resolver (Axis 2, Slice 2).

Drives the backend end-to-end against a fake ComfyUI that captures the submitted workflow, so we
can assert the prompt / frame-count were injected and the output was downloaded — with no real
ComfyUI, GPU, or model. The real round-trip is manual-to-verify.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from laura.generate.backend import (
    ComfyUIVideoGenerateBackend,
    StubVideoGenerateBackend,
    resolve_video_generate_backend,
)
from laura.generate.comfyui_client import ComfyUIClient


class _CapturingComfy(BaseHTTPRequestHandler):
    submitted: dict[str, Any] = {}

    def log_message(self, *args: Any) -> None:
        pass

    def _json(self, status: int, obj: dict[str, Any]) -> None:
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        type(self).submitted = json.loads(body)["prompt"]
        self._json(200, {"prompt_id": "pid-1"})

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/history/"):
            self._json(200, {
                "pid-1": {"outputs": {"9": {"gifs": [
                    {"filename": "out.mp4", "subfolder": "", "type": "output"}
                ]}}}
            })
        elif self.path.startswith("/view"):
            data = b"MP4-BYTES"
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self._json(404, {})


@pytest.fixture
def comfy() -> Iterator[tuple[str, type[Any]]]:
    handler = type("H", (_CapturingComfy,), {})
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", handler
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_backend_injects_prompt_frames_and_downloads(
    comfy: tuple[str, type[Any]], tmp_path: Path
) -> None:
    url, handler = comfy
    template = {
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "A %PROMPT% scene"}},
        "7": {"class_type": "LTXVScheduler", "inputs": {"num_frames": "%FRAMES%"}},
    }
    backend = ComfyUIVideoGenerateBackend(ComfyUIClient(url), template)
    out = tmp_path / "gen.mp4"

    backend.generate(prompt="calm ocean", out_path=out, duration_frames=90, fps_num=30, fps_den=1)

    assert out.read_bytes() == b"MP4-BYTES"
    assert handler.submitted["6"]["inputs"]["text"] == "A calm ocean scene"
    assert handler.submitted["7"]["inputs"]["num_frames"] == 90  # injected as int, not "%FRAMES%"


def test_resolve_returns_stub_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAURA_COMFYUI_URL", raising=False)
    assert isinstance(resolve_video_generate_backend(), StubVideoGenerateBackend)


def test_resolve_returns_comfyui_when_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wf = tmp_path / "workflow.json"
    wf.write_text(json.dumps({"1": {"class_type": "X", "inputs": {}}}))
    monkeypatch.setenv("LAURA_COMFYUI_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("LAURA_COMFYUI_WORKFLOW", str(wf))
    assert isinstance(resolve_video_generate_backend(), ComfyUIVideoGenerateBackend)


def test_resolve_falls_back_to_stub_when_workflow_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LAURA_COMFYUI_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("LAURA_COMFYUI_WORKFLOW", str(tmp_path / "does-not-exist.json"))
    assert isinstance(resolve_video_generate_backend(), StubVideoGenerateBackend)
