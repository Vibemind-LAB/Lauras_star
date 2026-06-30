"""Tests for the optional ASR sidecar adapter (``laura.analysis.sidecar``).

A threaded stub HTTP worker stands in for the GPU container so the fallback chain
(sidecar -> in-process -> skip) is exercised without Docker or a real model.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

import laura.analysis.asr as asr
import laura.analysis.sidecar as sidecar
from laura.analysis.types import SegmentResult


class _StubHandler(BaseHTTPRequestHandler):
    healthz_ok = True
    transcribe_status = 200
    transcribe_body: dict[str, Any] = {"segments": []}

    def log_message(self, *args: Any) -> None:  # silence test noise
        pass

    def _json(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._json(200, {"status": "ok", "device": "cpu"}) if self.healthz_ok else self._json(
                503, {"status": "down"}
            )
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length", "0")))  # drain body
        if self.path.startswith("/transcribe"):
            self._json(self.transcribe_status, self.transcribe_body)
        else:
            self._json(404, {"error": "not found"})


@pytest.fixture
def stub_worker() -> Iterator[tuple[str, type[Any]]]:
    handler = type("H", (_StubHandler,), {})  # fresh subclass so config never leaks across tests
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", handler
    finally:
        server.shutdown()
        thread.join(timeout=2)


@pytest.fixture
def wav(tmp_path: Path) -> Path:
    p = tmp_path / "a.wav"
    p.write_bytes(b"RIFF0000WAVE")  # contents ignored by the stub
    return p


_SEGMENTS = {
    "segments": [
        {
            "text": "hallo welt",
            "start_sec": 0.0,
            "end_sec": 1.5,
            "confidence": -0.2,
            "words": [
                {"text": "hallo", "start_sec": 0.0, "end_sec": 0.7, "confidence": 0.9},
                {"text": "welt", "start_sec": 0.8, "end_sec": 1.5, "confidence": 0.8},
            ],
        }
    ]
}


def test_sidecar_success(
    stub_worker: tuple[str, type[Any]], wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, handler = stub_worker
    handler.transcribe_body = _SEGMENTS
    monkeypatch.setenv("LAURA_ANALYSIS_URL", url)
    out = sidecar.transcribe(wav, model_size="base", language="de")
    assert len(out) == 1
    seg = out[0]
    assert isinstance(seg, SegmentResult)
    assert seg.text == "hallo welt"
    assert (seg.start_sec, seg.end_sec) == (0.0, 1.5)
    assert [w.text for w in seg.words] == ["hallo", "welt"]
    assert seg.words[0].confidence == 0.9


def test_asr_available_true_when_healthy(
    stub_worker: tuple[str, type[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, _ = stub_worker
    monkeypatch.setenv("LAURA_ANALYSIS_URL", url)
    assert sidecar.asr_available() is True


def test_unhealthy_sidecar_falls_back_to_local(
    stub_worker: tuple[str, type[Any]], wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, handler = stub_worker
    handler.healthz_ok = False
    monkeypatch.setenv("LAURA_ANALYSIS_URL", url)
    sentinel = [SegmentResult(text="local", start_sec=0.0, end_sec=1.0)]
    monkeypatch.setattr(asr, "faster_whisper_available", lambda: True)
    monkeypatch.setattr(asr, "transcribe", lambda *a, **k: sentinel)
    assert sidecar.transcribe(wav, model_size="base", language=None) is sentinel


def test_sidecar_error_falls_back_when_local_available(
    stub_worker: tuple[str, type[Any]], wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, handler = stub_worker
    handler.transcribe_status = 500
    handler.transcribe_body = {"error": "boom"}
    monkeypatch.setenv("LAURA_ANALYSIS_URL", url)
    sentinel = [SegmentResult(text="local", start_sec=0.0, end_sec=1.0)]
    monkeypatch.setattr(asr, "faster_whisper_available", lambda: True)
    monkeypatch.setattr(asr, "transcribe", lambda *a, **k: sentinel)
    assert sidecar.transcribe(wav, model_size="base", language=None) is sentinel


def test_sidecar_error_raises_when_no_local(
    stub_worker: tuple[str, type[Any]], wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, handler = stub_worker
    handler.transcribe_status = 500
    monkeypatch.setenv("LAURA_ANALYSIS_URL", url)
    monkeypatch.setattr(asr, "faster_whisper_available", lambda: False)
    with pytest.raises(Exception):  # noqa: B017,PT011 - transport error must surface
        sidecar.transcribe(wav, model_size="base", language=None)


def test_no_sidecar_uses_local(wav: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAURA_ANALYSIS_URL", raising=False)
    sentinel = [SegmentResult(text="local", start_sec=0.0, end_sec=1.0)]
    monkeypatch.setattr(asr, "faster_whisper_available", lambda: True)
    monkeypatch.setattr(asr, "transcribe", lambda *a, **k: sentinel)
    assert sidecar.transcribe(wav, model_size="base", language=None) is sentinel


def test_asr_available_false_without_sidecar_or_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAURA_ANALYSIS_URL", raising=False)
    monkeypatch.setattr(asr, "faster_whisper_available", lambda: False)
    assert sidecar.asr_available() is False
