"""ROI→window math and filtergraph builders for the ``zoom_hybrid`` fit mode.

``zoom_hybrid`` renders each segment in two phases: the full frame on a
blurred-fill canvas, then — from ``zoom_start_s`` — a smooth push into an exact
out-aspect window around the scene's region of interest.  All window math is
pure and pixel-integer.  A linear blend of two windows that each lie fully
inside the source frame stays inside the frame, so the animated crop never
needs runtime clamping.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

MIN_HEIGHT_FRAC = 0.55

DEFAULT_TRANSITION_S = 0.6
_MIN_TRANSITION_S = 0.1
_MIN_ZOOM_SEGMENT_S = 0.8


def _even(v: float) -> int:
    return int(v) // 2 * 2


def roi_to_window(
    roi: tuple[float, float, float, float],
    *,
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
    min_height_frac: float = MIN_HEIGHT_FRAC,
) -> tuple[int, int, int, int]:
    """Expand a normalized ROI box to an exact ``out_w:out_h`` pixel window.

    The window covers the ROI, keeps at least ``min_height_frac`` of the source
    height (legibility floor — never zoom into pixel mush), is centered on the
    ROI center where possible and clamped fully inside the source frame.
    Returns ``(x, y, w, h)`` as even integers.
    """
    rx, ry, rw, rh = roi
    px, py = rx * src_w, ry * src_h
    pw, ph = rw * src_w, rh * src_h
    target = out_w / out_h

    h = max(ph, pw / target, min_height_frac * src_h)
    h = min(h, float(src_h))
    w = h * target
    if w > src_w:
        w = float(src_w)
        h = min(w / target, float(src_h))

    cx, cy = px + pw / 2, py + ph / 2
    x = min(max(cx - w / 2, 0.0), src_w - w)
    y = min(max(cy - h / 2, 0.0), src_h - h)

    w_i = max(_even(w), 2)
    h_i = max(_even(h), 2)
    x_i = min(max(_even(x), 0), src_w - w_i)
    y_i = min(max(_even(y), 0), src_h - h_i)
    return (x_i, y_i, w_i, h_i)


def start_window(
    end_win: tuple[int, int, int, int],
    *,
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
) -> tuple[int, int, int, int]:
    """Widest out-aspect window centered on ``end_win`` — where the push starts."""
    x, y, w, h = end_win
    roi = (x / src_w, y / src_h, w / src_w, h / src_h)
    return roi_to_window(
        roi, src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h, min_height_frac=1.0
    )


@dataclass(frozen=True)
class ZoomSpec:
    """Per-segment hybrid-zoom parameters in source-pixel space.

    ``zoom_start_s`` is relative to the segment start; the push runs over
    ``transition_s`` and then holds ``end_win`` until the segment ends.
    """

    end_win: tuple[int, int, int, int]
    start_win: tuple[int, int, int, int]
    zoom_start_s: float
    transition_s: float = DEFAULT_TRANSITION_S


def smooth_progress_expr(duration_s: float) -> str:
    """ffmpeg per-frame eased progress 0→1 over local time ``t`` ∈ [0, duration]."""
    lin = f"clip(t/{duration_s:.4f},0,1)"
    return f"(({lin})*({lin})*(3-2*({lin})))"


def zoom_crop_exprs(spec: ZoomSpec) -> tuple[str, str, str, str]:
    """Crop expressions (w, h, x, y) pushing ``start_win`` → ``end_win``.

    Local time base: the zoom branch is trimmed to begin at ``zoom_start_s``,
    so the push covers t ∈ [0, transition_s].  ``trunc(…/2)*2`` keeps every
    animated value an even integer (yuv420 chroma alignment).
    """
    p = smooth_progress_expr(spec.transition_s)
    sx, sy, sw, sh = spec.start_win
    ex, ey, ew, eh = spec.end_win
    w = f"trunc(({sw}+({ew}-{sw})*{p})/2)*2"
    h = f"trunc(({sh}+({eh}-{sh})*{p})/2)*2"
    x = f"trunc(({sx}+({ex}-{sx})*{p})/2)*2"
    y = f"trunc(({sy}+({ey}-{sy})*{p})/2)*2"
    return (w, h, x, y)


def zoom_spec_from_option(
    option: dict[str, Any] | None,
    *,
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
    segment_seconds: float,
) -> ZoomSpec | None:
    """Build a renderer ``ZoomSpec`` from one per-segment export option.

    Option shape: ``{"roi": {"x","y","w","h"}, "zoom_start_s": float,
    "transition_s": float?}`` (roi normalized).  Returns ``None`` — meaning
    plain full-frame blur-fill — for ``None``/malformed/out-of-range ROIs and
    for segments too short for a visible push.  Fallback contract: never
    crash, never center-crop.
    """
    if option is None:
        return None
    roi_raw = option.get("roi") or {}
    try:
        rx, ry = float(roi_raw["x"]), float(roi_raw["y"])
        rw, rh = float(roi_raw["w"]), float(roi_raw["h"])
        t0 = float(option.get("zoom_start_s", 0.0))
        td = float(option.get("transition_s", DEFAULT_TRANSITION_S))
    except (KeyError, TypeError, ValueError):
        return None
    if not (0.0 <= rx <= 1.0 and 0.0 <= ry <= 1.0 and 0.0 < rw <= 1.0 and 0.0 < rh <= 1.0):
        return None
    if rx + rw > 1.0 + 1e-9 or ry + rh > 1.0 + 1e-9:
        return None
    if not (math.isfinite(t0) and math.isfinite(td)):
        return None
    if segment_seconds < _MIN_ZOOM_SEGMENT_S or td <= 0.0:
        return None

    t0 = min(max(t0, 0.0), max(segment_seconds - td, 0.0))
    td = min(td, segment_seconds - t0)
    if td < _MIN_TRANSITION_S:
        return None

    end_win = roi_to_window((rx, ry, rw, rh), src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h)
    start = start_window(end_win, src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h)
    if start == end_win:
        return None
    return ZoomSpec(
        end_win=end_win,
        start_win=start,
        zoom_start_s=round(t0, 4),
        transition_s=round(td, 4),
    )
