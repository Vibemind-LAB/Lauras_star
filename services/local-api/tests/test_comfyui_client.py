"""ComfyUI HTTP client (Axis 2, Slice 2) — submit / wait / download against a fake ComfyUI server.

A threaded stub stands in for a running ComfyUI (E:\\ComfyUI); the client is exercised with no
real GPU, model, or ComfyUI. The real round-trip is manual-to-verify.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from laura.generate.comfyui_client import ComfyUIClient


class _FakeComfy(BaseHTTPRequestHandler):
    history_ready = True  # per-subclass; a test flips it to exercise the timeout path

    def log_message(self, *args: Any) -> None:  # silence
        pass

    def _json(self, status: int, obj: dict[str, Any]) -> None:
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self._json(200, {"prompt_id": "pid-1"}) if self.path == "/prompt" else self._json(404, {})

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/history/"):
            if type(self).history_ready:
                self._json(200, {
                    "pid-1": {
                        "outputs": {"9": {"gifs": [
                            {"filename": "out.mp4", "subfolder": "", "type": "output"}
                        ]}},
                        "status": {"completed": True},
                    }
                })
            else:
                self._json(200, {})  # not ready yet
        elif self.path.startswith("/view"):
            data = b"VIDEO-BYTES"
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self._json(404, {})


@pytest.fixture
def comfy() -> Iterator[tuple[str, type[Any]]]:
    handler = type("H", (_FakeComfy,), {})
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", handler
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_submit_returns_prompt_id(comfy: tuple[str, type[Any]]) -> None:
    url, _ = comfy
    pid = ComfyUIClient(url).submit({"1": {"class_type": "X", "inputs": {}}})
    assert pid == "pid-1"


def test_wait_returns_outputs_when_ready(comfy: tuple[str, type[Any]]) -> None:
    url, _ = comfy
    outputs = ComfyUIClient(url).wait("pid-1", timeout=2.0, interval=0.01)
    assert outputs["9"]["gifs"][0]["filename"] == "out.mp4"


def test_wait_times_out_when_never_ready(comfy: tuple[str, type[Any]]) -> None:
    url, handler = comfy
    handler.history_ready = False
    with pytest.raises(TimeoutError):
        ComfyUIClient(url).wait("pid-1", timeout=0.05, interval=0.01)


def test_download_writes_file(comfy: tuple[str, type[Any]], tmp_path: Path) -> None:
    url, _ = comfy
    out = tmp_path / "x.mp4"
    ComfyUIClient(url).download(filename="out.mp4", subfolder="", type_="output", out_path=out)
    assert out.read_bytes() == b"VIDEO-BYTES"
