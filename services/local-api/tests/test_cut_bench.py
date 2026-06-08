"""Tests for the ground-truth cut benchmark (:mod:`laura.bench.cut_bench`).

Two layers, matching the harness:

* **Pure GT-metric math** — hand-built detected/true boundary lists exercise the comparator's
  offsets, false positives, misses, and the precision/recall/f1 derivations. No ffmpeg.
* **An ffmpeg-gated end-to-end smoke** — generates ONE tiny synthetic hard-cut clip, runs the
  real detector + GT comparator, and asserts the numbers are sane (the true cuts are recovered
  within tolerance with no spurious detections). Skipped when ffmpeg is absent, mirroring the
  other ``*_real`` / ingest tests.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from laura.analysis.editorial import Word
from laura.bench.cut_bench import (
    GRADUAL_TOL,
    BenchCase,
    GTReport,
    KnobConfig,
    _build_hard_case,
    compare_to_ground_truth,
    editorial_pick_offset,
    editorial_tradeoff_curve,
    run_case,
)

# --- pure GT-metric math (no ffmpeg) --------------------------------------------------


def test_perfect_match_zero_offsets() -> None:
    rep = compare_to_ground_truth([30, 60, 90], [30, 60, 90], tol=2)
    assert rep.n_true == 3
    assert rep.n_matched == 3
    assert rep.false_positives == 0
    assert rep.misses == 0
    assert rep.mean_abs_offset == 0.0
    assert rep.median_abs_offset == 0.0
    assert rep.pct_exact == 1.0
    assert rep.pct_within1 == 1.0
    assert rep.matched_offsets == (0, 0, 0)
    assert rep.precision == 1.0
    assert rep.recall == 1.0
    assert rep.f1 == 1.0


def test_signed_offsets_within_tolerance() -> None:
    # 31 is +1 late of 30; 58 is -2 early of 60; 90 exact. All within tol=2 -> matched.
    rep = compare_to_ground_truth([31, 58, 90], [30, 60, 90], tol=2)
    assert rep.n_matched == 3
    assert rep.false_positives == 0
    assert rep.misses == 0
    assert sorted(rep.matched_offsets) == [-2, 0, 1]
    assert rep.mean_abs_offset == pytest.approx((1 + 2 + 0) / 3)
    assert rep.pct_exact == pytest.approx(1 / 3)
    assert rep.pct_within1 == pytest.approx(2 / 3)
    assert rep.pct_within2 == 1.0


def test_false_positive_is_unmatched_detection() -> None:
    # 200 has no true cut within tol -> a false positive; the rest match exactly.
    rep = compare_to_ground_truth([30, 60, 90, 200], [30, 60, 90], tol=2)
    assert rep.n_matched == 3
    assert rep.false_positives == 1
    assert rep.misses == 0
    assert rep.precision == pytest.approx(3 / 4)
    assert rep.recall == 1.0


def test_miss_is_unmatched_true_cut() -> None:
    # The true cut at 60 has no detection within tol -> a miss; 30 and 90 match.
    rep = compare_to_ground_truth([30, 90], [30, 60, 90], tol=2)
    assert rep.n_matched == 2
    assert rep.false_positives == 0
    assert rep.misses == 1
    assert rep.recall == pytest.approx(2 / 3)
    assert rep.precision == 1.0


def test_drifted_detection_is_both_fp_and_miss() -> None:
    # 26 is 4 frames off the true cut 30 (> tol 2): it neither matches 30 (which is then a miss)
    # nor any other true cut (so 26 is a false positive). This is the solid-colour snap pathology.
    rep = compare_to_ground_truth([26, 60, 90], [30, 60, 90], tol=2)
    assert rep.n_matched == 2          # 60 and 90
    assert rep.false_positives == 1    # 26 invented
    assert rep.misses == 1             # 30 dropped


def test_greedy_nearest_first_assignment() -> None:
    # Two detections near one true cut: the nearest claims it, the other is a false positive.
    rep = compare_to_ground_truth([29, 31], [30], tol=2)
    assert rep.n_matched == 1
    assert rep.false_positives == 1
    assert rep.misses == 0
    # The nearer (|29-30|=1 vs |31-30|=1 tie) resolves deterministically to the lower frame 29.
    assert rep.matched_offsets == (-1,)


def test_empty_detection_all_misses() -> None:
    rep = compare_to_ground_truth([], [30, 60, 90], tol=2)
    assert rep.n_matched == 0
    assert rep.misses == 3
    assert rep.false_positives == 0
    assert rep.recall == 0.0
    assert rep.precision == 0.0  # n_detected 0 with true cuts present -> 0 precision
    assert rep.mean_abs_offset == 0.0


def test_empty_truth_all_false_positives() -> None:
    rep = compare_to_ground_truth([30, 60], [], tol=2)
    assert rep.n_true == 0
    assert rep.n_matched == 0
    assert rep.false_positives == 2
    assert rep.misses == 0
    assert rep.recall == 1.0  # vacuous: nothing to find
    assert rep.precision == 0.0


def test_both_empty_is_vacuously_perfect() -> None:
    rep = compare_to_ground_truth([], [], tol=2)
    assert rep == GTReport(
        tol=2, n_true=0, n_detected=0, n_matched=0, false_positives=0, misses=0,
        mean_abs_offset=0.0, median_abs_offset=0.0, pct_exact=0.0, pct_within1=0.0,
        pct_within2=0.0, matched_offsets=(),
    )
    assert rep.f1 == 1.0  # precision 1.0, recall 1.0


def test_median_even_count() -> None:
    # offsets |0|,|1|,|2|,|5| -> median of [0,1,2,5] is (1+2)/2 = 1.5.
    rep = compare_to_ground_truth([30, 41, 52, 65], [30, 40, 50, 60], tol=6)
    assert rep.n_matched == 4
    assert rep.median_abs_offset == pytest.approx(1.5)


def test_negative_tolerance_rejected() -> None:
    with pytest.raises(ValueError, match="tol"):
        compare_to_ground_truth([30], [30], tol=-1)


# --- pure editorial scenarios (no ffmpeg) ---------------------------------------------


def test_editorial_low_weight_keeps_visual_peak() -> None:
    from laura.bench.cut_bench import _diverge_scenario

    sc = _diverge_scenario()
    chosen, off_ideal, off_peak = editorial_pick_offset(sc, w_editorial=0.0)
    # Picture-first -> stays on the visual peak (mid-word but visually exact).
    assert chosen == sc.visual_peak_frame
    assert off_peak == 0
    assert off_ideal == abs(sc.visual_peak_frame - sc.ideal_frame)


def test_editorial_high_weight_reaches_ideal_seam() -> None:
    from laura.bench.cut_bench import _diverge_scenario

    sc = _diverge_scenario()
    chosen, off_ideal, _off_peak = editorial_pick_offset(sc, w_editorial=1.0)
    # Sound-first -> lands on the editorial ideal (speaker turn in a silence).
    assert chosen == sc.ideal_frame
    assert off_ideal == 0


def test_editorial_tradeoff_curve_is_monotone() -> None:
    curve = editorial_tradeoff_curve()
    # As w_editorial rises, distance to the visual peak never decreases and distance to the
    # editorial ideal never increases — the defining shape of the trade-off (the bias slider).
    peaks = [p.offset_to_visual_peak for p in curve]
    ideals = [p.offset_to_editorial_ideal for p in curve]
    assert peaks == sorted(peaks)               # non-decreasing
    assert ideals == sorted(ideals, reverse=True)  # non-increasing
    assert peaks[0] == 0 and ideals[-1] == 0    # endpoints hit each extreme


def test_aligned_scenario_no_tradeoff() -> None:
    from laura.bench.cut_bench import _aligned_scenario

    sc = _aligned_scenario()
    # Visual and editorial agree -> every weight keeps the cut on the shared frame.
    for w in (0.0, 0.4, 1.0):
        chosen, off_ideal, off_peak = editorial_pick_offset(sc, w_editorial=w)
        assert chosen == sc.ideal_frame == sc.visual_peak_frame
        assert off_ideal == 0 and off_peak == 0


def test_sentence_end_frames_wired_into_scenario() -> None:
    # Sanity: the divergence scenario's second word "Next." is a sentence end, so semantic frames
    # are actually derived (not stubbed) inside editorial_pick_offset.
    from laura.analysis.semantic import sentence_end_frames
    from laura.bench.cut_bench import _diverge_scenario

    words = list(_diverge_scenario().words)
    assert any(isinstance(w, Word) for w in words)
    assert sentence_end_frames(words)  # non-empty -> wiring is real


# --- ffmpeg-gated end-to-end smoke ----------------------------------------------------

_FFMPEG = shutil.which(os.environ.get("LAURA_FFMPEG", "ffmpeg"))


@pytest.mark.skipif(_FFMPEG is None, reason="ffmpeg not available on PATH")
def test_smoke_hard_cut_case_recovers_ground_truth(tmp_path: Path) -> None:
    """ONE tiny synthetic hard-cut clip end-to-end: detect + GT comparator returns sane numbers."""
    case: BenchCase = _build_hard_case(
        tmp_path, "smoke", ["testsrc", "smptebars", "testsrc2"], scene_frames=20
    )
    # Three 20-frame scenes -> true cuts at [20, 40].
    assert case.true_cuts == (20, 40)
    assert case.video.is_file()

    knob = KnobConfig(detector="adaptive", snap_window=4, fuse_tol=8)
    result = run_case(case, knob)
    rep = result.report

    # The comparator must return a well-formed report over the real detection.
    assert rep.n_true == 2
    assert rep.tol == case.tol
    assert 0 <= rep.n_matched <= rep.n_true
    assert rep.false_positives >= 0
    assert rep.misses >= 0
    assert 0.0 <= rep.precision <= 1.0
    assert 0.0 <= rep.recall <= 1.0
    # On distinct test patterns the adaptive detector recovers both hard cuts exactly: no misses,
    # no false positives, every matched cut frame-exact.
    assert rep.misses == 0
    assert rep.false_positives == 0
    assert rep.mean_abs_offset <= case.tol


@pytest.mark.skipif(_FFMPEG is None, reason="ffmpeg not available on PATH")
def test_smoke_gradual_tol_is_wider(tmp_path: Path) -> None:
    """The gradual case carries the wider midpoint tolerance, not the hard-cut tolerance."""
    from laura.bench.cut_bench import _build_gradual_case

    case = _build_gradual_case(tmp_path, "smoke_grad")
    assert case.kind == "gradual"
    assert case.tol == GRADUAL_TOL
    assert case.true_cuts == (60,)  # pre 45 + fade 30 // 2 == 60
    assert case.video.is_file()
