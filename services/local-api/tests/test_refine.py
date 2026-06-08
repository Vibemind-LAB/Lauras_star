"""Unit tests for :func:`laura.analysis.refine.snap_boundaries`.

Synthetic, deterministic, no ffmpeg. The frame IO is injected via ``frame_loader`` so every
test feeds an in-memory grayscale sequence whose inter-frame diff signal has a single known
peak. Snapping must move a misplaced boundary onto that peak, leave a boundary already on the
peak unchanged, and never blow up on out-of-range windows.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from laura.analysis.eval_cut import FrameLoader
from laura.analysis.refine import snap_boundaries

H, W = 8, 8
N_FRAMES = 60


def _step_sequence(k: int, n: int = N_FRAMES) -> list[np.ndarray]:
    """Black before frame ``k``, white from ``k`` on -> a single diff peak at frame ``k``."""
    return [
        np.full((H, W), 0 if f < k else 255, dtype=np.uint8) for f in range(n)
    ]


def _loader_for(frames: list[np.ndarray]) -> FrameLoader:
    """A frame_loader slicing an in-memory sequence over [lo, hi) (clamped, like ffmpeg)."""

    def loader(_video: Path | str, lo: int, hi: int) -> list[np.ndarray]:
        lo = max(0, lo)
        hi = min(len(frames), hi)
        return [frames[i] for i in range(lo, hi)]

    return loader


def test_misplaced_boundary_snaps_to_peak() -> None:
    """A boundary 4 frames before the true change snaps onto the change frame."""
    peak = 30
    loader = _loader_for(_step_sequence(peak))
    out = snap_boundaries(
        Path("x.mp4"), [peak - 4], total_frames=N_FRAMES, frame_loader=loader
    )
    assert out == [peak]


def test_boundary_already_at_peak_unchanged() -> None:
    """A boundary already on the diff peak is a no-op (hard-cut case)."""
    peak = 30
    loader = _loader_for(_step_sequence(peak))
    out = snap_boundaries(
        Path("x.mp4"), [peak], total_frames=N_FRAMES, frame_loader=loader
    )
    assert out == [peak]


def test_boundary_after_peak_snaps_back() -> None:
    """A boundary placed past the true change snaps back onto it."""
    peak = 30
    loader = _loader_for(_step_sequence(peak))
    out = snap_boundaries(
        Path("x.mp4"), [peak + 3], total_frames=N_FRAMES, frame_loader=loader
    )
    assert out == [peak]


def test_two_boundaries_snapping_to_same_frame_dedup() -> None:
    """Two near boundaries that both snap onto the same peak collapse to one, order kept."""
    peak = 30
    loader = _loader_for(_step_sequence(peak))
    out = snap_boundaries(
        Path("x.mp4"), [peak - 2, peak + 2], total_frames=N_FRAMES, frame_loader=loader
    )
    assert out == [peak]  # deduped


def test_order_preserved_for_distinct_peaks() -> None:
    """Independent boundaries keep input order after snapping."""
    # Two changes: black->white at k1, white->black at k2.
    k1, k2 = 15, 40
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
    out = snap_boundaries(
        Path("x.mp4"), [k1 + 2, k2 - 1], total_frames=N_FRAMES, frame_loader=loader
    )
    assert out == [k1, k2]


def test_out_of_range_boundary_left_unchanged() -> None:
    """A boundary whose clamped window has no valid candidate is returned untouched."""
    loader = _loader_for(_step_sequence(30))
    # total_frames=1 clamps hi to 0, below first_candidate=1 -> no candidate -> unchanged.
    out = snap_boundaries(Path("x.mp4"), [5], total_frames=1, frame_loader=loader)
    assert out == [5]


def test_single_frame_window_left_unchanged() -> None:
    """A loader returning <2 frames (no diff possible) leaves the boundary in place."""

    def one_frame(_video: Path | str, _lo: int, _hi: int) -> list[np.ndarray]:
        return [np.zeros((H, W), dtype=np.uint8)]

    out = snap_boundaries(
        Path("x.mp4"), [30], total_frames=N_FRAMES, frame_loader=one_frame
    )
    assert out == [30]


def test_io_error_leaves_boundary_unchanged() -> None:
    """A frame_loader that raises -> the boundary is defensively left in place."""

    def boom(_video: Path | str, _lo: int, _hi: int) -> list[np.ndarray]:
        raise OSError("decode exploded")

    out = snap_boundaries(
        Path("x.mp4"), [30], total_frames=N_FRAMES, frame_loader=boom
    )
    assert out == [30]


def test_empty_boundaries() -> None:
    out = snap_boundaries(
        Path("x.mp4"), [], total_frames=N_FRAMES, frame_loader=_loader_for(_step_sequence(30))
    )
    assert out == []


def test_window_must_be_positive() -> None:
    with pytest.raises(ValueError, match="window"):
        snap_boundaries(
            Path("x.mp4"), [30], window=0, frame_loader=_loader_for(_step_sequence(30))
        )
