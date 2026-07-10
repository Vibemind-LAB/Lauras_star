"""ElevenLabs voice backend: opt-in resolve + faked-HTTP synthesize with word timings.

No real API call here — ``_http_post_bytes`` is monkeypatched; the actual roundtrip is
manual-to-verify (needs a key in the environment). The ``/with-timestamps`` response carries
base64 audio + a character alignment that becomes a ``<mp3>.timings.json`` sidecar.
"""

from __future__ import annotations

import base64
import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from laura.short_creator import voice


def _response(audio: bytes = b"MP3-BYTES", alignment: dict[str, Any] | None = None) -> bytes:
    body: dict[str, Any] = {"audio_base64": base64.b64encode(audio).decode("ascii")}
    if alignment is not None:
        body["alignment"] = alignment
    return json.dumps(body).encode("utf-8")


def test_resolve_backend_none_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAURA_ELEVENLABS_API_KEY", raising=False)
    assert voice.resolve_voice_backend() is None


def test_resolve_backend_reads_key_and_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAURA_ELEVENLABS_API_KEY", "k123")
    monkeypatch.setenv("LAURA_ELEVENLABS_VOICE", "voiceX")
    backend = voice.resolve_voice_backend()
    assert isinstance(backend, voice.ElevenLabsVoiceBackend)
    assert backend.api_key == "k123"
    assert backend.voice_id == "voiceX"


def test_resolve_backend_falls_back_to_default_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAURA_ELEVENLABS_API_KEY", "k123")
    monkeypatch.delenv("LAURA_ELEVENLABS_VOICE", raising=False)
    backend = voice.resolve_voice_backend()
    assert isinstance(backend, voice.ElevenLabsVoiceBackend)
    assert backend.voice_id


def test_words_from_alignment_groups_at_whitespace() -> None:
    chars = ["Y", "o", " ", "l", "o", "s", "!"]
    starts = [0.0, 0.1, 0.2, 0.5, 0.6, 0.7, 0.8]
    ends = [0.1, 0.2, 0.3, 0.6, 0.7, 0.8, 0.9]
    words = voice._words_from_alignment(chars, starts, ends)
    assert words == [
        {"text": "Yo", "start_s": 0.0, "end_s": 0.2},
        {"text": "los!", "start_s": 0.5, "end_s": 0.9},
    ]


def test_synthesize_writes_mp3_timings_and_posts_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}
    alignment = {
        "characters": ["H", "i", " ", "d", "a"],
        "character_start_times_seconds": [0.0, 0.1, 0.2, 0.4, 0.5],
        "character_end_times_seconds": [0.1, 0.2, 0.3, 0.5, 0.6],
    }

    def fake_post(url: str, payload: bytes, headers: dict[str, str]) -> bytes:
        seen["url"] = url
        seen["payload"] = json.loads(payload.decode("utf-8"))
        seen["headers"] = headers
        return _response(alignment=alignment)

    monkeypatch.setattr(voice, "_http_post_bytes", fake_post)
    backend = voice.ElevenLabsVoiceBackend(api_key="k", voice_id="v42")
    out = tmp_path / "vo" / "clip.mp3"

    result = backend.synthesize("Hi da", out)

    assert result["ok"] is True
    assert out.read_bytes() == b"MP3-BYTES"
    assert "/text-to-speech/v42/with-timestamps" in seen["url"]
    assert seen["payload"]["text"] == "Hi da"
    assert seen["payload"]["model_id"] == voice.DEFAULT_MODEL
    assert seen["headers"]["xi-api-key"] == "k"
    assert seen["headers"]["Accept"] == "application/json"

    # Word timings landed as a sidecar the renderer can burn word-accurate captions from.
    sidecar = Path(result["timings_path"])
    assert sidecar == Path(str(out) + ".timings.json")
    timed = json.loads(sidecar.read_text(encoding="utf-8"))["words"]
    assert timed == [
        {"text": "Hi", "start_s": 0.0, "end_s": 0.2},
        {"text": "da", "start_s": 0.4, "end_s": 0.6},
    ]


def test_synthesize_without_alignment_still_writes_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(voice, "_http_post_bytes", lambda url, payload, headers: _response())
    backend = voice.ElevenLabsVoiceBackend(api_key="k", voice_id="v")
    out = tmp_path / "clip.mp3"

    result = backend.synthesize("text", out)

    assert result["ok"] is True
    assert out.read_bytes() == b"MP3-BYTES"
    assert "timings_path" not in result
    assert not Path(str(out) + ".timings.json").exists()


def test_synthesize_http_error_is_ok_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, payload: bytes, headers: dict[str, str]) -> bytes:
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(voice, "_http_post_bytes", fake_post)
    backend = voice.ElevenLabsVoiceBackend(api_key="k", voice_id="v")
    out = tmp_path / "clip.mp3"

    result = backend.synthesize("text", out)

    assert result["ok"] is False
    assert "boom" in result["reason"]
    assert not out.exists()


def test_synthesize_bad_responses_are_ok_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Not JSON at all.
    monkeypatch.setattr(voice, "_http_post_bytes", lambda url, payload, headers: b"\xff\xfe")
    backend = voice.ElevenLabsVoiceBackend(api_key="k", voice_id="v")
    assert backend.synthesize("text", tmp_path / "a.mp3")["ok"] is False

    # JSON without audio.
    monkeypatch.setattr(voice, "_http_post_bytes", lambda url, payload, headers: b"{}")
    result = backend.synthesize("text", tmp_path / "b.mp3")
    assert result["ok"] is False
    assert not (tmp_path / "b.mp3").exists()
