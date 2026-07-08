"""ElevenLabs voice backend (Slice 3): opt-in resolve + faked-HTTP synthesize.

No real API call here — ``_http_post_bytes`` is monkeypatched; the actual roundtrip is
manual-to-verify (needs a key in the environment).
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from laura.short_creator import voice


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


def test_synthesize_writes_mp3_and_posts_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def fake_post(url: str, payload: bytes, headers: dict[str, str]) -> bytes:
        seen["url"] = url
        seen["payload"] = json.loads(payload.decode("utf-8"))
        seen["headers"] = headers
        return b"MP3-BYTES"

    monkeypatch.setattr(voice, "_http_post_bytes", fake_post)
    backend = voice.ElevenLabsVoiceBackend(api_key="k", voice_id="v42")
    out = tmp_path / "vo" / "clip.mp3"

    result = backend.synthesize("Volle Energie, los geht's!", out)

    assert result["ok"] is True
    assert out.read_bytes() == b"MP3-BYTES"
    assert "/text-to-speech/v42" in seen["url"]
    assert seen["payload"]["text"] == "Volle Energie, los geht's!"
    assert seen["payload"]["model_id"] == voice.DEFAULT_MODEL
    assert seen["headers"]["xi-api-key"] == "k"
    assert seen["headers"]["Accept"] == "audio/mpeg"


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


def test_synthesize_empty_audio_is_ok_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(voice, "_http_post_bytes", lambda url, payload, headers: b"")
    backend = voice.ElevenLabsVoiceBackend(api_key="k", voice_id="v")

    result = backend.synthesize("text", tmp_path / "c.mp3")

    assert result["ok"] is False
    assert not (tmp_path / "c.mp3").exists()
