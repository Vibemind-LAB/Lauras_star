"""Self-supervised cut-exactness evaluation (no ground-truth labels needed).

A detected shot boundary at source frame ``B`` is *frame-exact* when the visual change
peaks exactly at ``B``. The inter-frame luma-difference signal

    d(f) = mean(|gray[f] - gray[f-1]|)

measures how much the picture changed between ``f-1`` and ``f``. At a true hard cut, ``d``
has a sharp maximum on the first frame of the new shot. So for each boundary ``B`` we look
at a small window ``[B-window, B+window]``, take ``argmax(d)`` over it, and define

    offset = argmax_index - B

``offset == 0`` means the cut sits on the true transition frame; a large ``|offset|`` means
the boundary is placed ``offset`` frames away from where the picture actually changes. This
is a *self-supervised* exactness metric: it needs no human labels, only the pixels.

This is an evaluation tool, not a hot path — decoding a handful of consecutive frames per
boundary via ffmpeg is fine here. The frame IO is injected through ``frame_loader`` so tests
can feed in-memory frames without touching disk.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# Small grayscale samples are plenty to locate a luma jump; keep decode cheap.
SAMPLE_W, SAMPLE_H = 64, 36
DEFAULT_WINDOW = 6

# (video_path, lo_frame, hi_frame_exclusive) -> consecutive HxW uint8 grayscale frames,
# one per index in [lo, hi). The default implementation shells out to ffmpeg.
FrameLoader = Callable[[Path | str, int, int], "list[np.ndarray]"]


def load_gray_frames_ffmpeg(
    video: Path | str, lo: int, hi: int, *, w: int = SAMPLE_W, h: int = SAMPLE_H
) -> list[np.ndarray]:
    """Decode the *consecutive* grayscale frames ``[lo, hi)`` as HxW uint8 arrays.

    Unlike ``quality._sample_gray_frames`` (which samples evenly across a shot), this grabs
    every real frame in the range — exactness needs the true per-frame diff, not a sample.
    """
    if hi <= lo:
        return []
    expr = f"between(n\\,{lo}\\,{hi - 1})"
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(video),
        "-vf", f"select='{expr}',scale={w}:{h},format=gray",
        "-vsync", "0", "-frames:v", str(hi - lo), "-f", "rawvideo", "-",
    ]
    out = subprocess.run(cmd, capture_output=True, check=True).stdout  # noqa: S603
    frame_bytes = w * h
    frames: list[np.ndarray] = []
    for off in range(0, len(out) - frame_bytes + 1, frame_bytes):
        frames.append(
            np.frombuffer(out[off : off + frame_bytes], dtype=np.uint8).reshape(h, w)
        )
    return frames


def _diff_signal(frames: list[np.ndarray]) -> list[float]:
    """``d(f) = mean(|gray[f] - gray[f-1]|)`` for each adjacent pair (len == len(frames)-1)."""
    return [
        float(np.mean(np.abs(frames[i].astype(np.int16) - frames[i - 1].astype(np.int16))))
        for i in range(1, len(frames))
    ]


@dataclass(frozen=True)
class BoundaryEval:
    """Exactness result for a single detected boundary."""

    boundary: int          # the detected cut frame B (source-frame index)
    offset: int            # argmax(d) - B; 0 == frame-exact, sign = direction of true cut
    peak_diff: float       # d at the argmax frame (strength of the strongest local change)
    boundary_diff: float   # d at B itself (how strong the change is where the cut was placed)


@dataclass(frozen=True)
class CutEvalReport:
    """Aggregate cut-exactness over all internal boundaries of one asset."""

    n_boundaries: int
    mean_abs_offset: float
    pct_exact: float            # share with |offset| == 0
    pct_within1: float          # share with |offset| <= 1
    pct_within2: float          # share with |offset| <= 2
    n_imprecise: int            # count with |offset| > 2
    exactness_score: float      # single [0,1] headline score (== pct_within1)
    worst: tuple[tuple[int, int], ...]   # top-10 (boundary, offset) by |offset|, desc
    per_boundary: tuple[BoundaryEval, ...]

    @classmethod
    def empty(cls) -> CutEvalReport:
        return cls(
            n_boundaries=0,
            mean_abs_offset=0.0,
            pct_exact=0.0,
            pct_within1=0.0,
            pct_within2=0.0,
            n_imprecise=0,
            exactness_score=0.0,
            worst=(),
            per_boundary=(),
        )


def _evaluate_one(
    video_path: Path | str,
    boundary: int,
    *,
    window: int,
    total_frames: int | None,
    frame_loader: FrameLoader,
) -> BoundaryEval | None:
    """Locate the local diff-peak around ``boundary``; ``None`` if no usable signal."""
    # Search window of candidate cut frames, clamped to the valid frame range. We need one
    # extra frame *before* the first candidate so d(lo) = mean(|gray[lo]-gray[lo-1]|) exists.
    lo = boundary - window
    hi = boundary + window  # inclusive candidate upper bound
    if total_frames is not None:
        hi = min(hi, total_frames - 1)
    first_candidate = max(lo, 1)        # d undefined at frame 0 (no predecessor)
    if first_candidate > hi:
        return None
    load_lo = first_candidate - 1       # one predecessor for the first diff
    load_hi = hi + 1                    # end-exclusive
    frames = frame_loader(video_path, load_lo, load_hi)
    if len(frames) < 2:
        return None

    diffs = _diff_signal(frames)        # diffs[k] corresponds to candidate frame load_lo+1+k
    k = int(np.argmax(diffs))
    argmax_frame = load_lo + 1 + k
    offset = argmax_frame - boundary

    boundary_idx = boundary - (load_lo + 1)
    boundary_diff = (
        diffs[boundary_idx] if 0 <= boundary_idx < len(diffs) else 0.0
    )
    return BoundaryEval(
        boundary=boundary,
        offset=offset,
        peak_diff=diffs[k],
        boundary_diff=boundary_diff,
    )


def evaluate_boundaries(
    video_path: Path | str,
    boundaries: list[int],
    *,
    window: int = DEFAULT_WINDOW,
    total_frames: int | None = None,
    frame_loader: FrameLoader = load_gray_frames_ffmpeg,
) -> CutEvalReport:
    """Score how frame-exact each detected boundary is against the true luma transition.

    ``boundaries`` are the *internal* cut frames — each shot's ``src_in_frame`` except the
    leading 0 (the start of the asset is not a cut). For every boundary ``B`` the inter-frame
    diff signal over ``[B-window, B+window]`` is computed and ``offset = argmax(d) - B``.

    ``total_frames`` (if given) clamps windows to the valid range. ``frame_loader`` is the IO
    seam — the default decodes consecutive grayscale frames via ffmpeg; tests inject frames.
    """
    if window < 1:
        raise ValueError("window must be >= 1")
    evals: list[BoundaryEval] = []
    for b in boundaries:
        ev = _evaluate_one(
            video_path, b, window=window, total_frames=total_frames, frame_loader=frame_loader
        )
        if ev is not None:
            evals.append(ev)

    if not evals:
        return CutEvalReport.empty()

    abs_offsets = [abs(e.offset) for e in evals]
    n = len(evals)
    pct_exact = sum(1 for a in abs_offsets if a == 0) / n
    pct_within1 = sum(1 for a in abs_offsets if a <= 1) / n
    pct_within2 = sum(1 for a in abs_offsets if a <= 2) / n
    n_imprecise = sum(1 for a in abs_offsets if a > 2)
    worst = sorted(evals, key=lambda e: abs(e.offset), reverse=True)[:10]

    return CutEvalReport(
        n_boundaries=n,
        mean_abs_offset=sum(abs_offsets) / n,
        pct_exact=pct_exact,
        pct_within1=pct_within1,
        pct_within2=pct_within2,
        n_imprecise=n_imprecise,
        exactness_score=pct_within1,
        worst=tuple((e.boundary, e.offset) for e in worst),
        per_boundary=tuple(evals),
    )
