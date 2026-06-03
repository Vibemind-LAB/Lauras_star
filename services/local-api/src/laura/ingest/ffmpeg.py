"""Thin wrappers around the ffmpeg/ffprobe binaries.

Binaries are resolved from ``LAURA_FFMPEG``/``LAURA_FFPROBE`` env vars, else from
PATH. All calls are list-arg subprocess calls (no shell) for safety/portability.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


class FFmpegError(RuntimeError):
    """Raised when ffmpeg/ffprobe exits non-zero or is not found."""


def ffprobe_bin() -> str:
    return os.environ.get("LAURA_FFPROBE") or shutil.which("ffprobe") or "ffprobe"


def ffmpeg_bin() -> str:
    return os.environ.get("LAURA_FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"


def probe(path: Path | str) -> dict[str, Any]:
    """Return the parsed ffprobe JSON for ``path`` (format + streams)."""
    cmd = [
        ffprobe_bin(),
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603
    except FileNotFoundError as exc:
        raise FFmpegError(f"ffprobe not found: {ffprobe_bin()}") from exc
    if proc.returncode != 0:
        raise FFmpegError(proc.stderr.strip() or "ffprobe failed")
    data: dict[str, Any] = json.loads(proc.stdout)
    return data


def run_ffmpeg(args: list[str]) -> None:
    """Run ffmpeg with the given args (``-y`` and banner suppression are added)."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603
    except FileNotFoundError as exc:
        raise FFmpegError(f"ffmpeg not found: {ffmpeg_bin()}") from exc
    if proc.returncode != 0:
        # keep the tail of stderr — ffmpeg errors are most informative at the end
        raise FFmpegError((proc.stderr or "ffmpeg failed").strip()[-2000:])


def decode_scan(path: Path | str) -> int:
    """Full decode pass; returns the count of decode-error lines ffmpeg emitted.

    ``ffmpeg -v error -xerror -i <path> -f null -`` decodes every frame and prints one
    line per decode error. ``-xerror`` makes it bail on the first error, so a clean file
    decodes fully while a corrupt one returns quickly. Zero => clean.
    """
    cmd = [
        ffmpeg_bin(), "-hide_banner", "-nostdin", "-v", "error", "-xerror",
        "-i", str(path), "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603
    except FileNotFoundError as exc:
        raise FFmpegError(f"ffmpeg not found: {ffmpeg_bin()}") from exc
    stderr = (proc.stderr or "").strip()
    error_lines = len([line for line in stderr.splitlines() if line.strip()])
    if proc.returncode != 0:
        # A non-zero exit means ffmpeg aborted decoding (``-xerror``); count it as at
        # least one error even when stderr was suppressed.
        return max(error_lines, 1)
    return error_lines
