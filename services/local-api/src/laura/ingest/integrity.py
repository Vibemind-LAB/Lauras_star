"""Detect corrupt / incomplete media before it enters the editorial pipeline.

A cheap container probe catches truncated/garbled containers; an optional full
decode scan catches broken frames mid-stream. Returns a structured report rather
than raising, so the caller decides what to do.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .ffmpeg import FFmpegError, decode_scan
from .ffmpeg import probe as ffprobe


@dataclass(frozen=True)
class IntegrityReport:
    ok: bool
    container_ok: bool
    decode_errors: int
    detail: str


def verify_decode(path: Path | str, *, full_scan: bool = True) -> IntegrityReport:
    path = Path(path)
    try:
        ffprobe(path)
    except FFmpegError as exc:
        return IntegrityReport(
            ok=False, container_ok=False, decode_errors=0,
            detail=f"container unreadable: {exc}",
        )
    if not full_scan:
        return IntegrityReport(
            ok=True, container_ok=True, decode_errors=0,
            detail="container ok (decode scan skipped)",
        )
    errors = decode_scan(path)
    if errors:
        return IntegrityReport(
            ok=False, container_ok=True, decode_errors=errors,
            detail=f"{errors} decode error(s)",
        )
    return IntegrityReport(ok=True, container_ok=True, decode_errors=0, detail="ok")


def is_media_file(path: Path | str) -> bool:
    """True if ffprobe can read the container AND it has an audio or video stream.

    Used to pick real media out of a torrent's mixed contents (.nfo/.txt/samples).
    """
    try:
        data = ffprobe(path)
    except FFmpegError:
        return False
    streams = data.get("streams", [])
    return any(s.get("codec_type") in ("video", "audio") for s in streams)
