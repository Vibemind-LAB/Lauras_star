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
import subprocess
import sys
import tempfile
import wave
from functools import lru_cache
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_VOICEOVER_URL = "http://127.0.0.1:8898"
DEFAULT_VOICEOVER_TIMEOUT_SECONDS = 180.0
DEFAULT_VOICEOVER_SAMPLE_RATE = 48_000
DEFAULT_ELEVENLABS_MODEL = "eleven_multilingual_v2"
ELEVENLABS_TTS_URL_TEMPLATE = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"


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
        voice_id: str | None = None,
        fit_to_slot: bool = True,
    ) -> None:
        """Write a WAV voiceover to *out_path*.

        ``fit_to_slot=True`` (the default, preserving today's behavior) pads/trims the clip to
        exactly ``duration_frames``. ``fit_to_slot=False`` writes the full natural-length
        speech instead -- the caller measures the resulting duration itself.
        """
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
        voice_id: str | None = None,  # noqa: ARG002 - stub has a single tone
        fit_to_slot: bool = True,  # noqa: ARG002 - stub always emits duration_frames worth
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


class WindowsSapiVoiceoverBackend:
    """Real offline speech via the built-in Windows ``System.Speech`` engine (SAPI 5).

    Zero extra Python dependencies: it shells out to PowerShell to render the text to a WAV with
    the system TTS voice, then uses ffmpeg to resample to the canonical mono rate and fit the clip
    slot (pad short speech with silence, trim overly long speech). Robotic but immediate and fully
    local; a neural sidecar (:class:`SidecarVoiceoverBackend`) remains the quality upgrade path.
    """

    name = "sapi"

    def available(self) -> bool:
        return _sapi_available()

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
        voice_id: str | None = None,
        fit_to_slot: bool = True,
    ) -> None:
        if duration_frames <= 0:
            raise ValueError("voiceover duration must be positive")
        if fps_num <= 0 or fps_den <= 0:
            raise ValueError("voiceover fps must be positive")
        if sample_rate <= 0:
            raise ValueError("voiceover sample rate must be positive")

        # Lazy import keeps the module light for the dependency-free stub path.
        from ..ingest.ffmpeg import run_ffmpeg

        spoken = text.strip() or "."  # System.Speech rejects an empty utterance
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            text_file = tmp_dir / "voiceover.txt"
            text_file.write_text(spoken, encoding="utf-8")
            raw_wav = tmp_dir / "voiceover_raw.wav"
            script = (
                "$ErrorActionPreference='Stop'; "
                "Add-Type -AssemblyName System.Speech; "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                + _sapi_voice_select_script(voice_id, language)
                + f"$s.SetOutputToWaveFile('{raw_wav}'); "
                f"$t = [System.IO.File]::ReadAllText('{text_file}', "
                "[System.Text.Encoding]::UTF8); "
                "$s.Speak($t); $s.Dispose()"
            )
            try:
                proc = subprocess.run(  # noqa: S603
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise RuntimeError(f"windows SAPI synthesis failed to launch: {exc}") from exc
            if proc.returncode != 0 or not raw_wav.is_file():
                detail = (proc.stderr or proc.stdout or "").strip()[:300]
                raise RuntimeError(f"windows SAPI synthesis failed: {detail}")

            # Canonical mono rate; fit the slot when requested (``apad`` pads short speech,
            # ``-t`` trims long) -- ``fit_to_slot=False`` writes the natural-length speech as-is.
            args = ["-i", str(raw_wav), "-ac", "1", "-ar", str(sample_rate)]
            if fit_to_slot:
                seconds = duration_frames * fps_den / fps_num
                args += ["-af", "apad", "-t", f"{seconds:.6f}"]
            args += ["-c:a", "pcm_s16le", str(out_path)]
            run_ffmpeg(args)


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
        voice_id: str | None = None,
        fit_to_slot: bool = True,
    ) -> None:
        payload = {
            "text": text,
            "duration_frames": duration_frames,
            "fps_num": fps_num,
            "fps_den": fps_den,
            "sample_rate": sample_rate,
            "language": language,
            "voice_id": voice_id,
            "fit_to_slot": fit_to_slot,
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
        voice_id: str | None = None,  # noqa: ARG002
        fit_to_slot: bool = True,  # noqa: ARG002
    ) -> None:
        raise RuntimeError(f"voiceover backend '{self.name}' is not installed")


