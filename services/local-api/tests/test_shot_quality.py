"""Deterministic per-shot quality metrics + keep decisions."""

from __future__ import annotations

import numpy as np

from laura.analysis.quality import (
    ShotMetrics,
    decide_keep,
    dhash,
    hamming,
    mark_duplicates,
    static_score,
)


def test_black_frame_metrics() -> None:
    frames = [np.zeros((36, 64), dtype=np.uint8) for _ in range(4)]
    m = ShotMetrics.from_frames(frames)
    assert m.black_ratio == 1.0
    assert decide_keep(m)[1] == "black"


def test_static_frames_score_high() -> None:
    f = np.full((36, 64), 120, dtype=np.uint8)
    assert static_score([f, f.copy(), f.copy()]) == 1.0


def test_moving_frames_score_low() -> None:
    a = np.zeros((36, 64), dtype=np.uint8)
    b = np.full((36, 64), 255, dtype=np.uint8)
    assert static_score([a, b, a.copy()]) < 0.2


def test_identical_frames_share_phash() -> None:
    rng = np.arange(72, dtype=np.uint8).reshape(8, 9)
    assert dhash(rng) == dhash(rng.copy())
    assert hamming(dhash(rng), dhash(rng.copy())) == 0


def test_mark_duplicates_keeps_first() -> None:
    rows = [
        {"phash": "ffffffffffffffff", "keep": True, "drop_reason": None},
        {"phash": "ffffffffffffffff", "keep": True, "drop_reason": None},
        {"phash": "0000000000000000", "keep": True, "drop_reason": None},
    ]
    mark_duplicates(rows, dup_hamming=2)
    assert [r["drop_reason"] for r in rows] == [None, "duplicate", None]
