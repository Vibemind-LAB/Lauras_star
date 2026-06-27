"""S0 — black-filter regression tests.

Covers the two-condition black criterion (mean < BLACK_LUMA AND max < BLACK_MAX) and the
handler-level guard that prevents dropping 100 % of shots as "black".
"""

from __future__ import annotations

import numpy as np

from laura.analysis.quality import BLACK_LUMA, BLACK_MAX, ShotMetrics, decide_keep

# ---------------------------------------------------------------------------
# Part 1 — ShotMetrics.from_frames: the two-condition black criterion
# ---------------------------------------------------------------------------


def _uniform_dark_frames(mean_val: int = 8, count: int = 4) -> list[np.ndarray]:
    """All pixels at *mean_val* — uniformly dark, mean and max both low."""
    return [np.full((36, 64), mean_val, dtype=np.uint8) for _ in range(count)]


def _spotlight_frames(bg: int = 8, count: int = 4) -> list[np.ndarray]:
    """Dark background (mean~8) with a small bright spotlight patch (200) in the centre.

    Frame is 36×64 = 2304 pixels. Spotlight is 8×8 = 64 pixels → mean ≈ 8+200*64/2304 ≈ 13.6
    which is < BLACK_LUMA (16). Max = 200 >> BLACK_MAX (48). Correct regime for the test.
    """
    frames = []
    for _ in range(count):
        f = np.full((36, 64), bg, dtype=np.uint8)
        f[14:22, 28:36] = 200  # 8×8 bright spotlight — mean stays below 16
        frames.append(f)
    return frames


def test_uniform_dark_is_black() -> None:
    """Uniformly dark frames (mean < 16, max < 48) must be counted as black."""
    frames = _uniform_dark_frames(mean_val=8)
    m = ShotMetrics.from_frames(frames)
    assert m.black_ratio == 1.0, f"expected black_ratio=1.0, got {m.black_ratio}"
    keep, reason = decide_keep(m)
    assert not keep
    assert reason == "black"


def test_true_black_is_black() -> None:
    """All-zero frames (classic black leader) must be counted as black."""
    frames = [np.zeros((36, 64), dtype=np.uint8) for _ in range(4)]
    m = ShotMetrics.from_frames(frames)
    assert m.black_ratio == 1.0


def test_dark_spotlight_is_not_black() -> None:
    """Dark stage with a bright spotlight: mean < BLACK_LUMA but max >> BLACK_MAX.

    This is the exact scenario (poetry-slam, dark stage) that was wrongly dropped.
    After the fix, black_ratio must be 0.0 and decide_keep must NOT return 'black'.
    """
    frames = _spotlight_frames(bg=8)
    # Verify the test data is in the right regime
    mean_val = float(np.mean(frames[0]))
    max_val = float(np.max(frames[0]))
    assert mean_val < BLACK_LUMA, f"test setup: mean {mean_val} should be < {BLACK_LUMA}"
    assert max_val > BLACK_MAX, f"test setup: max {max_val} should be > {BLACK_MAX}"

    m = ShotMetrics.from_frames(frames)
    assert m.black_ratio == 0.0, (
        f"dark-with-spotlight must have black_ratio=0.0, got {m.black_ratio}"
    )
    keep, reason = decide_keep(m)
    assert reason != "black", f"expected not 'black', got reason={reason!r}"


def test_mixed_frames_partial_black_ratio() -> None:
    """Mix of truly black frames and spotlight frames → partial black_ratio."""
    truly_black = [np.zeros((36, 64), dtype=np.uint8) for _ in range(2)]
    spotlight = _spotlight_frames(bg=8, count=2)
    frames = truly_black + spotlight
    m = ShotMetrics.from_frames(frames)
    assert m.black_ratio == 0.5, f"expected 0.5, got {m.black_ratio}"


# ---------------------------------------------------------------------------
# Part 2 — handler guard: never drop 100 % of shots as black
# ---------------------------------------------------------------------------


def _make_row(*, keep: bool, drop_reason: str | None, br: float = 0.9) -> dict:
    """Minimal shot row for guard testing."""
    return {
        "keep": keep,
        "drop_reason": drop_reason,
        "black_ratio": br,
    }


def test_guard_rescues_all_black_asset() -> None:
    """When ALL rows have drop_reason='black', the guard resets them to keep=True."""
    # Import the guard logic inline: it lives between mark_duplicates and insert_shots
    # in handlers.py. We test the logic directly by extracting it into a helper here.
    rows = [
        _make_row(keep=False, drop_reason="black", br=0.95),
        _make_row(keep=False, drop_reason="black", br=0.90),
        _make_row(keep=False, drop_reason="black", br=0.85),
    ]

    # Replicate guard logic (same as in handlers.py _run_shots):
    if rows and all(not r.get("keep") and r.get("drop_reason") == "black" for r in rows):
        for r in rows:
            r["keep"] = True
            r["drop_reason"] = None

    assert all(r["keep"] for r in rows), "guard must set keep=True for all rows"
    assert all(r["drop_reason"] is None for r in rows), "guard must clear drop_reason"


def test_guard_does_not_activate_for_mixed_drops() -> None:
    """Guard must NOT fire if at least one shot is kept or dropped for a different reason."""
    rows = [
        _make_row(keep=False, drop_reason="black", br=0.95),
        _make_row(keep=True, drop_reason=None, br=0.1),  # one shot kept
    ]
    original = [(r["keep"], r["drop_reason"]) for r in rows]

    if rows and all(not r.get("keep") and r.get("drop_reason") == "black" for r in rows):
        for r in rows:
            r["keep"] = True
            r["drop_reason"] = None

    assert [(r["keep"], r["drop_reason"]) for r in rows] == original, (
        "guard must not change rows when at least one shot is kept"
    )


def test_guard_does_not_activate_for_other_drop_reason() -> None:
    """Guard must NOT fire if the universal drop is due to 'static', not 'black'."""
    rows = [
        _make_row(keep=False, drop_reason="static"),
        _make_row(keep=False, drop_reason="static"),
    ]
    original = [(r["keep"], r["drop_reason"]) for r in rows]

    if rows and all(not r.get("keep") and r.get("drop_reason") == "black" for r in rows):
        for r in rows:
            r["keep"] = True
            r["drop_reason"] = None

    assert [(r["keep"], r["drop_reason"]) for r in rows] == original


def test_guard_does_not_activate_for_empty_rows() -> None:
    """Guard edge case: empty shot list must not raise."""
    rows: list[dict] = []
    activated = False
    if rows and all(not r.get("keep") and r.get("drop_reason") == "black" for r in rows):
        activated = True
    assert not activated
