"""Voiceover backend protocol unit tests: the ``fit_to_slot`` flag across all backends, and
the ElevenLabs cloud TTS backend (task 1 of the narrated-reel arc, see
docs/superpowers/specs/2026-08-21-narrated-reel-design.md §1-2).

These are pure unit tests against the backend classes directly (mocked ``urlopen``/
``subprocess.run``/``run_ffmpeg``) -- no ffmpeg binary, no network, no Windows SAPI required.
"""

from __future__ import annotations

import email.message
import io
import json
import re
import subprocess
import wave
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest

from laura.ai import voiceover_backend as vo_backend
from laura.ai.voiceover_backend import (
    ElevenLabsVoiceoverBackend,
    SidecarVoiceoverBackend,
    StubVoiceoverBackend,
    UnavailableVoiceoverBackend,
    WindowsSapiVoiceoverBackend,
    resolve_voiceover_backend,
)

_SET_OUTPUT_RE = re.compile(r"SetOutputToWaveFile\('([^']+)'\)")


def _http_error(code: int, body: bytes) -> HTTPError:
    return HTTPError(
        "https://api.elevenlabs.io/v1/text-to-speech/voice-1",
        code,
        "boom",
        email.message.Message(),
        fp=io.BytesIO(body),
    )


class _FakeHTTPResponse:
    """Minimal context-manager stand-in for ``http.client.HTTPResponse``."""

    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


# --- registry ------------------------------------------------------------------------------


def test_resolve_backend_elevenlabs_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAURA_VOICEOVER_BACKEND", raising=False)
    assert isinstance(resolve_voiceover_backend("elevenlabs"), ElevenLabsVoiceoverBackend)
    assert isinstance(resolve_voiceover_backend("el"), ElevenLabsVoiceoverBackend)


def test_resolve_backend_auto_is_unchanged_by_elevenlabs(monkeypatch: pytest.MonkeyPatch) -> None:
    """"auto" never implies the paid cloud backend -- only SAPI -> stub, exactly as before."""
    monkeypatch.delenv("LAURA_VOICEOVER_BACKEND", raising=False)
    monkeypatch.setattr(vo_backend, "_sapi_available", lambda: True)
    assert isinstance(resolve_voiceover_backend("auto"), WindowsSapiVoiceoverBackend)
    monkeypatch.setattr(vo_backend, "_sapi_available", lambda: False)
    assert isinstance(resolve_voiceover_backend("auto"), StubVoiceoverBackend)


# --- availability --------------------------------------------------------------------------


def test_elevenlabs_available_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAURA_ELEVENLABS_API_KEY", raising=False)
    backend = ElevenLabsVoiceoverBackend()
    assert backend.available() is False

    monkeypatch.setenv("LAURA_ELEVENLABS_API_KEY", "secret-key-123")
    assert backend.available() is True


# --- fit_to_slot: stub -----------------------------------------------------------------------


def test_stub_ignores_fit_to_slot(tmp_path: Path) -> None:
    backend = StubVoiceoverBackend()
    out_fit = tmp_path / "fit.wav"
    out_natural = tmp_path / "natural.wav"
    backend.synthesize(
        text="hi", out_path=out_fit, duration_frames=30, fps_num=30, fps_den=1,
        sample_rate=48_000, fit_to_slot=True,
    )
    backend.synthesize(
        text="hi", out_path=out_natural, duration_frames=30, fps_num=30, fps_den=1,
        sample_rate=48_000, fit_to_slot=False,
    )
    with wave.open(str(out_fit), "rb") as w1, wave.open(str(out_natural), "rb") as w2:
        assert w1.getnframes() == w2.getnframes() == 48_000


# --- fit_to_slot: sidecar --------------------------------------------------------------------


def test_sidecar_payload_carries_fit_to_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: float | None = None) -> _FakeHTTPResponse:
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeHTTPResponse(b"RIFF")

    monkeypatch.setattr(vo_backend, "urlopen", fake_urlopen)

    backend = SidecarVoiceoverBackend(base_url="http://127.0.0.1:8898")
    backend.synthesize(
        text="hi", out_path=tmp_path / "out.wav", duration_frames=30, fps_num=30, fps_den=1,
        sample_rate=48_000, fit_to_slot=False,
    )
    assert captured["payload"]["fit_to_slot"] is False
    assert captured["payload"]["duration_frames"] == 30


# --- fit_to_slot: SAPI (mocked -- no real Windows System.Speech / ffmpeg needed) --------------


def test_sapi_fit_to_slot_false_skips_apad_and_trim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_subprocess_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        script = cmd[-1]
        match = _SET_OUTPUT_RE.search(script)
        assert match is not None, "expected SetOutputToWaveFile(...) in the SAPI script"
        Path(match.group(1)).write_bytes(b"\x00" * 44)  # dummy WAV; ffmpeg is mocked below
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(vo_backend.subprocess, "run", fake_subprocess_run)  # type: ignore[attr-defined]

    captured: dict[str, list[str]] = {}

    def fake_run_ffmpeg(args: list[str]) -> None:
        captured["args"] = args

    monkeypatch.setattr("laura.ingest.ffmpeg.run_ffmpeg", fake_run_ffmpeg)

    backend = WindowsSapiVoiceoverBackend()
    backend.synthesize(
        text="hello", out_path=tmp_path / "out.wav", duration_frames=90, fps_num=30, fps_den=1,
        sample_rate=48_000, fit_to_slot=False,
    )

    assert "apad" not in captured["args"]
    assert "-t" not in captured["args"]


