"""Optional remote ASR via a containerised analysis worker (``LAURA_ANALYSIS_URL``).

This module is a drop-in for :func:`laura.analysis.asr.transcribe`: the call site swaps a single
import. When a *healthy* analysis worker is configured the transcription runs in that container
(GPU), which keeps the heavy model off the host CPU/RAM. Otherwise — or if the worker fails
mid-request — it falls back to the in-process faster-whisper path, and if that optional extra is
absent ASR is skipped. This preserves the local-first invariant: **the backend runs without the
container**.

Transport is stdlib ``urllib`` only (mirrors ``laura.ai.voiceover_backend``) — no new dependency.
The HTTP contract is formgleich to the in-process result so both paths share the DB-mapping code:

    GET  /healthz                       -> {"status": "ok", ...}
    POST /transcribe?model_size=&language=
         body: WAV bytes (audio/wav)    -> {"segments": [{text,start_sec,end_sec,confidence,
                                              words:[{text,start_sec,end_sec,confidence}]}]}
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from . import asr
from .types import SegmentResult, WordResult

logger = logging.getLogger(__name__)

# Long audios transcribe for minutes; keep the request timeout generous (override via env).
DEFAULT_TIMEOUT_S = 1800.0
HEALTH_TIMEOUT_S = 3.0


def _analysis_url() -> str | None:
    url = os.environ.get("LAURA_ANALYSIS_URL")
    return url.rstrip("/") if url else None


def _timeout() -> float:
    raw = os.environ.get("LAURA_ANALYSIS_TIMEOUT")
    try:
        return float(raw) if raw else DEFAULT_TIMEOUT_S
    except ValueError:
        return DEFAULT_TIMEOUT_S


def sidecar_healthy(url: str | None = None, *, timeout: float = HEALTH_TIMEOUT_S) -> bool:
    """True when the analysis worker answers ``/healthz`` with ``status == "ok"``."""
    base = url or _analysis_url()
    if not base:
        return False
    try:
        with urllib.request.urlopen(f"{base}/healthz", timeout=timeout) as resp:
            if resp.status != 200:
                return False
            body = json.loads(resp.read().decode("utf-8"))
        return bool(body.get("status") == "ok")
    except Exception:  # noqa: BLE001 - any failure means "not available", fall back
        return False


def asr_available() -> bool:
    """ASR can run if a healthy sidecar is configured OR the local extra is installed."""
    return sidecar_healthy() or asr.faster_whisper_available()


def _parse_segments(payload: dict[str, Any]) -> list[SegmentResult]:
    segments: list[SegmentResult] = []
    for seg in payload.get("segments", []):
        words = [
            WordResult(
                text=str(w["text"]),
                start_sec=float(w["start_sec"]),
                end_sec=float(w["end_sec"]),
                confidence=None if w.get("confidence") is None else float(w["confidence"]),
                is_punctuation=bool(w.get("is_punctuation", False)),
            )
            for w in seg.get("words", [])
        ]
        segments.append(
            SegmentResult(
                text=str(seg["text"]),
                start_sec=float(seg["start_sec"]),
                end_sec=float(seg["end_sec"]),
                confidence=None if seg.get("confidence") is None else float(seg["confidence"]),
                words=words,
            )
        )
    return segments


def transcribe_via_sidecar(
    audio_path: Path | str,
    *,
    model_size: str,
    language: str | None,
    url: str | None = None,
) -> list[SegmentResult]:
    """POST the audio bytes to the worker's ``/transcribe``; raise on any transport/decode error."""
    base = url or _analysis_url()
    if not base:
        raise RuntimeError("no LAURA_ANALYSIS_URL configured")
    data = Path(audio_path).read_bytes()
    params: dict[str, str] = {"model_size": model_size}
    if language:
        params["language"] = language
    req = urllib.request.Request(
        f"{base}/transcribe?{urllib.parse.urlencode(params)}",
        data=data,
        method="POST",
        headers={"Content-Type": "audio/wav"},
    )
    with urllib.request.urlopen(req, timeout=_timeout()) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return _parse_segments(body)


def transcribe(
    audio_path: Path | str,
    *,
    model_size: str = "base",
    language: str | None = None,
) -> list[SegmentResult]:
    """Drop-in for ``asr.transcribe``: prefer a healthy sidecar (GPU), else run in-process.

    On a sidecar failure, fall back to the local path when the extra is installed — a container
    hiccup must not fail an analysis run that could still transcribe on CPU. With no sidecar
    configured, this is exactly the in-process call.
    """
    base = _analysis_url()
    if base and sidecar_healthy(base):
        try:
            return transcribe_via_sidecar(
                audio_path, model_size=model_size, language=language, url=base
            )
        except Exception as exc:  # noqa: BLE001
            if not asr.faster_whisper_available():
                raise
            logger.warning("ASR sidecar failed (%s); falling back to in-process", exc)
    return asr.transcribe(audio_path, model_size=model_size, language=language)
