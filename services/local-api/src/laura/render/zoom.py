"""ROI→window math and filtergraph builders for the ``zoom_hybrid`` fit mode.

``zoom_hybrid`` renders each segment in two phases: the full frame on a
blurred-fill canvas, then — from ``zoom_start_s`` — a smooth push into an exact
out-aspect window around the scene's region of interest.  All window math is
pure and pixel-integer.  A linear blend of two windows that each lie fully
inside the source frame stays inside the frame, so the animated crop never
needs runtime clamping.
"""

from __future__ import annotations

MIN_HEIGHT_FRAC = 0.55


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
