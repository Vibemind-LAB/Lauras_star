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
    """Raised when ffmpeg/ffprobe exits non-zero or cannot be started."""


# Windows rejects a command line past ~32k chars with this, and subprocess surfaces it as
# the very same FileNotFoundError a missing binary raises.
_WINERROR_CMDLINE_TOO_LONG = 206


def _spawn_failure(exc: OSError, binary: str, *, cwd: Path | str | None = None) -> FFmpegError:
    """Name why a spawn failed instead of always blaming a missing binary.

    ``subprocess`` raises ``FileNotFoundError`` for a missing binary AND for an over-long
    command line; a missing ``cwd`` looks similar. Reporting "not found" for all three
    cost a live session an hour of hunting an ffmpeg that was healthy the whole time.
    """
    if getattr(exc, "winerror", None) == _WINERROR_CMDLINE_TOO_LONG:
        return FFmpegError(
            f"command line too long (WinError {_WINERROR_CMDLINE_TOO_LONG}): the OS caps it "
            f"near 32k chars — pass the filtergraph as a file (-filter_complex_script) "
            f"instead of inline. binary: {binary}"
        )
    if cwd is not None and not Path(cwd).is_dir():
        return FFmpegError(f"working directory does not exist: {cwd} (binary: {binary})")
    if not Path(binary).is_file() and shutil.which(binary) is None:
        return FFmpegError(f"not found: {binary}")
    return FFmpegError(f"cannot start {binary}: {exc}")


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
    except OSError as exc:
        raise _spawn_failure(exc, ffprobe_bin()) from exc
    if proc.returncode != 0:
        raise FFmpegError(proc.stderr.strip() or "ffprobe failed")
    data: dict[str, Any] = json.loads(proc.stdout)
    return data


def run_ffmpeg(args: list[str], *, cwd: Path | str | None = None) -> None:
    """Run ffmpeg with the given args (``-y`` and banner suppression are added).

    ``cwd`` sets the subprocess working directory. This lets a filtergraph
    reference helper files (e.g. drawtext ``textfile=``) by *basename*, which
    sidesteps the brittle Windows drive-colon escaping that ``textfile=`` (unlike
    ``fontfile=``) does not accept. Absolute input/output paths are unaffected.
    """
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)  # noqa: S603
    except OSError as exc:
        raise _spawn_failure(exc, ffmpeg_bin(), cwd=cwd) from exc
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
    except OSError as exc:
        raise _spawn_failure(exc, ffmpeg_bin()) from exc
    stderr = (proc.stderr or "").strip()
    error_lines = len([line for line in stderr.splitlines() if line.strip()])
    if proc.returncode != 0:
        # A non-zero exit means ffmpeg aborted decoding (``-xerror``); count it as at
        # least one error even when stderr was suppressed.
        return max(error_lines, 1)
    return error_lines
