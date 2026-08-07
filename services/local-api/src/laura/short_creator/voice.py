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
from uuid import uuid4

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


def _write_atomic_bytes(path: Path, data: bytes) -> None:
    """Publish ``data`` at ``path`` via a uniquely named tmp file + atomic replace.

    ``path`` doubles as this line's on-disk synthesis CACHE — a killed process (or any writer
    interrupted mid-write) must never leave a truncated file sitting AT the cache path, because
    a future run would then find it via ``clip.is_file()`` and treat garbage bytes as a valid,
    permanently-cached clip. The tmp name is unique per call (mirrors board.py's
    ``_write_atomic``) so two concurrent synthesis calls for the same line never tear each
    other's writes.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        tmp.write_bytes(data)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _words_from_alignment(
    chars: list[Any], starts: list[Any], ends: list[Any]
) -> list[dict[str, Any]]:
    """Word-level timings from ElevenLabs' character alignment. Pure.

    Characters group at whitespace; each word carries its first character's start and last
    character's end (seconds). Mismatched list lengths truncate to the shortest (zip).
    """
    words: list[dict[str, Any]] = []
    current = ""
    word_start = 0.0
    last_end = 0.0
    for ch, start_s, end_s in zip(chars, starts, ends, strict=False):
        if str(ch).isspace():
            if current:
                words.append({"text": current, "start_s": word_start, "end_s": last_end})
                current = ""
            continue
        if not current:
            word_start = float(start_s)
        current += str(ch)
        last_end = float(end_s)
    if current:
        words.append({"text": current, "start_s": word_start, "end_s": last_end})
    return words


class ElevenLabsVoiceBackend:
    """VoiceBackend over the ElevenLabs text-to-speech API (mp3 + word timings out).

    Uses the ``/with-timestamps`` variant: the response carries the audio (base64) plus a
    character-level alignment, which is folded into word timings and written as a
    ``<mp3>.timings.json`` sidecar — the renderer burns word-accurate captions from it
    (live finding: evenly-spread captions drift audibly from the spoken voice).
    """

    def __init__(self, *, api_key: str, voice_id: str, model: str = DEFAULT_MODEL) -> None:
        self.api_key = api_key
        self.voice_id = voice_id
        self.model = model

    def synthesize(self, text: str, out_path: Path) -> dict[str, Any]:
        import base64
        import json

        url = f"{API_BASE}/text-to-speech/{self.voice_id}/with-timestamps"
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
            "Accept": "application/json",
        }
        try:
            raw = _http_post_bytes(url, payload, headers)
        except (urllib.error.URLError, OSError) as exc:
            logger.warning("elevenlabs synthesize failed: %s", exc)
            return {"ok": False, "reason": str(exc)[:300]}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {"ok": False, "reason": "unexpected response (not JSON)"}
        try:
            audio = base64.b64decode(data.get("audio_base64") or "")
        except ValueError:
            audio = b""
        if not audio:
            return {"ok": False, "reason": "empty audio response"}
        _write_atomic_bytes(out_path, audio)
        result: dict[str, Any] = {"ok": True, "path": str(out_path), "bytes": len(audio)}

        alignment = data.get("alignment") or data.get("normalized_alignment") or {}
        words = _words_from_alignment(
            list(alignment.get("characters") or []),
            list(alignment.get("character_start_times_seconds") or []),
            list(alignment.get("character_end_times_seconds") or []),
        )
        if words:
            timings_path = Path(str(out_path) + ".timings.json")
            try:
                timings_path.write_text(
                    json.dumps({"words": words}, ensure_ascii=False), encoding="utf-8"
                )
                result["timings_path"] = str(timings_path)
            except OSError as exc:  # captions fall back to even spread — never fail the voice
                logger.warning("voiceover timings sidecar not written: %s", exc)
        return result


def resolve_voice_backend() -> VoiceBackend | None:
    """The configured ElevenLabs backend, or ``None`` without an API key."""
    api_key = (os.environ.get("LAURA_ELEVENLABS_API_KEY") or "").strip()
    if not api_key:
        return None
    voice_id = (os.environ.get("LAURA_ELEVENLABS_VOICE") or "").strip() or "21m00Tcm4TlvDq8ikWAM"
    return ElevenLabsVoiceBackend(api_key=api_key, voice_id=voice_id)
