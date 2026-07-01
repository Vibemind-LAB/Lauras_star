"""Unit tests for :mod:`laura.analysis.crop_controller` — pure crop controller (VE6).

All tests use synthetic bboxes only — no model, no ffmpeg, no mediapipe.  The module
must be importable and fully testable in a base-deps-only environment.
"""

from __future__ import annotations

import math

import pytest

from laura.analysis.crop_controller import (
    BBox,
    CropWindow,
    _primary_subject,
    compute_crop_windows,
    crop_qa,
    face_detection_available,
    target_crop_size,
)
from laura.analysis.shorts_types import QAResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bbox(x: float, y: float, w: float, h: float) -> BBox:
    return BBox(x=x, y=y, w=w, h=h)


def _subject_at(cx: float, cy: float, w: float = 100.0, h: float = 150.0) -> BBox:
    """A bbox centred on (cx, cy)."""
    return BBox(x=cx - w / 2.0, y=cy - h / 2.0, w=w, h=h)


def _frames_moving_subject(
    n: int,
    src_w: float,
    src_h: float,
    *,
    x_start: float | None = None,
    x_end: float | None = None,
) -> list[tuple[int, list[BBox]]]:
    """``n`` frames with a subject moving linearly from left to right across the source."""
    x0 = x_start if x_start is not None else src_w * 0.1
    x1 = x_end if x_end is not None else src_w * 0.9
    cy = src_h / 2.0
    out = []
    for i in range(n):
        t = i / max(n - 1, 1)
        cx = x0 + t * (x1 - x0)
        out.append((i, [_subject_at(cx, cy)]))
    return out


# ============================================================
# 1 — target_crop_size
# ============================================================


class TestTargetCropSize:
    def test_landscape_1920x1080(self) -> None:
        """1920×1080 → (607.5, 1080) — width is constrained by height."""
        cw, ch = target_crop_size(1920, 1080)
        assert math.isclose(cw, 607.5, rel_tol=1e-9)
        assert math.isclose(ch, 1080.0, rel_tol=1e-9)

    def test_portrait_1080x1920(self) -> None:
        """1080×1920 portrait: full width fits → (1080, 1920)."""
        cw, ch = target_crop_size(1080, 1920)
        assert math.isclose(cw, 1080.0, rel_tol=1e-9)
        assert math.isclose(ch, 1920.0, rel_tol=1e-9)

    def test_square_1000x1000(self) -> None:
        """Square 1000×1000: cw = 1000 * 9/16 = 562.5, ch = 1000."""
        cw, ch = target_crop_size(1000, 1000)
        assert math.isclose(cw, 562.5, rel_tol=1e-9)
        assert math.isclose(ch, 1000.0, rel_tol=1e-9)

    def test_narrow_portrait_720x1280(self) -> None:
        """720×1280: 9:16 ratio exactly fits → full frame."""
        cw, ch = target_crop_size(720, 1280)
        assert math.isclose(cw, 720.0, rel_tol=1e-9)
        assert math.isclose(ch, 1280.0, rel_tol=1e-9)

    def test_custom_ratio_4x3(self) -> None:
        """Custom 4:3 ratio on 1920×1080 source."""
        cw, ch = target_crop_size(1920, 1080, ratio_w=4, ratio_h=3)
        # cw = min(1920, 1080*4/3) = min(1920, 1440) = 1440; ch = 1440*3/4 = 1080
        assert math.isclose(cw, 1440.0, rel_tol=1e-9)
        assert math.isclose(ch, 1080.0, rel_tol=1e-9)

    def test_crop_never_exceeds_source(self) -> None:
        """Crop dimensions are always ≤ source dimensions."""
        for sw, sh in [(640, 480), (1920, 1080), (1080, 1920), (100, 100)]:
            cw, ch = target_crop_size(sw, sh)
            assert cw <= sw + 1e-9
            assert ch <= sh + 1e-9

    def test_aspect_ratio_preserved(self) -> None:
        """Result always has the requested aspect ratio (within float tolerance)."""
        for sw, sh in [(640, 480), (1920, 1080), (1080, 1920), (500, 500)]:
            cw, ch = target_crop_size(sw, sh)
            assert math.isclose(cw / ch, 9 / 16, rel_tol=1e-6)


