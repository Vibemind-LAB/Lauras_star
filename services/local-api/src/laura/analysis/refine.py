"""Snap detected shot boundaries to the local frame of maximum visual change.

Detectors place a cut boundary somewhere inside a *gradual* transition (a dissolve or
crossfade), often several frames off the perceptual peak — the frame where the picture
actually changes the most. On HARD cuts the boundary already sits on that peak, so snapping
is a no-op there; it only helps on gradual transitions.

For each boundary ``B`` we decode the consecutive grayscale frames ``[B-window, B+window]``,
compute the inter-frame luma-diff signal ``d(f) = mean(|gray[f] - gray[f-1]|)`` (the same
metric the cut-exactness eval scores against), and move ``B`` to the frame of the local
``argmax(d)``. This is purely defensive: any IO error or unusable window leaves ``B`` exactly
where it was. The frame IO is injected through ``frame_loader`` so tests run without ffmpeg.

The default search half-window is :data:`SNAP_WINDOW` (``4``), deliberately *narrower* than the
shared ``eval_cut.DEFAULT_WINDOW`` (``6``): the ground-truth benchmark in
:mod:`laura.bench.cut_bench` showed a wider window lets ``argmax(d)`` wander off a true hard cut
into compression flicker on flat scenes, and ``4`` minimised that drift with no regression. See
``docs/eval/cut-tuning.md``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .eval_cut import (
    FrameLoader,
    _diff_signal,
    load_gray_frames_ffmpeg,
)

# Snap search half-window. Decoupled from ``eval_cut.DEFAULT_WINDOW`` (the eval/joint scoring
# window, 6) on purpose: the ground-truth benchmark (``laura.bench.cut_bench``) showed that a
# *narrower* snap window is strictly better on hard cuts. On a flat scene (solid colours), a hard
# cut already sits on its luma peak, but a wide window lets ``argmax(d)`` wander backward into
# libx264 compression flicker — drifting the cut 4-10 frames off the true boundary, the wander
# growing with the window. Window 4 minimised that drift across the suite with no regression on
# textured or low-motion scenes, so it is the snap default; eval/joint keep their own 6.
SNAP_WINDOW = 4


def _snap_one(
    video_path: Path | str,
    boundary: int,
    *,
    window: int,
    total_frames: int | None,
    frame_loader: FrameLoader,
) -> int:
    """Return the local diff-peak frame near ``boundary``; ``boundary`` unchanged on failure.

    Mirrors ``eval_cut._evaluate_one``: search candidate cut frames in
    ``[boundary-window, boundary+window]`` (clamped to the valid range), load one extra
    predecessor so ``d`` is defined on the first candidate, and pick ``argmax(d)``.
    """
    lo = boundary - window
    hi = boundary + window  # inclusive candidate upper bound
    if total_frames is not None:
        hi = min(hi, total_frames - 1)
    first_candidate = max(lo, 1)  # d undefined at frame 0 (no predecessor)
    if first_candidate > hi:
        return boundary
    load_lo = first_candidate - 1  # one predecessor for the first diff
    load_hi = hi + 1  # end-exclusive
    try:
        frames = frame_loader(video_path, load_lo, load_hi)
    except Exception:
        # IO / decode error -> leave the boundary untouched (defensive).
        return boundary
    if len(frames) < 2:
        return boundary

    diffs = _diff_signal(frames)  # diffs[k] corresponds to candidate frame load_lo+1+k
    k = int(np.argmax(diffs))
    return load_lo + 1 + k


def snap_boundaries(
    video_path: Path | str,
    boundaries: list[int],
    *,
    window: int = SNAP_WINDOW,
    total_frames: int | None = None,
    frame_loader: FrameLoader = load_gray_frames_ffmpeg,
) -> list[int]:
    """Snap each boundary to the local ``argmax`` of the inter-frame luma-diff signal.

    For every ``B`` the consecutive grayscale frames ``[B-window, B+window]`` are decoded and
    ``B`` moves to the frame of maximum ``d(f) = mean(|gray[f] - gray[f-1]|)`` within that
    window. On a hard cut the peak already sits on ``B`` so the value is unchanged; on a
    gradual transition ``B`` slides onto the true transition frame.

    The result preserves the input order and drops duplicates that arise when two adjacent
    boundaries snap onto the same frame (the first wins). Out-of-range windows and IO/decode
    errors leave the affected boundary exactly where it was — snapping never fails a boundary,
    it only ever refines one.
    """
    if window < 1:
        raise ValueError("window must be >= 1")

    snapped: list[int] = []
    seen: set[int] = set()
    for b in boundaries:
        s = _snap_one(
            video_path, b, window=window, total_frames=total_frames, frame_loader=frame_loader
        )
        if s in seen:
            continue
        seen.add(s)
        snapped.append(s)
    return snapped
