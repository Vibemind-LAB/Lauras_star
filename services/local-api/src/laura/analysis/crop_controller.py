"""Face-aware 9:16 crop controller + safe-area QA (VE6).

Given per-frame subject bounding boxes, produces a smoothed 9:16 crop window that keeps
the subject framed, plus a "subject in safe area" QA result.

Design
------
* :func:`target_crop_size` — largest 9:16 rect fitting inside the source frame.
* :func:`_primary_subject` — selects the dominant subject bbox per frame (largest area).
* :func:`compute_crop_windows` — per-frame EMA-smoothed 9:16 crop, clamped to frame.
* :func:`crop_qa` — checks that the subject center stays inside the safe area for
  ``≥ safe_frac`` of frames that have a subject.
* :func:`face_detection_available` / :class:`FaceDetector` — optional lazy detector gate
  (mirrors :func:`~laura.analysis.visual_embed.visual_available`).  The core math never
  imports the detector.

Invariants (same as the rest of Laura's editorial layer)
---------------------------------------------------------
* Crop geometry may be ``float`` (sub-pixel precision); frame *indices* are always ``int``.
* The 9:16 window is always fully contained within the source frame — the crop center
  is clamped, not truncated.
* Nothing at module level touches MediaPipe or any heavy model — only
  :func:`face_detection_available` probes for the optional dep at runtime.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from .shorts_types import QAResult

__all__ = [
    "BBox",
    "CropWindow",
    "FaceDetector",
    "compute_crop_windows",
    "crop_qa",
    "face_detection_available",
    "target_crop_size",
]

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BBox:
    """Axis-aligned bounding box in source pixel space (top-left + size).

    All values are floats so the caller can use fractional coords from any
    detector without losing precision.  ``x`` / ``y`` are the top-left corner;
    ``w`` / ``h`` are the width and height.
    """

    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        """Horizontal center."""
        return self.x + self.w / 2.0

    @property
    def cy(self) -> float:
        """Vertical center."""
        return self.y + self.h / 2.0

    @property
    def area(self) -> float:
        """Area in square pixels."""
        return self.w * self.h


@dataclass(frozen=True)
class CropWindow:
    """One 9:16 crop rectangle for a single source frame, in source pixel space.

    ``frame`` is the integer source-frame index (same space as all other Laura
    frame indices).  ``x`` / ``y`` are the top-left corner of the crop; ``w`` / ``h``
    are its width and height (both equal to the output of :func:`target_crop_size`
    for the source dimensions, or the clamped equivalent when the source is already
    narrower/shorter than the target).
    """

    frame: int
    x: float
    y: float
    w: float
    h: float


# ---------------------------------------------------------------------------
# Pure geometry helpers
# ---------------------------------------------------------------------------


def target_crop_size(
    src_w: float,
    src_h: float,
    *,
    ratio_w: int = 9,
    ratio_h: int = 16,
) -> tuple[float, float]:
    """Return ``(crop_w, crop_h)`` — largest ``ratio_w:ratio_h`` rect fitting in ``src_w×src_h``.

    For a landscape 1920×1080 source the output is ``(607.5, 1080)``; for a portrait
    1080×1920 source it is ``(1080, 1920)`` (the full frame); for a square 1000×1000
    source it is ``(562.5, 1000)``.

    The algorithm:

    1. Set ``cw = min(src_w, src_h * ratio_w / ratio_h)`` — the width is either the
       full source width, or the width implied by the target ratio clamped to src_h.
    2. Derive ``ch = cw * ratio_h / ratio_w``, then clamp to ``src_h`` to handle
       floating-point overshoot.
    """
    cw = min(float(src_w), float(src_h) * ratio_w / ratio_h)
    ch = min(float(src_h), cw * ratio_h / ratio_w)
    return cw, ch


def _primary_subject(bboxes: Sequence[BBox]) -> BBox | None:
    """Return the largest-area bbox in ``bboxes``, or ``None`` when the sequence is empty.

    MVP: a single primary subject.  A future revision could merge multiple
    face bboxes into their bounding union.
    """
    if not bboxes:
        return None
    return max(bboxes, key=lambda b: b.area)


# ---------------------------------------------------------------------------
# EMA crop window computation
# ---------------------------------------------------------------------------


def compute_crop_windows(
    frames: Sequence[tuple[int, Sequence[BBox]]],
    src_w: float,
    src_h: float,
    *,
    ratio: tuple[int, int] = (9, 16),
    smooth_alpha: float = 0.25,
    default_center: tuple[float, float] | None = None,
) -> list[CropWindow]:
    """Compute EMA-smoothed 9:16 crop windows for every frame in ``frames``.

    Parameters
    ----------
    frames:
        Sequence of ``(frame_index, bboxes)`` tuples.  May arrive in any order —
        the function sorts by frame index before processing so temporal smoothing
        is always correct.  ``bboxes`` may be empty (no subject detected in that frame).
    src_w, src_h:
        Source frame dimensions in pixels.
    ratio:
        Target aspect ratio ``(ratio_w, ratio_h)``.  Defaults to ``(9, 16)``.
    smooth_alpha:
        EMA smoothing factor.  ``c_t = alpha * subject_center + (1-alpha) * c_{t-1}``.
        Lower values = more smoothing (slower reaction to subject movement).
    default_center:
        Center to use when no subject has been seen at all yet.  Defaults to the
        geometric center of the source frame ``(src_w/2, src_h/2)``.

    Returns
    -------
    list[CropWindow]
        One :class:`CropWindow` per input frame, in input order.

    Algorithm
    ---------
    Per frame:

    1. Find the primary subject center; if absent, hold the last known center
       (or ``default_center`` / frame center if no prior center exists).
    2. EMA-smooth the center across frames.
    3. Build a ``(cw, ch)`` crop from :func:`target_crop_size`, position its
       top-left at ``(cx - cw/2, cy - ch/2)``.
    4. Clamp: ``x ∈ [0, src_w-cw]`` and ``y ∈ [0, src_h-ch]`` so the crop
       never leaves the source frame.
    """
    ratio_w, ratio_h = ratio
    cw, ch = target_crop_size(src_w, src_h, ratio_w=ratio_w, ratio_h=ratio_h)

    # Defensive sort: callers may pass frames in any order; EMA is temporal so order matters.
    sorted_frames = sorted(frames, key=lambda fb: fb[0])

    # Fallback center when no subject has been seen yet
    dc_x = default_center[0] if default_center is not None else src_w / 2.0
    dc_y = default_center[1] if default_center is not None else src_h / 2.0

    result: list[CropWindow] = []
    ema_cx: float | None = None
    ema_cy: float | None = None

    for frame_idx, bboxes in sorted_frames:
        subject = _primary_subject(bboxes)

        if subject is not None:
            raw_cx = subject.cx
            raw_cy = subject.cy
        else:
            # No subject: hold last known EMA center; if none yet, use default
            raw_cx = ema_cx if ema_cx is not None else dc_x
            raw_cy = ema_cy if ema_cy is not None else dc_y

        # Initialise EMA on first frame; update thereafter.
        # Both ema_cx and ema_cy are always set/unset together, so checking one is enough.
        if ema_cx is None or ema_cy is None:
            ema_cx = raw_cx
            ema_cy = raw_cy
        else:
            ema_cx = smooth_alpha * raw_cx + (1.0 - smooth_alpha) * ema_cx
            ema_cy = smooth_alpha * raw_cy + (1.0 - smooth_alpha) * ema_cy

        # Position crop top-left so the crop is centred on the EMA center
        raw_x = ema_cx - cw / 2.0
        raw_y = ema_cy - ch / 2.0

        # Clamp so crop stays fully inside the source frame
        x = float(np.clip(raw_x, 0.0, max(0.0, src_w - cw)))
        y = float(np.clip(raw_y, 0.0, max(0.0, src_h - ch)))

        result.append(CropWindow(frame=int(frame_idx), x=x, y=y, w=cw, h=ch))

    return result


# ---------------------------------------------------------------------------
# Safe-area QA
# ---------------------------------------------------------------------------


def crop_qa(
    crop_windows: Sequence[CropWindow],
    frames: Sequence[tuple[int, Sequence[BBox]]],
    src_w: float,
    src_h: float,
    *,
    safe_frac: float = 0.9,
    margin_frac: float = 0.05,
) -> QAResult:
    """QA: check that the subject center stays inside the safe area for ≥ ``safe_frac`` of frames.

    The *safe area* for a given crop is the crop rectangle inset by
    ``margin_frac * cw`` on each horizontal side and ``margin_frac * ch`` on each
    vertical side.  The subject center must fall inside this inset rectangle.

    Parameters
    ----------
    crop_windows:
        Output of :func:`compute_crop_windows` (one per frame, same order as ``frames``).
    frames:
        Same ``(frame_index, bboxes)`` sequence that was passed to :func:`compute_crop_windows`.
    src_w, src_h:
        Source frame dimensions.  Each crop window is checked to lie within
        ``[0, src_w] × [0, src_h]``; any that exceeds the source bounds is
        flagged as a bounds-violation issue (a defensive invariant — clamping in
        :func:`compute_crop_windows` should make this unreachable in normal use).
    safe_frac:
        Minimum fraction of *frames with a detected subject* that must pass the safe-area
        check for ``passed=True``.
    margin_frac:
        Fraction of the crop width/height used as the safe-area margin.

    Returns
    -------
    QAResult
        ``passed=True`` when the fraction meets the threshold (or when there are no frames
        with a subject, which is treated as a neutral pass).  ``issues`` contains one human-
        readable description when the check fails.
    """
    # Build a frame-index → crop lookup for O(1) access
    crop_by_frame: dict[int, CropWindow] = {c.frame: c for c in crop_windows}

    # Defensive invariant: every crop window must lie within [0, src_w] × [0, src_h].
    # Under normal use this is guaranteed by clamping in compute_crop_windows; flag if not.
    bounds_issues: list[str] = []
    for cw_item in crop_windows:
        if (
            cw_item.x < -1e-9
            or cw_item.y < -1e-9
            or cw_item.x + cw_item.w > src_w + 1e-9
            or cw_item.y + cw_item.h > src_h + 1e-9
        ):
            bounds_issues.append(
                f"crop window for frame {cw_item.frame} exceeds source bounds"
                f" (x={cw_item.x:.1f}, y={cw_item.y:.1f},"
                f" w={cw_item.w:.1f}, h={cw_item.h:.1f};"
                f" src={src_w:.1f}×{src_h:.1f})"
            )
    if bounds_issues:
        _log.error("crop_qa: out-of-bounds crop windows detected: %s", bounds_issues)
        return QAResult(passed=False, issues=bounds_issues)

    frames_with_subject: int = 0
    in_safe_count: int = 0
    failing_frames: list[int] = []

    for frame_idx, bboxes in frames:
        subject = _primary_subject(bboxes)
        if subject is None:
            continue
        frames_with_subject += 1

        crop = crop_by_frame.get(int(frame_idx))
        if crop is None:
            # No matching crop window — treat as failing (defensive)
            failing_frames.append(int(frame_idx))
            continue

        # Safe area: the crop inset by margin on each side
        margin_x = margin_frac * crop.w
        margin_y = margin_frac * crop.h
        safe_x0 = crop.x + margin_x
        safe_y0 = crop.y + margin_y
        safe_x1 = crop.x + crop.w - margin_x
        safe_y1 = crop.y + crop.h - margin_y

        # Subject center inside safe area?
        if safe_x0 <= subject.cx <= safe_x1 and safe_y0 <= subject.cy <= safe_y1:
            in_safe_count += 1
        else:
            failing_frames.append(int(frame_idx))

    # Neutral pass when there are no frames with a subject
    if frames_with_subject == 0:
        return QAResult(passed=True, issues=[])

    passed = (in_safe_count / frames_with_subject) >= safe_frac

    if passed:
        return QAResult(passed=True, issues=[])

    out_count = frames_with_subject - in_safe_count
    issue = (
        f"subject left safe area in {out_count}/{frames_with_subject} frames"
        f" (frames: {failing_frames})"
    )
    return QAResult(passed=False, issues=[issue])


# ---------------------------------------------------------------------------
# Optional lazy detector gate (mirrors visual_embed.visual_available)
# ---------------------------------------------------------------------------


def face_detection_available() -> bool:
    """``True`` when a face-detection backend (e.g. MediaPipe) is importable.

    Mirrors :func:`~laura.analysis.visual_embed.visual_available`: a soft probe the job
    uses to skip gracefully.  Never imports the heavy model; always safe to call.
    """
    try:
        import mediapipe  # type: ignore[import-not-found]  # noqa: F401
    except Exception:
        return False
    return True


class FaceDetector(Protocol):
    """Protocol for an injected face/subject detector.

    Implementors take a batch of ``HxWx3`` uint8 RGB frames and return a parallel
    list of bbox lists — one list per input frame.  Tests inject a deterministic fake
    implementing this Protocol; the core crop math never calls a real detector.
    """

    def detect(self, frames: list[np.ndarray]) -> list[list[BBox]]: ...