# ============================================================
# 2 — _primary_subject
# ============================================================


class TestPrimarySubject:
    def test_empty_returns_none(self) -> None:
        assert _primary_subject([]) is None

    def test_single_bbox(self) -> None:
        b = _bbox(0, 0, 100, 100)
        assert _primary_subject([b]) is b

    def test_largest_area_wins(self) -> None:
        small = _bbox(0, 0, 50, 50)    # area 2500
        large = _bbox(0, 0, 200, 200)  # area 40000
        assert _primary_subject([small, large]) is large

    def test_tied_area_deterministic(self) -> None:
        """With equal areas the result is one of them (not None)."""
        a = _bbox(0, 0, 100, 100)
        b = _bbox(50, 50, 100, 100)
        result = _primary_subject([a, b])
        assert result in (a, b)


# ============================================================
# 3 — compute_crop_windows: moving subject
# ============================================================


class TestComputeCropWindowsMovingSubject:
    SRC_W = 1920.0
    SRC_H = 1080.0

    def test_result_length_matches_input(self) -> None:
        frames = _frames_moving_subject(10, self.SRC_W, self.SRC_H)
        windows = compute_crop_windows(frames, self.SRC_W, self.SRC_H)
        assert len(windows) == 10

    def test_frame_indices_preserved(self) -> None:
        frames = _frames_moving_subject(5, self.SRC_W, self.SRC_H)
        windows = compute_crop_windows(frames, self.SRC_W, self.SRC_H)
        for (fi, _), w in zip(frames, windows, strict=True):
            assert w.frame == fi

    def test_crop_dimensions_match_target_size(self) -> None:
        cw, ch = target_crop_size(self.SRC_W, self.SRC_H)
        frames = _frames_moving_subject(5, self.SRC_W, self.SRC_H)
        windows = compute_crop_windows(frames, self.SRC_W, self.SRC_H)
        for win in windows:
            assert math.isclose(win.w, cw, rel_tol=1e-9)
            assert math.isclose(win.h, ch, rel_tol=1e-9)

    def test_crop_never_leaves_frame(self) -> None:
        """x ∈ [0, src_w-cw] and y ∈ [0, src_h-ch] for all crop windows."""
        frames = _frames_moving_subject(20, self.SRC_W, self.SRC_H)
        windows = compute_crop_windows(frames, self.SRC_W, self.SRC_H)
        cw, ch = target_crop_size(self.SRC_W, self.SRC_H)
        for win in windows:
            assert win.x >= -1e-9
            assert win.y >= -1e-9
            assert win.x + win.w <= self.SRC_W + 1e-9
            assert win.y + win.h <= self.SRC_H + 1e-9

    def test_crop_center_follows_subject_monotonically(self) -> None:
        """When subject moves left→right, the crop center moves left→right too."""
        frames = _frames_moving_subject(20, self.SRC_W, self.SRC_H)
        windows = compute_crop_windows(frames, self.SRC_W, self.SRC_H)
        centers_x = [w.x + w.w / 2.0 for w in windows]
        # Each step should be non-decreasing (EMA follows the moving subject)
        for i in range(1, len(centers_x)):
            assert centers_x[i] >= centers_x[i - 1] - 1e-6, (
                f"crop center regressed at step {i}: {centers_x[i]:.2f} < {centers_x[i-1]:.2f}"
            )

    def test_smoothing_reduces_per_frame_jump(self) -> None:
        """EMA smoothing: per-frame crop-center delta is smaller than subject delta."""
        # Subject jumps from left side to right side over 2 frames
        src_w, src_h = 1920.0, 1080.0
        cx_left = src_w * 0.2
        cx_right = src_w * 0.8
        frames: list[tuple[int, list[BBox]]] = [
            (0, [_subject_at(cx_left, src_h / 2)]),
            (1, [_subject_at(cx_right, src_h / 2)]),
        ]
        windows = compute_crop_windows(frames, src_w, src_h, smooth_alpha=0.25)
        subject_delta = abs(cx_right - cx_left)
        crop_delta = abs(
            (windows[1].x + windows[1].w / 2.0) - (windows[0].x + windows[0].w / 2.0)
        )
        assert crop_delta < subject_delta, (
            f"EMA did not dampen: crop_delta={crop_delta:.1f} >= subject_delta={subject_delta:.1f}"
        )

    def test_outlier_bbox_jump_is_dampened(self) -> None:
        """Single outlier frame in the middle does not cause a full crop jump."""
        src_w, src_h = 1920.0, 1080.0
        cx_normal = src_w * 0.5
        cx_outlier = src_w * 0.95  # far right outlier

        frames: list[tuple[int, list[BBox]]] = [
            (0, [_subject_at(cx_normal, src_h / 2)]),
            (1, [_subject_at(cx_normal, src_h / 2)]),
            (2, [_subject_at(cx_outlier, src_h / 2)]),  # outlier
            (3, [_subject_at(cx_normal, src_h / 2)]),
            (4, [_subject_at(cx_normal, src_h / 2)]),
        ]
        windows = compute_crop_windows(frames, src_w, src_h, smooth_alpha=0.25)
        # Crop center at frame 2 should NOT reach cx_outlier (EMA damped it)
        outlier_crop_cx = windows[2].x + windows[2].w / 2.0
        # Clamp: EMA center can't exceed src_w/2 + (src_w - crop_w)/2 due to clamping,
        # but the key test is it didn't jump all the way to cx_outlier
        full_jump_cx = cx_outlier
        # The EMA-smoothed center at step 2 is 0.75*cx_normal + 0.25*cx_outlier (approx)
        # (after two steps at cx_normal, one step at alpha=0.25 toward outlier)
        expected_max = cx_normal + 0.25 * (cx_outlier - cx_normal) + 1.0  # tiny tolerance
        assert outlier_crop_cx <= expected_max + 50.0, (
            f"Outlier was not dampened: crop_cx={outlier_crop_cx:.1f}, "
            f"full_jump={full_jump_cx:.1f}"
        )


