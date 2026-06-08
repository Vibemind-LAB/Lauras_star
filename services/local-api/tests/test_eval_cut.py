"""Cut-exactness eval — synthetic, deterministic, no ffmpeg.

A frame sequence with one hard change at frame K: frames [0,K) are black, [K,...) are
white. The inter-frame diff d(f) is then zero everywhere except a single spike at f=K
(black->white), so argmax(d) over any window covering K lands exactly on K.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from laura.analysis.eval_cut import (
    BoundaryEval,
    CutEvalReport,
    FrameLoader,
    evaluate_boundaries,
)

H, W = 8, 8
N_FRAMES = 40
HARD_CHANGE_K = 20


def _make_sequence(k: int = HARD_CHANGE_K, n: int = N_FRAMES) -> list[np.ndarray]:
    """Black before frame ``k``, white from ``k`` on — one hard luma jump at ``k``."""
    frames: list[np.ndarray] = []
    for f in range(n):
        val = 0 if f < k else 255
        frames.append(np.full((H, W), val, dtype=np.uint8))
    return frames


def _loader_for(frames: list[np.ndarray]) -> FrameLoader:
    """Return a frame_loader that slices an in-memory sequence over [lo, hi)."""

    def loader(_video: Path | str, lo: int, hi: int) -> list[np.ndarray]:
        lo = max(0, lo)
        hi = min(len(frames), hi)
        return [frames[i] for i in range(lo, hi)]

    return loader


def test_exact_boundary_has_zero_offset() -> None:
    frames = _make_sequence()
    report = evaluate_boundaries(
        Path("dummy.mp4"), [HARD_CHANGE_K], total_frames=N_FRAMES,
        frame_loader=_loader_for(frames),
    )
    assert report.n_boundaries == 1
    assert report.per_boundary[0].offset == 0
    assert report.mean_abs_offset == 0.0
    assert report.pct_exact == 1.0
    assert report.pct_within1 == 1.0
    assert report.exactness_score == 1.0
    assert report.n_imprecise == 0


def test_boundary_two_late_reports_negative_two() -> None:
    # Cut placed at K+2 while the real change is at K => argmax is at K, offset = K-(K+2) = -2.
    frames = _make_sequence()
    report = evaluate_boundaries(
        Path("dummy.mp4"), [HARD_CHANGE_K + 2], total_frames=N_FRAMES,
        frame_loader=_loader_for(frames),
    )
    assert report.n_boundaries == 1
    assert report.per_boundary[0].offset == -2
    assert report.mean_abs_offset == 2.0
    assert report.pct_exact == 0.0
    assert report.pct_within2 == 1.0
    assert report.n_imprecise == 0


def test_boundary_two_early_reports_positive_two() -> None:
    # Cut placed at K-2 while the real change is at K => argmax is at K, offset = +2.
    frames = _make_sequence()
    report = evaluate_boundaries(
        Path("dummy.mp4"), [HARD_CHANGE_K - 2], total_frames=N_FRAMES,
        frame_loader=_loader_for(frames),
    )
    assert report.per_boundary[0].offset == 2
    assert report.mean_abs_offset == 2.0


def test_empty_boundaries_yield_empty_report() -> None:
    report = evaluate_boundaries(
        Path("dummy.mp4"), [], total_frames=N_FRAMES, frame_loader=_loader_for(_make_sequence())
    )
    assert report == CutEvalReport.empty()
    assert report.n_boundaries == 0
    assert report.worst == ()
    assert report.per_boundary == ()


def test_far_offset_counts_as_imprecise() -> None:
    # Real change at K; window large enough to still capture it from a far-off boundary.
    frames = _make_sequence()
    report = evaluate_boundaries(
        Path("dummy.mp4"), [HARD_CHANGE_K + 4], total_frames=N_FRAMES, window=6,
        frame_loader=_loader_for(frames),
    )
    assert report.per_boundary[0].offset == -4
    assert report.n_imprecise == 1
    assert report.pct_within2 == 0.0


def test_aggregates_over_multiple_boundaries() -> None:
    # Two changes (at K and at K2) + a deliberately misplaced third boundary.
    k1, k2 = 12, 28
    frames: list[np.ndarray] = []
    for f in range(N_FRAMES):
        if f < k1:
            val = 0
        elif f < k2:
            val = 255
        else:
            val = 0
        frames.append(np.full((H, W), val, dtype=np.uint8))
    loader = _loader_for(frames)

    # k1 exact, k2 exact, and a boundary at k2+1 (offset -1 -> within1 but not exact).
    report = evaluate_boundaries(
        Path("dummy.mp4"), [k1, k2, k2 + 1], total_frames=N_FRAMES, window=4,
        frame_loader=loader,
    )
    assert report.n_boundaries == 3
    offsets = sorted(e.offset for e in report.per_boundary)
    assert offsets == [-1, 0, 0]
    assert report.pct_exact == 2 / 3
    assert report.pct_within1 == 1.0
    assert report.mean_abs_offset == 1 / 3
    # worst is sorted by |offset| desc; the misplaced one leads.
    assert report.worst[0] == (k2 + 1, -1)


def test_window_must_be_positive() -> None:
    import pytest

    with pytest.raises(ValueError, match="window"):
        evaluate_boundaries(
            Path("dummy.mp4"), [HARD_CHANGE_K], window=0,
            frame_loader=_loader_for(_make_sequence()),
        )


def test_boundary_eval_is_frozen() -> None:
    import pytest

    ev = BoundaryEval(boundary=10, offset=0, peak_diff=1.0, boundary_diff=1.0)
    with pytest.raises(AttributeError):
        ev.offset = 5  # type: ignore[misc]