def test_sapi_fit_to_slot_true_includes_apad_and_trim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_subprocess_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        script = cmd[-1]
        match = _SET_OUTPUT_RE.search(script)
        assert match is not None
        Path(match.group(1)).write_bytes(b"\x00" * 44)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(vo_backend.subprocess, "run", fake_subprocess_run)  # type: ignore[attr-defined]

    captured: dict[str, list[str]] = {}

    def fake_run_ffmpeg(args: list[str]) -> None:
        captured["args"] = args

    monkeypatch.setattr("laura.ingest.ffmpeg.run_ffmpeg", fake_run_ffmpeg)

    backend = WindowsSapiVoiceoverBackend()
    backend.synthesize(
        text="hello", out_path=tmp_path / "out.wav", duration_frames=90, fps_num=30, fps_den=1,
        sample_rate=48_000, fit_to_slot=True,
    )

    assert "apad" in captured["args"]
    assert "-t" in captured["args"]


# --- fit_to_slot / mp3->wav path: ElevenLabs (mocked urlopen + run_ffmpeg) --------------------


def test_elevenlabs_synthesize_fit_to_slot_true_pads_and_trims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAURA_ELEVENLABS_API_KEY", "secret-key-xyz")
    monkeypatch.setenv("LAURA_ELEVENLABS_VOICE", "voice-1")

    def fake_urlopen(request: Any, timeout: float | None = None) -> _FakeHTTPResponse:
        return _FakeHTTPResponse(b"fake-mp3-bytes")

    monkeypatch.setattr(vo_backend, "urlopen", fake_urlopen)

    captured: dict[str, list[str]] = {}

    def fake_run_ffmpeg(args: list[str]) -> None:
        captured["args"] = args

    monkeypatch.setattr("laura.ingest.ffmpeg.run_ffmpeg", fake_run_ffmpeg)

    backend = ElevenLabsVoiceoverBackend()
    backend.synthesize(
        text="hello there", out_path=tmp_path / "el.wav", duration_frames=90, fps_num=30,
        fps_den=1, sample_rate=48_000, fit_to_slot=True,
    )

    assert "apad" in captured["args"]
    assert "-t" in captured["args"]


def test_elevenlabs_synthesize_fit_to_slot_false_is_natural_length(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAURA_ELEVENLABS_API_KEY", "secret-key-xyz")
    monkeypatch.setenv("LAURA_ELEVENLABS_VOICE", "voice-1")

    def fake_urlopen(request: Any, timeout: float | None = None) -> _FakeHTTPResponse:
        return _FakeHTTPResponse(b"fake-mp3-bytes")

    monkeypatch.setattr(vo_backend, "urlopen", fake_urlopen)

    captured: dict[str, list[str]] = {}

    def fake_run_ffmpeg(args: list[str]) -> None:
        captured["args"] = args

    monkeypatch.setattr("laura.ingest.ffmpeg.run_ffmpeg", fake_run_ffmpeg)

    backend = ElevenLabsVoiceoverBackend()
    backend.synthesize(
        text="hello there", out_path=tmp_path / "el.wav", duration_frames=90, fps_num=30,
        fps_den=1, sample_rate=48_000, fit_to_slot=False,
    )

    assert "apad" not in captured["args"]
    assert "-t" not in captured["args"]


# --- ElevenLabs error handling: body verbatim, key never leaked -------------------------------


def test_elevenlabs_http_error_surfaces_body_never_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api_key = "sk-super-secret-value-do-not-leak"
    monkeypatch.setenv("LAURA_ELEVENLABS_API_KEY", api_key)
    monkeypatch.setenv("LAURA_ELEVENLABS_VOICE", "voice-1")

    body = b'{"detail":{"status":"payment_issue"}}'

    def fake_urlopen(request: Any, timeout: float | None = None) -> Any:
        raise _http_error(402, body)

    monkeypatch.setattr(vo_backend, "urlopen", fake_urlopen)

    backend = ElevenLabsVoiceoverBackend()
    with pytest.raises(RuntimeError) as exc_info:
        backend.synthesize(
            text="hi", out_path=tmp_path / "out.wav", duration_frames=30, fps_num=30, fps_den=1,
            sample_rate=48_000,
        )

    message = str(exc_info.value)
    assert "payment_issue" in message
    assert api_key not in message


def test_elevenlabs_missing_api_key_error_never_contains_a_key(tmp_path: Path) -> None:
    """No key configured at all -- the error must stay generic, no network call attempted."""
    backend = ElevenLabsVoiceoverBackend()
    with pytest.raises(RuntimeError) as exc_info:
        backend.synthesize(
            text="hi", out_path=tmp_path / "out.wav", duration_frames=30, fps_num=30, fps_den=1,
            sample_rate=48_000,
        )
    assert "elevenlabs" in str(exc_info.value)


# --- UnavailableVoiceoverBackend accepts (and ignores) the flag -------------------------------


def test_unavailable_backend_accepts_fit_to_slot_kwarg(tmp_path: Path) -> None:
    backend = UnavailableVoiceoverBackend("nope")
    with pytest.raises(RuntimeError):
        backend.synthesize(
            text="hi", out_path=tmp_path / "out.wav", duration_frames=30, fps_num=30, fps_den=1,
            sample_rate=48_000, fit_to_slot=False,
        )