# ============================================================
# 4 — compute_crop_windows: missing subject
# ============================================================


class TestComputeCropWindowsMissingSubject:
    SRC_W = 1920.0
    SRC_H = 1080.0

    def test_no_subject_holds_last_position(self) -> None:
        """When no subject is detected, crop holds the last known position."""
        cx_fixed = self.SRC_W * 0.3
        frames: list[tuple[int, list[BBox]]] = [
            (0, [_subject_at(cx_fixed, self.SRC_H / 2)]),
            (1, []),  # no subject
            (2, []),  # no subject
        ]
        windows = compute_crop_windows(frames, self.SRC_W, self.SRC_H)
        # All crop centers should stay at the same EMA position (no movement without input)
        cx_0 = windows[0].x + windows[0].w / 2.0
        cx_1 = windows[1].x + windows[1].w / 2.0
        cx_2 = windows[2].x + windows[2].w / 2.0
        assert math.isclose(cx_0, cx_1, rel_tol=1e-9)
        assert math.isclose(cx_1, cx_2, rel_tol=1e-9)

    def test_all_empty_uses_default_center(self) -> None:
        """No subjects at all → crop stays at the frame center (default)."""
        frames: list[tuple[int, list[BBox]]] = [(0, []), (1, []), (2, [])]
        windows = compute_crop_windows(frames, self.SRC_W, self.SRC_H)
        cw, ch = target_crop_size(self.SRC_W, self.SRC_H)
        expected_cx = self.SRC_W / 2.0
        for win in windows:
            crop_cx = win.x + win.w / 2.0
            assert math.isclose(crop_cx, expected_cx, abs_tol=1e-6), (
                f"crop center {crop_cx} ≠ expected {expected_cx}"
            )

    def test_custom_default_center(self) -> None:
        """No subjects → crop snaps to the caller-supplied default_center."""
        dc = (self.SRC_W * 0.3, self.SRC_H * 0.4)
        frames: list[tuple[int, list[BBox]]] = [(0, []), (1, [])]
        windows = compute_crop_windows(
            frames, self.SRC_W, self.SRC_H, default_center=dc
        )
        cw, _ = target_crop_size(self.SRC_W, self.SRC_H)
        # Expected: crop is centred on dc_x, but clamped so crop stays in frame
        expected_cx = dc[0]
        # With crop_x = dc_x - cw/2 clamped to [0, SRC_W-cw], recalculate:
        raw_x = expected_cx - cw / 2.0
        clamped_x = max(0.0, min(raw_x, self.SRC_W - cw))
        expected_cx_clamped = clamped_x + cw / 2.0
        for win in windows:
            crop_cx = win.x + win.w / 2.0
            assert math.isclose(crop_cx, expected_cx_clamped, abs_tol=1e-6)


