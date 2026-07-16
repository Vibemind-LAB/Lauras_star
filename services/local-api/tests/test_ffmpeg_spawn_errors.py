"""A failed spawn must name the real cause, not always blame a missing binary.

Live finding: a 55-segment cut produced ``ffmpeg not found: <path>`` for an ffmpeg that
ran fine — Windows had rejected the over-long command line (WinError 206) and subprocess
raised the same ``FileNotFoundError`` a missing binary raises. The message sent an hour
of debugging after a healthy binary.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from laura.ingest import ffmpeg


class _CmdLineTooLong(FileNotFoundError):
    """What Windows raises when the command line exceeds ~32k chars."""

    winerror = 206


def _raise(exc: Exception) -> Any:
    def _run(*_args: Any, **_kwargs: Any) -> Any:
        raise exc

    return _run


def test_over_long_command_line_is_not_reported_as_missing_ffmpeg(monkeypatch: Any) -> None:
    monkeypatch.setattr(subprocess, "run", _raise(_CmdLineTooLong()))

    with pytest.raises(ffmpeg.FFmpegError) as excinfo:
        ffmpeg.run_ffmpeg(["-i", "in.mp4", "out.mp4"])

    message = str(excinfo.value)
    assert "too long" in message
    assert "206" in message
    assert "not found" not in message, "must not blame the binary for a command-line cap"


def test_missing_binary_still_says_not_found(monkeypatch: Any) -> None:
    monkeypatch.setattr(subprocess, "run", _raise(FileNotFoundError(2, "No such file")))
    monkeypatch.setattr(ffmpeg, "ffmpeg_bin", lambda: "definitely-not-a-real-ffmpeg")

    with pytest.raises(ffmpeg.FFmpegError) as excinfo:
        ffmpeg.run_ffmpeg(["-i", "in.mp4", "out.mp4"])

    assert "not found" in str(excinfo.value)


def test_missing_cwd_names_the_directory(monkeypatch: Any, tmp_path: Path) -> None:
    missing = tmp_path / "gone"
    monkeypatch.setattr(subprocess, "run", _raise(FileNotFoundError(2, "No such file")))

    with pytest.raises(ffmpeg.FFmpegError) as excinfo:
        ffmpeg.run_ffmpeg(["-i", "in.mp4", "out.mp4"], cwd=missing)

    message = str(excinfo.value)
    assert "working directory" in message
    assert str(missing) in message


def test_probe_over_long_command_line_is_not_reported_as_missing_ffprobe(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(subprocess, "run", _raise(_CmdLineTooLong()))

    with pytest.raises(ffmpeg.FFmpegError) as excinfo:
        ffmpeg.probe("some.mp4")

    assert "too long" in str(excinfo.value)
    assert "not found" not in str(excinfo.value)
