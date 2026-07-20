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


def test_static_drop_only_for_short_shots() -> None:
    # A textured-but-frozen shot: static==1.0, not black, not blurry (checkerboard).
    f = ((np.indices((36, 64)).sum(0) % 2) * 255).astype(np.uint8)
    m = ShotMetrics.from_frames([f, f.copy(), f.copy()])
    assert m.static == 1.0
    # Short freeze/glitch -> dropped as static.
    assert decide_keep(m, length_frames=10)[1] == "static"
    # Long held shot (intentional content) -> kept despite low motion.
    assert decide_keep(m, length_frames=100)[0] is True


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


# --- duplicate tolerance vs motion ------------------------------------------------------
# Live finding (10-min screen recording, AgentFarm): a 5-minute "Missing Information" prompt
# was dropped as a duplicate of an unrelated MCP-server picker — distance 6, threshold 6. The
# two screens share nothing but dark chrome and a header band, and at 9x8 the text that tells
# them apart is long gone. Measured on that material: genuine duplicates score 0, DIFFERENT
# screens score 6-8. Raising the hash resolution does not help (it was measured too: 256- and
# 1024-bit variants put the same pair at 9.8-12.5% either way). The tolerance is the problem.

_ZERO = "0000000000000000"
_SIX_BITS_OFF = "000000000000003f"  # 0b111111 -> hamming 6 from _ZERO
_TWO_BITS_OFF = "0000000000000003"  # hamming 2 from _ZERO
_FROZEN = 0.9998  # a screen recording; real values measured 0.9928-0.9999
_MOVING = 0.5


def _row(phash: str, static: float | None) -> dict[str, object]:
    return {"phash": phash, "keep": True, "drop_reason": None, "static_score": static}


def test_two_different_frozen_screens_are_not_duplicates() -> None:
    """THE live bug: distinct UI screens sit ~6 bits apart and must survive."""
    rows = [_row(_ZERO, _FROZEN), _row(_SIX_BITS_OFF, _FROZEN)]

    mark_duplicates(rows)

    assert [r["drop_reason"] for r in rows] == [None, None]
    assert all(r["keep"] for r in rows)


def test_a_genuinely_repeated_frozen_screen_is_still_a_duplicate() -> None:
    """Frozen footage repeats pixel-exactly, so the strict tolerance still catches it."""
    rows = [_row(_ZERO, _FROZEN), _row(_ZERO, _FROZEN)]

    mark_duplicates(rows)

    assert [r["drop_reason"] for r in rows] == [None, "duplicate"]


def test_moving_footage_keeps_the_noise_tolerant_threshold() -> None:
    """Camera duplicates never match exactly (sensor noise) — 6 bits of slack must stay."""
    rows = [_row(_ZERO, _MOVING), _row(_SIX_BITS_OFF, _MOVING)]

    mark_duplicates(rows)

    assert [r["drop_reason"] for r in rows] == [None, "duplicate"]


def test_a_frozen_shot_against_a_moving_one_uses_the_tolerant_threshold() -> None:
    """The strict rule needs BOTH shots frozen; a mixed pair keeps today's behaviour."""
    rows = [_row(_ZERO, _MOVING), _row(_SIX_BITS_OFF, _FROZEN)]

    mark_duplicates(rows)

    assert [r["drop_reason"] for r in rows] == [None, "duplicate"]


def test_a_frozen_near_duplicate_within_the_strict_tolerance_is_still_dropped() -> None:
    """Encoder noise on an otherwise identical screen must not defeat the dedup."""
    rows = [_row(_ZERO, _FROZEN), _row(_TWO_BITS_OFF, _FROZEN)]

    mark_duplicates(rows)

    assert [r["drop_reason"] for r in rows] == [None, "duplicate"]


def test_a_missing_static_score_behaves_exactly_as_before() -> None:
    """Metrics can fail; an unknown motion level must not silently tighten the rule."""
    rows = [_row(_ZERO, None), _row(_SIX_BITS_OFF, None)]

    mark_duplicates(rows)

    assert [r["drop_reason"] for r in rows] == [None, "duplicate"]