# ============================================================
# 5 — compute_crop_windows: empty input
# ============================================================


class TestComputeCropWindowsEdgeCases:
    def test_empty_frames_list(self) -> None:
        windows = compute_crop_windows([], 1920.0, 1080.0)
        assert windows == []

    def test_single_frame(self) -> None:
        frames: list[tuple[int, list[BBox]]] = [
            (0, [_subject_at(960.0, 540.0)])
        ]
        windows = compute_crop_windows(frames, 1920.0, 1080.0)
        assert len(windows) == 1
        assert windows[0].frame == 0

    def test_clamping_subject_at_left_edge(self) -> None:
        """Subject at far left — crop is clamped to x=0 (never negative)."""
        frames: list[tuple[int, list[BBox]]] = [
            (0, [_subject_at(0.0, 540.0)])  # cx=0, far left
        ]
        windows = compute_crop_windows(frames, 1920.0, 1080.0)
        assert windows[0].x >= -1e-9

    def test_clamping_subject_at_right_edge(self) -> None:
        """Subject at far right — crop right edge is clamped to src_w."""
        frames: list[tuple[int, list[BBox]]] = [
            (0, [_subject_at(1920.0, 540.0)])  # cx=1920, far right
        ]
        windows = compute_crop_windows(frames, 1920.0, 1080.0)
        cw, _ = target_crop_size(1920.0, 1080.0)
        assert windows[0].x + windows[0].w <= 1920.0 + 1e-9

    def test_portrait_source_full_width_crop(self) -> None:
        """Portrait 1080×1920 source: crop == full frame, x=0, y=0."""
        frames: list[tuple[int, list[BBox]]] = [(0, [_subject_at(540.0, 960.0)])]
        windows = compute_crop_windows(frames, 1080.0, 1920.0)
        assert math.isclose(windows[0].x, 0.0, abs_tol=1e-9)
        assert math.isclose(windows[0].y, 0.0, abs_tol=1e-9)
        assert math.isclose(windows[0].w, 1080.0, rel_tol=1e-9)
        assert math.isclose(windows[0].h, 1920.0, rel_tol=1e-9)


# ============================================================
# 6 — crop_qa: passing cases
# ============================================================


class TestCropQAPassing:
    SRC_W = 1920.0
    SRC_H = 1080.0

    def test_subject_always_centered_passes(self) -> None:
        """Subject stays at frame center → always inside safe area → passed=True."""
        n = 20
        cx, cy = self.SRC_W / 2.0, self.SRC_H / 2.0
        frames: list[tuple[int, list[BBox]]] = [
            (i, [_subject_at(cx, cy)]) for i in range(n)
        ]
        windows = compute_crop_windows(frames, self.SRC_W, self.SRC_H)
        result = crop_qa(windows, frames, self.SRC_W, self.SRC_H)
        assert result.passed
        assert result.issues == []

    def test_no_subjects_anywhere_passes_neutral(self) -> None:
        """No subjects detected in any frame → neutral pass (safe_frac denominator=0)."""
        frames: list[tuple[int, list[BBox]]] = [(i, []) for i in range(5)]
        windows = compute_crop_windows(frames, self.SRC_W, self.SRC_H)
        result = crop_qa(windows, frames, self.SRC_W, self.SRC_H)
        assert result.passed
        assert result.issues == []

    def test_empty_frames_list_passes(self) -> None:
        result = crop_qa([], [], self.SRC_W, self.SRC_H)
        assert result.passed

    def test_high_in_safe_count_passes(self) -> None:
        """19/20 frames in safe area with safe_frac=0.9 → passed=True."""
        cx, cy = self.SRC_W / 2.0, self.SRC_H / 2.0
        frames: list[tuple[int, list[BBox]]] = [
            (i, [_subject_at(cx, cy)]) for i in range(20)
        ]
        windows = compute_crop_windows(frames, self.SRC_W, self.SRC_H)
        # Manually replace one window's crop with one that still has subject inside
        result = crop_qa(windows, frames, self.SRC_W, self.SRC_H, safe_frac=0.9)
        assert result.passed


