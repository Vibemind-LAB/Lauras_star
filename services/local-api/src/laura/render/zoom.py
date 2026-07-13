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

from laura.render.reel import reel_blur_fill_graph

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


def _fmt_seconds(value: float) -> str:
    """Trim-friendly seconds formatting (no trailing zeros, plain int stays int)."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text if text else "0"


def zoom_hybrid_segment_parts(
    input_idx: int,
    seg_idx: int,
    *,
    start_frame: int,
    end_frame_exclusive: int,
    spec: ZoomSpec | None,
    out_w: int,
    out_h: int,
) -> tuple[list[str], str]:
    """Filtergraph parts rendering ONE trimmed segment as hybrid zoom.

    ``spec=None`` → full-frame blur-fill only (the fallback contract).  All
    internal labels carry ``seg_idx`` so any number of segments compose into
    one ``filter_complex`` without label collisions.  Returns
    ``(parts, out_label)``; the output is exactly ``out_w×out_h``, SAR 1.
    """
    out_label = f"[zh{seg_idx}]"
    composed = f"[zfx{seg_idx}]"
    trim = (
        f"[{input_idx}:v]trim=start_frame={start_frame}:end_frame={end_frame_exclusive},"
        f"setpts=PTS-STARTPTS,settb=AVTB"
    )
    if spec is None:
        src = f"[zsrc{seg_idx}]"
        return (
            [
                f"{trim}{src}",
                reel_blur_fill_graph(src, composed, out_w=out_w, out_h=out_h, tag=f"_z{seg_idx}"),
                f"{composed}setsar=1{out_label}",
            ],
            out_label,
        )

    full_in, zoom_in = f"[zfa{seg_idx}]", f"[zza{seg_idx}]"
    full_out, zoom_out = f"[zfull{seg_idx}]", f"[zzoom{seg_idx}]"
    w, h, x, y = zoom_crop_exprs(spec)
    parts = [
        f"{trim},split=2{full_in}{zoom_in}",
        reel_blur_fill_graph(full_in, composed, out_w=out_w, out_h=out_h, tag=f"_z{seg_idx}"),
        f"{composed}setsar=1{full_out}",
        (
            f"{zoom_in}trim=start={_fmt_seconds(spec.zoom_start_s)},setpts=PTS-STARTPTS,"
            f"crop=w='{w}':h='{h}':x='{x}':y='{y}',"
            f"scale={out_w}:{out_h}:flags=lanczos,setsar=1,settb=AVTB{zoom_out}"
        ),
        (
            f"{full_out}{zoom_out}xfade=transition=fade"
            f":duration={_fmt_seconds(spec.transition_s)}"
            f":offset={_fmt_seconds(spec.zoom_start_s)},settb=AVTB{out_label}"
        ),
    ]
    return parts, out_label


def zoom_concat_graph(
    clips: list[tuple[int, int]],
    specs: list[ZoomSpec | None],
    *,
    audio_flags: list[bool],
    has_base_audio: bool,
    rate_num: int,
    rate_den: int,
    out_w: int,
    out_h: int,
) -> tuple[str, str, str | None]:
    """Full pre-caption filtergraph for the zoom_hybrid path.

    Returns ``(parts, "[vcat]", "[abase]" | None)``.  Video: concat of the
    per-segment hybrid graphs.  Audio: the classic per-segment atrim concat —
    byte-equivalent semantics to the standard concat path, so the voiceover
    post-mux and loudnorm stages downstream stay unchanged.
    """
    parts: list[str] = []
    v_labels: list[str] = []
    for i, ((fin, fout), spec) in enumerate(zip(clips, specs, strict=True)):
        seg_parts, label = zoom_hybrid_segment_parts(
            i, i, start_frame=fin, end_frame_exclusive=fout,
            spec=spec, out_w=out_w, out_h=out_h,
        )
        parts.extend(seg_parts)
        v_labels.append(label)

    n = len(v_labels)
    if n == 1:
        parts.append(f"{v_labels[0]}null[vcat]")
    else:
        parts.append(f"{''.join(v_labels)}concat=n={n}:v=1:a=0[vcat]")

    a_label: str | None = None
    if has_base_audio:
        for i, (fin, fout) in enumerate(clips):
            if audio_flags[i]:
                start = fin * rate_den / rate_num
                end = fout * rate_den / rate_num
                parts.append(
                    f"[{i}:a]atrim=start={_fmt_seconds(start)}:end={_fmt_seconds(end)},"
                    f"asetpts=PTS-STARTPTS[zba{i}]"
                )
            else:
                dur = (fout - fin) * rate_den / rate_num
                parts.append(
                    "anullsrc=channel_layout=stereo:sample_rate=48000,"
                    f"atrim=duration={_fmt_seconds(dur)},asetpts=PTS-STARTPTS[zba{i}]"
                )
        if n == 1:
            parts.append("[zba0]anull[abase]")
        else:
            parts.append(f"{''.join(f'[zba{i}]' for i in range(n))}concat=n={n}:v=0:a=1[abase]")
        a_label = "[abase]"

    return ";".join(parts), "[vcat]", a_label
