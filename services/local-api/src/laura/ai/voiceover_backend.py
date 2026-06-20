"""Pluggable Voiceover/TTS backend interface for Laura.

The real TTS engines stay outside Laura's core process.  This module provides a
safe dependency-free stub and an HTTP sidecar adapter so the editor flow can be
tested and used without making heavy model packages mandatory.
"""

from __future__ import annotations

import json
import math
import os
import struct
import wave
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_VOICEOVER_URL = "http://127.0.0.1:8898"
DEFAULT_VOICEOVER_TIMEOUT_SECONDS = 180.0
DEFAULT_VOICEOVER_SAMPLE_RATE = 48_000


@runtime_checkable
class VoiceoverBackend(Protocol):
    name: str

    def available(self) -> bool:
        """Return True iff this backend can currently synthesize audio."""
        ...

    def synthesize(
        self,
        *,
        text: str,
        out_path: Path,
        duration_frames: int,
        fps_num: int,
        fps_den: int,
        sample_rate: int,
        language: str | None = None,
    ) -> None:
        """Write a WAV voiceover to *out_path*."""
        ...


class StubVoiceoverBackend:
    """Deterministic audible placeholder for the voiceover flow.

    It intentionally does not pretend to be a human voice.  It writes a mono
    PCM WAV with a soft tone envelope whose exact sample count matches the
    requested frame duration, which makes the whole Laura placement/export path
    testable without a TTS model.
    """

    name = "stub"

    def available(self) -> bool:
        return True

    def synthesize(
        self,
        *,
        text: str,
        out_path: Path,
        duration_frames: int,
        fps_num: int,
        fps_den: int,
        sample_rate: int,
        language: str | None = None,  # noqa: ARG002 - stub is language-independent
    ) -> None:
        if duration_frames <= 0:
            raise ValueError("voiceover duration must be positive")
        if fps_num <= 0 or fps_den <= 0:
            raise ValueError("voiceover fps must be positive")
        if sample_rate <= 0:
            raise ValueError("voiceover sample rate must be positive")

        samples = round(duration_frames * sample_rate * fps_den / fps_num)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        base_freq = 220 + (sum(ord(ch) for ch in text) % 180)
        amplitude = 7000
        with wave.open(str(out_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            frames = bytearray()
            for idx in range(samples):
                t = idx / sample_rate
                envelope = min(1.0, idx / max(1, sample_rate * 0.03))
                envelope *= min(1.0, (samples - idx) / max(1, sample_rate * 0.03))
                value = int(amplitude * envelope * math.sin(2.0 * math.pi * base_freq * t))
                frames.extend(struct.pack("<h", value))
            wav.writeframes(bytes(frames))


class SidecarVoiceoverBackend:
    """HTTP adapter for a local VibeVideo/Chatterbox/Fish-style TTS sidecar.

    Expected contract:

    * ``GET /healthz`` -> 2xx when ready
    * ``POST /voiceover`` JSON body with text, duration, fps, language and
      sample-rate -> response body is WAV bytes.
    """

    name = "sidecar"

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        raw_base_url = base_url or os.environ.get("LAURA_VOICEOVER_URL") or DEFAULT_VOICEOVER_URL
        self.base_url = raw_base_url.rstrip("/")
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else _float_env("LAURA_VOICEOVER_TIMEOUT", DEFAULT_VOICEOVER_TIMEOUT_SECONDS)
        )

    def available(self) -> bool:
        request = Request(f"{self.base_url}/healthz", method="GET")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return 200 <= int(response.status) < 300
        except (HTTPError, OSError, TimeoutError, URLError, ValueError):
            return False

    def synthesize(
        self,
        *,
        text: str,
        out_path: Path,
        duration_frames: int,
        fps_num: int,
        fps_den: int,
        sample_rate: int,
        language: str | None = None,
    ) -> None:
        payload = {
            "text": text,
            "duration_frames": duration_frames,
            "fps_num": fps_num,
            "fps_den": fps_den,
            "sample_rate": sample_rate,
            "language": language,
        }
        data = json.dumps(payload).encode("utf-8")
        request = Request(
            f"{self.base_url}/voiceover",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(data)),
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                status = int(response.status)
                wav_bytes = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"voiceover sidecar failed with HTTP {exc.code}: {detail}") from exc
        except (OSError, TimeoutError, URLError) as exc:
            raise RuntimeError(f"voiceover sidecar unavailable: {exc}") from exc

        if not 200 <= status < 300:
            raise RuntimeError(f"voiceover sidecar failed with HTTP {status}")
        if not wav_bytes:
            raise RuntimeError("voiceover sidecar returned an empty response")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(wav_bytes)


class UnavailableVoiceoverBackend:
    def __init__(self, name: str) -> None:
        self.name = name

    def available(self) -> bool:
        return False

    def synthesize(
        self,
        *,
        text: str,  # noqa: ARG002
        out_path: Path,  # noqa: ARG002
        duration_frames: int,  # noqa: ARG002
        fps_num: int,  # noqa: ARG002
        fps_den: int,  # noqa: ARG002
        sample_rate: int,  # noqa: ARG002
        language: str | None = None,  # noqa: ARG002
    ) -> None:
        raise RuntimeError(f"voiceover backend '{self.name}' is not installed")


def resolve_voiceover_backend(
    name: str | None = None,
    *,
    base_url: str | None = None,
) -> VoiceoverBackend:
    chosen = (name or os.environ.get("LAURA_VOICEOVER_BACKEND") or "stub").strip().lower()
    if chosen == "stub":
        return StubVoiceoverBackend()
    if chosen in {"sidecar", "vibevideo"}:
        return SidecarVoiceoverBackend(base_url=base_url)
    return UnavailableVoiceoverBackend(chosen)


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        parsed = float(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default