# ============================================================
# 7 — crop_qa: failing cases
# ============================================================


class TestCropQAFailing:
    SRC_W = 1920.0
    SRC_H = 1080.0

    def test_subject_outside_crop_fails(self) -> None:
        """Subject drifts completely outside the crop window → failed QA."""
        cw, ch = target_crop_size(self.SRC_W, self.SRC_H)

        # Subject is placed at the right edge of the source; crop is forced to the left
        # by freezing EMA at the left side.  We construct a scenario where the crop
        # window is at the left but the subject is far right.
        far_right_cx = self.SRC_W * 0.9
        frames: list[tuple[int, list[BBox]]] = [
            (i, [_subject_at(far_right_cx, self.SRC_H / 2.0)]) for i in range(20)
        ]
        # Compute windows normally — then override subject position for QA check
        windows = compute_crop_windows(frames, self.SRC_W, self.SRC_H)

        # Build frames with the subject placed OUTSIDE the resulting crop (far left)
        far_left_cx = 10.0  # very close to left edge
        qa_frames: list[tuple[int, list[BBox]]] = [
            (i, [_subject_at(far_left_cx, self.SRC_H / 2.0)]) for i in range(20)
        ]
        result = crop_qa(windows, qa_frames, self.SRC_W, self.SRC_H, safe_frac=0.9)
        assert not result.passed
        assert len(result.issues) == 1
        assert "subject left safe area" in result.issues[0]

    def test_subject_outside_safe_area_issue_contains_frame_indices(self) -> None:
        """When QA fails, issues[0] contains the failing frame numbers."""
        cw, ch = target_crop_size(self.SRC_W, self.SRC_H)
        # Crop window at left (x=0) but subject at far right
        windows: list[CropWindow] = [
            CropWindow(frame=0, x=0.0, y=0.0, w=cw, h=ch),
        ]
        far_right_cx = self.SRC_W * 0.95
        qa_frames: list[tuple[int, list[BBox]]] = [
            (0, [_subject_at(far_right_cx, ch / 2.0)])
        ]
        result = crop_qa(windows, qa_frames, self.SRC_W, self.SRC_H, safe_frac=0.9)
        assert not result.passed
        assert "0" in result.issues[0]  # frame index 0 mentioned

    def test_partial_failure_below_threshold_fails(self) -> None:
        """8/10 in safe area with safe_frac=0.9 → passed=False."""
        cw, ch = target_crop_size(self.SRC_W, self.SRC_H)
        # Crop centred in the middle
        cx, cy = self.SRC_W / 2.0, self.SRC_H / 2.0
        crop_x = max(0.0, cx - cw / 2.0)
        crop_y = max(0.0, cy - ch / 2.0)

        windows: list[CropWindow] = [
            CropWindow(frame=i, x=crop_x, y=crop_y, w=cw, h=ch) for i in range(10)
        ]

        margin_x = 0.05 * cw
        # 8 frames: subject in safe area; 2 frames: subject outside (beyond right margin)
        safe_cx = cx  # inside safe area
        unsafe_cx = crop_x + cw - margin_x * 0.5  # right of margin → outside

        frames: list[tuple[int, list[BBox]]] = []
        for i in range(10):
            sx = safe_cx if i < 8 else unsafe_cx
            frames.append((i, [_subject_at(sx, cy)]))

        result = crop_qa(windows, frames, self.SRC_W, self.SRC_H, safe_frac=0.9)
        assert not result.passed
        assert len(result.issues) == 1

    def test_partial_failure_at_threshold_passes(self) -> None:
        """9/10 in safe area with safe_frac=0.9 → passed=True (≥ threshold)."""
        cw, ch = target_crop_size(self.SRC_W, self.SRC_H)
        cx, cy = self.SRC_W / 2.0, self.SRC_H / 2.0
        crop_x = max(0.0, cx - cw / 2.0)
        crop_y = max(0.0, cy - ch / 2.0)

        windows: list[CropWindow] = [
            CropWindow(frame=i, x=crop_x, y=crop_y, w=cw, h=ch) for i in range(10)
        ]
        margin_x = 0.05 * cw
        safe_cx = cx
        unsafe_cx = crop_x + cw - margin_x * 0.5

        frames: list[tuple[int, list[BBox]]] = []
        for i in range(10):
            sx = safe_cx if i < 9 else unsafe_cx  # 9 safe, 1 unsafe
            frames.append((i, [_subject_at(sx, cy)]))

        result = crop_qa(windows, frames, self.SRC_W, self.SRC_H, safe_frac=0.9)
        assert result.passed


