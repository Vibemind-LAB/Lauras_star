"""Constructed voice track: offsets, merged sidecar, real ffmpeg concat."""

import subprocess
from pathlib import Path

import pytest

from laura.short_creator.voice_concat import (
    concat_with_gaps,
    line_offsets,
    merge_word_timings,
    probe_duration_s,
)


def _tone(path: Path, seconds: float) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-q:a", "9", str(path)],
        check=True, capture_output=True,
    )


def test_line_offsets_cumulative_with_gaps() -> None:
    assert line_offsets([2.0, 3.0, 1.0], gap_s=0.35) == pytest.approx([0.0, 2.35, 5.7])


def test_merge_word_timings_shifts_by_offset() -> None:
    merged = merge_word_timings(
        [
            [{"text": "hallo", "start_s": 0.0, "end_s": 0.4}],
            [{"text": "welt", "start_s": 0.1, "end_s": 0.5}],
        ],
        offsets=[0.0, 2.35],
    )
    assert merged == {
        "words": [
            {"text": "hallo", "start_s": 0.0, "end_s": 0.4},
            {"text": "welt", "start_s": 2.45, "end_s": 2.85},
        ]
    }


def test_merge_skips_missing_line_timings() -> None:
    merged = merge_word_timings([[], [{"text": "x", "start_s": 0.0, "end_s": 0.2}]],
                                offsets=[0.0, 1.0])
    assert merged == {"words": [{"text": "x", "start_s": 1.0, "end_s": 1.2}]}


def test_concat_with_gaps_duration(tmp_path: Path) -> None:
    a, b, out = tmp_path / "a.mp3", tmp_path / "b.mp3", tmp_path / "out.mp3"
    _tone(a, 1.0)
    _tone(b, 0.5)
    concat_with_gaps([a, b], gap_s=0.35, out_path=out)
    total = probe_duration_s(out)
    assert total == pytest.approx(1.0 + 0.35 + 0.5, abs=0.15)
