"""Tests for apply_overlay_precedence — pure, no DB, no I/O."""
from __future__ import annotations

from typing import Any

from laura.editing.overlays import apply_overlay_precedence

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base(
    asset_id: str,
    src_in: int,
    src_out: int,
    seq_in: int,
    seq_out: int,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "asset_id": asset_id,
        "src_in_frame": src_in,
        "src_out_frame_exclusive": src_out,
        "seq_in_frame": seq_in,
        "seq_out_frame_exclusive": seq_out,
        "lane": 0,
        "role": "base",
        "speed_num": 1,
        "speed_den": 1,
        **extra,
    }


def _overlay(
    asset_id: str,
    src_in: int,
    src_out: int,
    seq_in: int,
    seq_out: int,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "asset_id": asset_id,
        "src_in_frame": src_in,
        "src_out_frame_exclusive": src_out,
        "seq_in_frame": seq_in,
        "seq_out_frame_exclusive": seq_out,
        "lane": 1,
        "role": "replace",
        "speed_num": 1,
        "speed_den": 1,
        **extra,
    }


def _to_tuples(rows: list[dict[str, Any]]) -> list[tuple[str, int, int, int, int]]:
    return [
        (
            r["asset_id"],
            r["src_in_frame"],
            r["src_out_frame_exclusive"],
            r["seq_in_frame"],
            r["seq_out_frame_exclusive"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Test 1: Canonical worked example
# ---------------------------------------------------------------------------

def test_canonical_worked_example() -> None:
    """Middle overlay splits base into left + right sub-segments with correct src mapping."""
    base = [_base("A", 100, 130, 0, 30)]
    ovl = [_overlay("B", 0, 10, 10, 20)]
    result = apply_overlay_precedence(base, ovl)
    assert _to_tuples(result) == [
        ("A", 100, 110, 0, 10),
        ("B", 0, 10, 10, 20),
        ("A", 120, 130, 20, 30),
    ]


# ---------------------------------------------------------------------------
# Test 2: Overlay equals entire base range
# ---------------------------------------------------------------------------

def test_overlay_covers_full_base() -> None:
    """Overlay spanning the whole base range leaves no base segments."""
    base = [_base("A", 100, 130, 0, 30)]
    ovl = [_overlay("B", 0, 30, 0, 30)]
    result = apply_overlay_precedence(base, ovl)
    assert _to_tuples(result) == [("B", 0, 30, 0, 30)]


# ---------------------------------------------------------------------------
# Test 3: No overlays
# ---------------------------------------------------------------------------

def test_no_overlays_returns_base_unchanged() -> None:
    """Empty overlay list returns base rows with identical content."""
    base = [_base("A", 100, 130, 0, 30)]
    result = apply_overlay_precedence(base, [])
    assert _to_tuples(result) == [("A", 100, 130, 0, 30)]


# ---------------------------------------------------------------------------
# Test 4: Overlay at base START — no zero-length left segment
# ---------------------------------------------------------------------------

def test_overlay_at_start() -> None:
    """Overlay at [0,10) leaves only the right base piece; no empty left segment."""
    base = [_base("A", 100, 130, 0, 30)]
    ovl = [_overlay("B", 0, 10, 0, 10)]
    result = apply_overlay_precedence(base, ovl)
    assert _to_tuples(result) == [
        ("B", 0, 10, 0, 10),
        ("A", 110, 130, 10, 30),
    ]


# ---------------------------------------------------------------------------
# Test 5: Overlay at base END — no zero-length right segment
# ---------------------------------------------------------------------------

def test_overlay_at_end() -> None:
    """Overlay at [20,30) leaves only the left base piece; no empty right segment."""
    base = [_base("A", 100, 130, 0, 30)]
    ovl = [_overlay("B", 0, 10, 20, 30)]
    result = apply_overlay_precedence(base, ovl)
    assert _to_tuples(result) == [
        ("A", 100, 120, 0, 20),
        ("B", 0, 10, 20, 30),
    ]


# ---------------------------------------------------------------------------
# Test 6: Two non-overlapping overlays on one base
# ---------------------------------------------------------------------------

def test_two_non_overlapping_overlays() -> None:
    """Two overlays at [5,10) and [20,25) split base into 3 pieces + 2 overlays, 5 rows total."""
    base = [_base("A", 100, 130, 0, 30)]
    ovl = [
        _overlay("B", 0, 5, 5, 10),
        _overlay("C", 0, 5, 20, 25),
    ]
    result = apply_overlay_precedence(base, ovl)
    # Expected base pieces: [0,5) [10,20) [25,30)
    # src mapping (b_seq_in=0, b_src_in=100):
    #   [0,5)   -> src [100,105)
    #   [10,20) -> src [110,120)
    #   [25,30) -> src [125,130)
    assert _to_tuples(result) == [
        ("A", 100, 105, 0, 5),
        ("B", 0, 5, 5, 10),
        ("A", 110, 120, 10, 20),
        ("C", 0, 5, 20, 25),
        ("A", 125, 130, 25, 30),
    ]
    assert len(result) == 5


# ---------------------------------------------------------------------------
# Test 7: Extra fields are preserved on surviving sub-segments
# ---------------------------------------------------------------------------

def test_extra_fields_preserved_in_subsegment() -> None:
    """A surviving base sub-segment carries all original fields (speed_num, lane, etc.)."""
    base_row = _base("A", 100, 130, 0, 30)
    ovl = [_overlay("B", 0, 10, 10, 20)]
    result = apply_overlay_precedence([base_row], ovl)

    # Find the left sub-segment (seq 0..10)
    left = next(r for r in result if r["asset_id"] == "A" and r["seq_in_frame"] == 0)
    assert left["lane"] == 0
    assert left["speed_num"] == 1
    assert left["speed_den"] == 1
    assert left["role"] == "base"
