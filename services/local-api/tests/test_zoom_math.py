"""Pure window math for the zoom_hybrid fit mode."""

from laura.render.zoom import (  # noqa: E402
    DEFAULT_TRANSITION_S,
    MIN_HEIGHT_FRAC,
    ZoomSpec,
    roi_to_window,
    start_window,
    zoom_spec_from_option,
)


def _aspect(win: tuple[int, int, int, int]) -> float:
    return win[2] / win[3]


def test_small_roi_hits_min_height_floor() -> None:
    # tiny roi in the middle of 1080p → window height = 55% of 1080 = 594 → even 594
    win = roi_to_window((0.45, 0.45, 0.1, 0.1), src_w=1920, src_h=1080, out_w=1080, out_h=1920)
    x, y, w, h = win
    assert h == 594 and w == 334  # 594 * (1080/1920) = 334.125 → even 334
    assert abs(_aspect(win) - 1080 / 1920) < 0.01
    assert x >= 0 and y >= 0 and x + w <= 1920 and y + h <= 1080


def test_wide_roi_clamps_to_source() -> None:
    # roi 70% wide needs h = 0.7*1920/0.5625 ≈ 2389 > 1080 → clamp to full height
    win = roi_to_window((0.15, 0.3, 0.7, 0.3), src_w=1920, src_h=1080, out_w=1080, out_h=1920)
    x, y, w, h = win
    assert h == 1080 and w == 606  # 1080 * 0.5625 = 607.5 → even 606
    assert 0 <= x <= 1920 - w and y == 0


def test_corner_roi_is_clamped_inside_frame() -> None:
    win = roi_to_window((0.9, 0.85, 0.1, 0.15), src_w=1920, src_h=1080, out_w=1080, out_h=1920)
    x, y, w, h = win
    assert x + w <= 1920 and y + h <= 1080 and x >= 0 and y >= 0


def test_window_values_are_even() -> None:
    win = roi_to_window((0.33, 0.21, 0.17, 0.13), src_w=1920, src_h=1080, out_w=1080, out_h=1920)
    assert all(v % 2 == 0 for v in win)


def test_square_output_aspect() -> None:
    win = roi_to_window((0.4, 0.4, 0.2, 0.2), src_w=1920, src_h=1080, out_w=1080, out_h=1080)
    assert abs(_aspect(win) - 1.0) < 0.01
    assert win[3] >= int(MIN_HEIGHT_FRAC * 1080)


def test_start_window_is_full_height_centered_on_end_win() -> None:
    end = roi_to_window((0.6, 0.1, 0.25, 0.25), src_w=1920, src_h=1080, out_w=1080, out_h=1920)
    start = start_window(end, src_w=1920, src_h=1080, out_w=1080, out_h=1920)
    sx, sy, sw, sh = start
    assert sh == 1080 and sw == 606 and sy == 0
    end_cx = end[0] + end[2] / 2
    assert abs((sx + sw / 2) - end_cx) <= sw / 2  # centered as far as clamping allows


def _spec() -> ZoomSpec:
    end = roi_to_window((0.6, 0.1, 0.25, 0.25), src_w=1920, src_h=1080, out_w=1080, out_h=1920)
    start = start_window(end, src_w=1920, src_h=1080, out_w=1080, out_h=1920)
    return ZoomSpec(end_win=end, start_win=start, zoom_start_s=1.0, transition_s=0.6)


def test_zoom_spec_from_option_happy_path() -> None:
    spec = zoom_spec_from_option(
        {"roi": {"x": 0.6, "y": 0.1, "w": 0.25, "h": 0.25}, "zoom_start_s": 1.0},
        src_w=1920, src_h=1080, out_w=1080, out_h=1920, segment_seconds=4.0,
    )
    assert spec is not None
    assert spec.zoom_start_s == 1.0 and spec.transition_s == DEFAULT_TRANSITION_S
    assert spec.start_win[3] == 1080  # push starts at full height


def test_zoom_spec_from_option_fallbacks_return_none() -> None:
    kw: dict[str, int | float] = dict(
        src_w=1920, src_h=1080, out_w=1080, out_h=1920, segment_seconds=4.0
    )
    assert zoom_spec_from_option(None, **kw) is None  # type: ignore[arg-type]
    assert zoom_spec_from_option({}, **kw) is None  # type: ignore[arg-type]
    assert zoom_spec_from_option({"roi": {"x": 2.0, "y": 0, "w": 0.5, "h": 0.5}}, **kw) is None  # type: ignore[arg-type]
    assert zoom_spec_from_option({"roi": {"x": 0.8, "y": 0, "w": 0.5, "h": 0.5}}, **kw) is None  # type: ignore[arg-type]
    assert zoom_spec_from_option({"roi": {"x": 0.1, "y": 0.1, "w": "bad", "h": 0.5}}, **kw) is None  # type: ignore[arg-type]
    short: dict[str, int | float] = dict(kw, segment_seconds=0.5)  # too short for a visible push
    assert zoom_spec_from_option({"roi": {"x": 0.1, "y": 0.1, "w": 0.3, "h": 0.3}}, **short) is None  # type: ignore[arg-type]


def test_zoom_spec_from_option_clamps_timing_to_segment() -> None:
    kw: dict[str, int | float] = dict(
        src_w=1920, src_h=1080, out_w=1080, out_h=1920, segment_seconds=2.0
    )
    spec = zoom_spec_from_option(
        {"roi": {"x": 0.6, "y": 0.1, "w": 0.2, "h": 0.2}, "zoom_start_s": 99.0},
        **kw,  # type: ignore[arg-type]
    )
    assert spec is not None
    assert spec.zoom_start_s + spec.transition_s <= 2.0 + 1e-9


def test_zoom_spec_from_option_same_aspect_wide_roi_builds_spec() -> None:
    # same-aspect src/out with a wide-but-not-full roi: geometric check must govern
    spec = zoom_spec_from_option(
        {"roi": {"x": 0.0, "y": 0.0, "w": 0.96, "h": 0.5}},
        src_w=1080, src_h=1920, out_w=1080, out_h=1920, segment_seconds=4.0,
    )
    assert spec is not None
    assert spec.start_win != spec.end_win


def test_zoom_spec_from_option_rejects_non_finite_timing() -> None:
    kw = dict(src_w=1920, src_h=1080, out_w=1080, out_h=1920, segment_seconds=4.0)
    roi = {"x": 0.6, "y": 0.1, "w": 0.25, "h": 0.25}
    assert zoom_spec_from_option({"roi": roi, "zoom_start_s": float("nan")}, **kw) is None  # type: ignore[arg-type]
    assert zoom_spec_from_option({"roi": roi, "transition_s": float("inf")}, **kw) is None  # type: ignore[arg-type]