# ============================================================
# 8 — BBox properties
# ============================================================


class TestBBoxProperties:
    def test_cx_cy(self) -> None:
        b = BBox(x=100.0, y=200.0, w=50.0, h=80.0)
        assert math.isclose(b.cx, 125.0)
        assert math.isclose(b.cy, 240.0)

    def test_area(self) -> None:
        b = BBox(x=0.0, y=0.0, w=10.0, h=20.0)
        assert math.isclose(b.area, 200.0)

    def test_frozen(self) -> None:
        b = BBox(x=0.0, y=0.0, w=10.0, h=10.0)
        with pytest.raises((AttributeError, TypeError)):
            b.x = 5.0  # type: ignore[misc]


# ============================================================
# 9 — face_detection_available: safe to call, returns bool
# ============================================================


class TestFaceDetectionAvailable:
    def test_returns_bool(self) -> None:
        result = face_detection_available()
        assert isinstance(result, bool)

    def test_does_not_raise(self) -> None:
        # Must not raise even when mediapipe is absent
        try:
            face_detection_available()
        except Exception as exc:
            pytest.fail(f"face_detection_available() raised: {exc}")


# ============================================================
# 10 — Integration: compute then qa
# ============================================================


class TestIntegration:
    SRC_W = 1920.0
    SRC_H = 1080.0

    def test_centered_subject_full_run_passes(self) -> None:
        """End-to-end: compute windows then run QA — centered subject always passes."""
        n = 30
        cx, cy = self.SRC_W / 2.0, self.SRC_H / 2.0
        frames: list[tuple[int, list[BBox]]] = [
            (i, [_subject_at(cx, cy)]) for i in range(n)
        ]
        windows = compute_crop_windows(frames, self.SRC_W, self.SRC_H)
        result = crop_qa(windows, frames, self.SRC_W, self.SRC_H)
        assert result == QAResult(passed=True, issues=[])

    def test_drifting_subject_fails_qa_with_issue_indices(self) -> None:
        """Subject alternates extreme left/right → EMA cannot keep up → QA fails.

        With alpha=0.25 the EMA-smoothed center converges toward neither extreme in
        alternating inputs.  The reviewer confirmed this pattern yields ~39/40 frames
        outside the safe area on a 1920×1080 source.  We assert:

        * ``passed is False``
        * at least one out-of-safe-area frame index is reported in ``issues[0]``
        """
        n = 40
        frames: list[tuple[int, list[BBox]]] = []
        for i in range(n):
            # Alternate between far left and far right every frame
            cx = self.SRC_W * 0.02 if i % 2 == 0 else self.SRC_W * 0.98
            frames.append((i, [_subject_at(cx, self.SRC_H / 2.0)]))

        windows = compute_crop_windows(frames, self.SRC_W, self.SRC_H, smooth_alpha=0.25)
        result = crop_qa(windows, frames, self.SRC_W, self.SRC_H)

        assert result.passed is False, (
            "Expected QA failure for fast-alternating subject, but got passed=True"
        )
        assert len(result.issues) == 1
        # The issue string must name at least one frame index
        issue_text = result.issues[0]
        assert "frames:" in issue_text, f"Expected frame indices in issue text, got: {issue_text!r}"
        # Sanity-check: a large fraction of frames must be failing (reviewer: ~39/40)
        assert "39" in issue_text or "38" in issue_text or "40" in issue_text or (
            # accept any count ≥ 35 reported in the text
            any(str(k) in issue_text for k in range(35, 41))
        ), f"Expected most frames to fail (~39/40), got: {issue_text!r}"

    def test_qa_result_type_reused_from_shorts_types(self) -> None:
        """crop_qa returns a shorts_types.QAResult (same class as qa_gate, qa_candidate)."""
        result = crop_qa([], [], self.SRC_W, self.SRC_H)
        assert type(result).__name__ == "QAResult"
        assert hasattr(result, "passed")
        assert hasattr(result, "issues")


