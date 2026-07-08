"""ElevenLabs TTS backend for the Transcript Master (Slice 3).

Stdlib-HTTP wie ``describe.py``/``vlm_ollama.py`` — keine neue Dependency. Opt-in & graceful:
:func:`resolve_voice_backend` liefert ``None`` ohne ``LAURA_ELEVENLABS_API_KEY``; ein Fehler beim
Synthetisieren wird als ``ok=False`` gemeldet, nie geworfen. Der echte API-Roundtrip ist
manual-to-verify (kein Key in CI); Tests faken die HTTP-Schicht.
"""

from __future__ import annotations

import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

API_BASE = "https://api.elevenlabs.io/v1"
# Multilingual model — the user's scripts are German-first, never English.
DEFAULT_MODEL = "eleven_multilingual_v2"


@runtime_checkable
class VoiceBackend(Protocol):
    """A TTS engine (or fake) that turns a script into an audio file."""

    def synthesize(self, text: str, out_path: Path) -> dict[str, Any]: ...


def _http_post_bytes(url: str, payload: bytes, headers: dict[str, str]) -> bytes:
    req = urllib.request.Request(url, data=payload, headers=headers)
    with urllib.request.urlopen(req, timeout=300.0) as resp:  # noqa: S310 (fixed https host)
        return bytes(resp.read())


class ElevenLabsVoiceBackend:
    """VoiceBackend over the ElevenLabs text-to-speech API (mp3 out)."""

    def __init__(self, *, api_key: str, voice_id: str, model: str = DEFAULT_MODEL) -> None:
        self.api_key = api_key
        self.voice_id = voice_id
        self.model = model

    def synthesize(self, text: str, out_path: Path) -> dict[str, Any]:
        import json

        url = f"{API_BASE}/text-to-speech/{self.voice_id}"
        payload = json.dumps(
            {
                "text": text,
                "model_id": self.model,
                "voice_settings": {"stability": 0.4, "similarity_boost": 0.75},
            }
        ).encode("utf-8")
        headers = {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        try:
            audio = _http_post_bytes(url, payload, headers)
        except (urllib.error.URLError, OSError) as exc:
            logger.warning("elevenlabs synthesize failed: %s", exc)
            return {"ok": False, "reason": str(exc)[:300]}
        if not audio:
            return {"ok": False, "reason": "empty audio response"}
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(audio)
        return {"ok": True, "path": str(out_path), "bytes": len(audio)}


def resolve_voice_backend() -> VoiceBackend | None:
    """The configured ElevenLabs backend, or ``None`` without an API key."""
    api_key = (os.environ.get("LAURA_ELEVENLABS_API_KEY") or "").strip()
    if not api_key:
        return None
    voice_id = (os.environ.get("LAURA_ELEVENLABS_VOICE") or "").strip() or "21m00Tcm4TlvDq8ikWAM"
    return ElevenLabsVoiceBackend(api_key=api_key, voice_id=voice_id)
