"""Describer + Transcript-Analyst tools (Iteration 4b).

``transcript_window`` (real, over ``get_latest_analysis_run`` + ``get_transcript``) and
``describe_moment`` (injectable VLM backend + frame extractor, graceful when no
model). The window-filter is pure and tested exhaustively; the VLM describe path
is exercised with a fake backend so no Ollama/ffmpeg is needed.
"""

from __future__ import annotations

from typing import Any

import pytest

from laura.db.database import Database
from laura.short_creator import context, describe

# --- _segments_in_window: pure overlap filter -----------------------------------------------


def _seg(start: int, end: int, text: str) -> dict[str, Any]:
    return {"start_frame": start, "end_frame": end, "text": text}


def test_segments_in_window_selects_overlapping() -> None:
    # center=100, window=50 -> window is [50, 150].
    segs = [
        _seg(0, 30, "before"),  # ends below the window low -> excluded
        _seg(90, 110, "overlap lo"),  # included
        _seg(100, 120, "on center"),  # included
        _seg(140, 160, "overlap hi"),  # starts at 140 <= 150 -> included
        _seg(200, 220, "after"),  # starts above the window high -> excluded
    ]
    got = context._segments_in_window(segs, center_frame=100, window_frames=50)
    assert [s["text"] for s in got] == ["overlap lo", "on center", "overlap hi"]


def test_segments_in_window_excludes_outside() -> None:
    segs = [_seg(0, 10, "a"), _seg(1000, 1010, "b")]
    got = context._segments_in_window(segs, center_frame=500, window_frames=100)
    assert got == []


def test_segments_in_window_skips_rows_without_frames() -> None:
    segs: list[dict[str, Any]] = [{"text": "no frames"}, _seg(100, 110, "ok")]
    got = context._segments_in_window(segs, center_frame=105, window_frames=50)
    assert [s["text"] for s in got] == ["ok"]


def test_transcript_window_no_run_is_graceful(db: Database) -> None:
    out = context.transcript_window(db, "no-such-asset", 100)
    assert out["ok"] is False
    assert out["segments"] == []
    assert out["text"] == ""


# --- describe_moment: injectable, graceful --------------------------------------------------


class _FakeBackend:
    def __init__(self, available: bool, text: str) -> None:
        self._available = available
        self._text = text

    def available(self) -> bool:
        return self._available

    def describe(self, frames: list[bytes], prompt: str) -> str:
        return self._text


def test_describe_moment_no_backend_is_graceful(db: Database) -> None:
    out = context.describe_moment(db, "asset", 10, backend=_FakeBackend(False, ""))
    assert out["ok"] is False
    assert out["description"] == ""


def test_describe_moment_no_frame_is_graceful(db: Database) -> None:
    out = context.describe_moment(
        db, "asset", 10, backend=_FakeBackend(True, "x"), extract=lambda _db, _a, _f: []
    )
    assert out["ok"] is False


def test_describe_moment_with_backend_returns_text(db: Database) -> None:
    out = context.describe_moment(
        db,
        "asset",
        10,
        backend=_FakeBackend(True, "a person walking on a beach"),
        extract=lambda _db, _a, _f: [b"jpeg-bytes"],
    )
    assert out["ok"] is True
    assert out["description"] == "a person walking on a beach"


def test_resolve_describe_backend_none_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAURA_VLM_MODEL", raising=False)
    monkeypatch.delenv("LAURA_VLM", raising=False)
    assert describe.resolve_describe_backend() is None