class ElevenLabsVoiceoverBackend:
    """Cloud TTS via the ElevenLabs REST API (stdlib ``urllib`` only, no SDK dependency).

    A real neural voice as an *explicit* opt-in path -- never implied by ``"auto"`` (heavy/paid
    backends are optional extras, per the repo invariant). Requires
    ``LAURA_ELEVENLABS_API_KEY``; voice and model default from env but can be overridden per call.
    """

    name = "elevenlabs"

    def __init__(self, *, timeout_seconds: float | None = None) -> None:
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else _float_env("LAURA_VOICEOVER_TIMEOUT", DEFAULT_VOICEOVER_TIMEOUT_SECONDS)
        )

    def available(self) -> bool:
        """True iff an API key is configured. No network round-trip: request errors (bad key,
        payment issues, rate limits) surface at synthesis time, same as the sidecar backend."""
        return bool(os.environ.get("LAURA_ELEVENLABS_API_KEY"))

    def synthesize(
        self,
        *,
        text: str,
        out_path: Path,
        duration_frames: int,
        fps_num: int,
        fps_den: int,
        sample_rate: int,
        language: str | None = None,  # noqa: ARG002 - ElevenLabs infers language from the voice
        voice_id: str | None = None,
        fit_to_slot: bool = True,
    ) -> None:
        if duration_frames <= 0:
            raise ValueError("voiceover duration must be positive")
        if fps_num <= 0 or fps_den <= 0:
            raise ValueError("voiceover fps must be positive")
        if sample_rate <= 0:
            raise ValueError("voiceover sample rate must be positive")

        api_key = os.environ.get("LAURA_ELEVENLABS_API_KEY")
        if not api_key:
            raise RuntimeError(f"voiceover backend '{self.name}' is not installed")
        voice = voice_id or os.environ.get("LAURA_ELEVENLABS_VOICE")
        if not voice:
            raise RuntimeError(
                "elevenlabs voiceover requires a voice id "
                "(set LAURA_ELEVENLABS_VOICE or pass voice_id)"
            )
        model = os.environ.get("LAURA_ELEVENLABS_MODEL") or DEFAULT_ELEVENLABS_MODEL

        data = json.dumps({"text": text, "model_id": model}).encode("utf-8")
        request = Request(
            ELEVENLABS_TTS_URL_TEMPLATE.format(voice_id=voice),
            data=data,
            headers={
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
                "xi-api-key": api_key,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                status = int(response.status)
                mp3_bytes = response.read()
        except HTTPError as exc:
            # The body (e.g. {"detail": {"status": "payment_issue"}}) goes verbatim into the
            # message -- it never contains the request headers, so the API key cannot leak here.
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"elevenlabs voiceover failed with HTTP {exc.code}: {detail}"
            ) from exc
        except (OSError, TimeoutError, URLError) as exc:
            raise RuntimeError(f"elevenlabs voiceover unavailable: {exc}") from exc

        if not 200 <= status < 300:
            raise RuntimeError(f"elevenlabs voiceover failed with HTTP {status}")
        if not mp3_bytes:
            raise RuntimeError("elevenlabs voiceover returned an empty response")

        # Lazy import keeps the module light for the dependency-free stub path.
        from ..ingest.ffmpeg import run_ffmpeg

        out_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            raw_mp3 = Path(tmp) / "voiceover_raw.mp3"
            raw_mp3.write_bytes(mp3_bytes)

            # Canonical mono rate; fit the slot when requested (``apad`` pads short speech,
            # ``-t`` trims long) -- ``fit_to_slot=False`` writes the natural-length speech as-is.
            args = ["-i", str(raw_mp3), "-ac", "1", "-ar", str(sample_rate)]
            if fit_to_slot:
                seconds = duration_frames * fps_den / fps_num
                args += ["-af", "apad", "-t", f"{seconds:.6f}"]
            args += ["-c:a", "pcm_s16le", str(out_path)]
            run_ffmpeg(args)


@lru_cache(maxsize=1)
def _sapi_available() -> bool:
    """True iff Windows ``System.Speech`` can be loaded (cached; one cheap probe per process)."""
    if sys.platform != "win32":
        return False
    try:
        proc = subprocess.run(  # noqa: S603
            [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                "Add-Type -AssemblyName System.Speech; 'ok'",
            ],
            capture_output=True,
            text=True,
            timeout=25,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and "ok" in (proc.stdout or "")


def _ps_quote(value: str) -> str:
    """Escape a value for a single-quoted PowerShell string literal."""
    return value.replace("'", "''")


def _sapi_voice_select_script(voice_id: str | None, language: str | None) -> str:
    """PowerShell that best-effort selects a SAPI voice by explicit name or by language culture,
    silently keeping the default voice when the requested one is absent (never breaks synthesis)."""
    if voice_id:
        return f"try {{ $s.SelectVoice('{_ps_quote(voice_id)}') }} catch {{}}; "
    if language:
        lang = _ps_quote(language)
        return (
            "try { $m = $s.GetInstalledVoices() | "
            f"Where-Object {{ $_.Enabled -and $_.VoiceInfo.Culture.Name -like '{lang}*' }} | "
            "Select-Object -First 1; if ($m) { $s.SelectVoice($m.VoiceInfo.Name) } } catch {}; "
        )
    return ""


@lru_cache(maxsize=1)
def list_sapi_voices() -> list[dict[str, str]]:
    """Installed Windows SAPI voices as ``[{name, culture, gender}]`` (empty list off Windows)."""
    if not _sapi_available():
        return []
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "(New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices() | "
        "Where-Object { $_.Enabled } | ForEach-Object { $i = $_.VoiceInfo; "
        '[pscustomobject]@{ name=$i.Name; culture=$i.Culture.Name; gender="$($i.Gender)" } } | '
        "ConvertTo-Json -Compress"
    )
    try:
        proc = subprocess.run(  # noqa: S603
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return []
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    return [
        {
            "name": str(v.get("name", "")),
            "culture": str(v.get("culture", "")),
            "gender": str(v.get("gender", "")),
        }
        for v in data
        if isinstance(v, dict) and v.get("name")
    ]


def resolve_voiceover_backend(
    name: str | None = None,
    *,
    base_url: str | None = None,
) -> VoiceoverBackend:
    chosen = (os.environ.get("LAURA_VOICEOVER_BACKEND") or name or "stub").strip().lower()
    if chosen == "auto":
        # Prefer a real local voice (Windows SAPI) when present, else the dependency-free tone.
        sapi = WindowsSapiVoiceoverBackend()
        return sapi if sapi.available() else StubVoiceoverBackend()
    if chosen == "stub":
        return StubVoiceoverBackend()
    if chosen in {"sapi", "windows", "windows_sapi"}:
        return WindowsSapiVoiceoverBackend()
    if chosen in {"sidecar", "vibevideo"}:
        return SidecarVoiceoverBackend(base_url=base_url)
    if chosen in {"elevenlabs", "el"}:
        return ElevenLabsVoiceoverBackend()
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