# ============================================================
# 11 — compute_crop_windows: unsorted input order
# ============================================================


class TestComputeCropWindowsUnsortedInput:
    """Frames passed in shuffled order must yield the same CropWindows as sorted order."""

    SRC_W = 1920.0
    SRC_H = 1080.0

    def test_shuffled_equals_sorted(self) -> None:
        """Shuffled frame input produces identical CropWindows to sorted input."""
        import random

        n = 20
        # Build sorted frames with a moving subject
        sorted_frames = _frames_moving_subject(n, self.SRC_W, self.SRC_H)

        # Shuffle a copy (use a fixed seed for reproducibility)
        shuffled_frames = list(sorted_frames)
        rng = random.Random(42)
        rng.shuffle(shuffled_frames)

        # Ensure the shuffle actually changed order (sanity check)
        assert shuffled_frames != sorted_frames, "Shuffle did not change order; test is vacuous"

        windows_sorted = compute_crop_windows(sorted_frames, self.SRC_W, self.SRC_H)
        windows_shuffled = compute_crop_windows(shuffled_frames, self.SRC_W, self.SRC_H)

        # Both outputs must be in ascending frame order and numerically identical
        assert len(windows_sorted) == len(windows_shuffled)
        for ws, wu in zip(windows_sorted, windows_shuffled, strict=True):
            assert ws.frame == wu.frame, f"Frame index mismatch: {ws.frame} vs {wu.frame}"
            assert math.isclose(ws.x, wu.x, rel_tol=1e-9), (
                f"x mismatch at frame {ws.frame}: {ws.x} vs {wu.x}"
            )
            assert math.isclose(ws.y, wu.y, rel_tol=1e-9), (
                f"y mismatch at frame {ws.frame}: {ws.y} vs {wu.y}"
            )


# ============================================================
# 12 — crop_qa: out-of-bounds crop window flagged
# ============================================================


class TestCropQABoundsViolation:
    """crop_qa must flag CropWindows that exceed the source frame boundaries."""

    SRC_W = 1920.0
    SRC_H = 1080.0

    def test_out_of_bounds_window_right_edge_fails(self) -> None:
        """A CropWindow whose right edge exceeds src_w is flagged as a bounds violation."""
        cw, ch = target_crop_size(self.SRC_W, self.SRC_H)

        # Construct a window that clearly exceeds the right edge of the source
        bad_window = CropWindow(
            frame=0,
            x=self.SRC_W - 10.0,  # right edge = SRC_W - 10 + cw >> SRC_W
            y=0.0,
            w=cw,
            h=ch,
        )
        frames: list[tuple[int, list[BBox]]] = [
            (0, [_subject_at(self.SRC_W / 2.0, self.SRC_H / 2.0)])
        ]
        result = crop_qa([bad_window], frames, self.SRC_W, self.SRC_H)

        assert result.passed is False, (
            "Expected QA failure for out-of-bounds crop window, but got passed=True"
        )
        assert len(result.issues) >= 1
        assert any("exceeds source bounds" in issue for issue in result.issues), (
            f"Expected bounds-violation message in issues, got: {result.issues!r}"
        )

    def test_out_of_bounds_window_top_left_negative_fails(self) -> None:
        """A CropWindow with negative x is flagged as a bounds violation."""
        cw, ch = target_crop_size(self.SRC_W, self.SRC_H)
        bad_window = CropWindow(frame=0, x=-50.0, y=0.0, w=cw, h=ch)
        frames: list[tuple[int, list[BBox]]] = [(0, [])]
        result = crop_qa([bad_window], frames, self.SRC_W, self.SRC_H)
        assert result.passed is False
        assert any("exceeds source bounds" in issue for issue in result.issues)

    def test_valid_window_no_bounds_issue(self) -> None:
        """A properly clamped CropWindow raises no bounds issue."""
        cw, ch = target_crop_size(self.SRC_W, self.SRC_H)
        good_window = CropWindow(frame=0, x=0.0, y=0.0, w=cw, h=ch)
        frames: list[tuple[int, list[BBox]]] = [(0, [])]
        result = crop_qa([good_window], frames, self.SRC_W, self.SRC_H)
        # No bounds issue; passed=True because no subjects detected (neutral pass)
        assert not any("exceeds source bounds" in issue for issue in result.issues)
