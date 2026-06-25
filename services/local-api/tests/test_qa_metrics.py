"""Unit tests for :mod:`laura.analysis.qa_metrics`.

Pure and deterministic — no IO, no ffmpeg, no models. All inputs constructed in-memory.
"""

from __future__ import annotations

import pytest

from laura.analysis.editorial import Word
from laura.analysis.qa_metrics import (
    audio_jump_score,
    boundary_offset,
    cut_f1,
    match_cuts,
    word_interruption_rate,
)

# ---------------------------------------------------------------------------
# match_cuts
# ---------------------------------------------------------------------------


def test_match_cuts_perfect() -> None:
    """Identical predicted and gold → all matched, no unmatched."""
    matches, up, ug = match_cuts([10, 50, 90], [10, 50, 90], tol_frames=6)
    assert len(matches) == 3
    assert up == []
    assert ug == []


def test_match_cuts_within_tolerance() -> None:
    """Predicted cut off by tol_frames exactly → matched."""
    matches, up, ug = match_cuts([16], [10], tol_frames=6)
    assert len(matches) == 1
    assert matches[0] == (16, 10)
    assert up == []
    assert ug == []


def test_match_cuts_beyond_tolerance() -> None:
    """Predicted cut off by tol_frames+1 → no match."""
    matches, up, ug = match_cuts([17], [10], tol_frames=6)
    assert len(matches) == 0
    assert up == [17]
    assert ug == [10]


def test_match_cuts_greedy_one_to_one() -> None:
    """Two predicted close to one gold → only the nearest is matched."""
    matches, up, ug = match_cuts([8, 12], [10], tol_frames=6)
    # Both within tol; greedy picks nearest first (dist=2 for 8 or 12 vs 10)
    assert len(matches) == 1
    assert len(up) == 1
    assert ug == []


def test_match_cuts_empty_both() -> None:
    matches, up, ug = match_cuts([], [], tol_frames=6)
    assert matches == []
    assert up == []
    assert ug == []


def test_match_cuts_empty_predicted() -> None:
    matches, up, ug = match_cuts([], [10, 20], tol_frames=6)
    assert matches == []
    assert up == []
    assert sorted(ug) == [10, 20]


def test_match_cuts_empty_gold() -> None:
    matches, up, ug = match_cuts([10, 20], [], tol_frames=6)
    assert matches == []
    assert sorted(up) == [10, 20]
    assert ug == []


def test_match_cuts_partition_holds_with_duplicate_pred() -> None:
    """Duplicate predicted frame values must partition correctly by index.

    match_cuts([30, 30, 30], [30], tol=0): only one gold cut to absorb;
    the other two predicted must be unmatched — NOT collapsed by value-set logic.
    """
    matches, up, ug = match_cuts([30, 30, 30], [30], tol_frames=0)
    assert len(matches) == 1
    assert len(up) == 2
    assert len(ug) == 0
    # Partition invariants must hold.
    assert len(matches) + len(up) == 3  # == len(pred)
    assert len(matches) + len(ug) == 1  # == len(gold)


def test_match_cuts_partition_holds_with_duplicate_gold() -> None:
    """Duplicate gold frame values must partition correctly by index.

    match_cuts([30], [30, 30, 30], tol=0): one predicted absorbs one gold;
    two gold remain unmatched.
    """
    matches, up, ug = match_cuts([30], [30, 30, 30], tol_frames=0)
    assert len(matches) == 1
    assert len(up) == 0
    assert len(ug) == 2
    # Partition invariants.
    assert len(matches) + len(up) == 1   # == len(pred)
    assert len(matches) + len(ug) == 3   # == len(gold)


# ---------------------------------------------------------------------------
# cut_f1
# ---------------------------------------------------------------------------


def test_cut_f1_perfect() -> None:
    result = cut_f1([30, 80, 120], [30, 80, 120], tol_frames=6)
    assert result["f1"] == pytest.approx(1.0)
    assert result["precision"] == pytest.approx(1.0)
    assert result["recall"] == pytest.approx(1.0)


def test_cut_f1_both_empty() -> None:
    """Empty pred + empty gold → perfect (1.0/1.0/1.0)."""
    result = cut_f1([], [], tol_frames=6)
    assert result["f1"] == pytest.approx(1.0)
    assert result["precision"] == pytest.approx(1.0)
    assert result["recall"] == pytest.approx(1.0)


def test_cut_f1_pred_empty_gold_nonempty() -> None:
    """No predicted cuts, some gold → recall 0, f1 0."""
    result = cut_f1([], [30], tol_frames=6)
    assert result["recall"] == pytest.approx(0.0)
    assert result["f1"] == pytest.approx(0.0)
    assert result["precision"] == pytest.approx(1.0)


def test_cut_f1_gold_empty_pred_nonempty() -> None:
    """Gold empty, predicted nonempty → precision 0, f1 0."""
    result = cut_f1([30], [], tol_frames=6)
    assert result["precision"] == pytest.approx(0.0)
    assert result["f1"] == pytest.approx(0.0)
    assert result["recall"] == pytest.approx(1.0)


def test_cut_f1_partial_match() -> None:
    """2 of 3 predicted match gold → precision 2/3, recall 2/2."""
    result = cut_f1([30, 80, 200], [30, 80], tol_frames=6)
    assert result["precision"] == pytest.approx(2 / 3)
    assert result["recall"] == pytest.approx(1.0)
    assert result["f1"] == pytest.approx(2 * (2 / 3) * 1.0 / (2 / 3 + 1.0))


def test_cut_f1_off_by_tol_matches() -> None:
    """Predicted cut at tol boundary still counts as match."""
    result = cut_f1([36], [30], tol_frames=6)
    assert result["f1"] == pytest.approx(1.0)


def test_cut_f1_off_by_tol_plus_one_no_match() -> None:
    """Predicted cut one frame past tol → no match → f1=0."""
    result = cut_f1([37], [30], tol_frames=6)
    assert result["f1"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# boundary_offset
# ---------------------------------------------------------------------------


def test_boundary_offset_perfect() -> None:
    result = boundary_offset([30, 80], [30, 80], tol_frames=6)
    assert result["mean_abs_offset"] == pytest.approx(0.0)
    assert result["max_abs_offset"] == pytest.approx(0.0)


def test_boundary_offset_with_error() -> None:
    result = boundary_offset([33, 83], [30, 80], tol_frames=6)
    assert result["mean_abs_offset"] == pytest.approx(3.0)
    assert result["max_abs_offset"] == pytest.approx(3.0)


def test_boundary_offset_no_matches() -> None:
    """Nothing within tol → 0.0/0.0 (not an error)."""
    result = boundary_offset([100], [30], tol_frames=6)
    assert result["mean_abs_offset"] == pytest.approx(0.0)
    assert result["max_abs_offset"] == pytest.approx(0.0)


def test_boundary_offset_asymmetric() -> None:
    """Mixed offsets: mean and max differ."""
    result = boundary_offset([31, 85], [30, 80], tol_frames=6)
    assert result["mean_abs_offset"] == pytest.approx(3.0)  # (1+5)/2
    assert result["max_abs_offset"] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# word_interruption_rate
# ---------------------------------------------------------------------------


def test_word_interruption_rate_zero_on_boundary_cuts() -> None:
    """Cuts on word boundaries are editorially clean → rate=0.0."""
    words = [Word(start_frame=0, end_frame=30), Word(start_frame=40, end_frame=70)]
    rate = word_interruption_rate([30, 40], words)
    assert rate == pytest.approx(0.0)


def test_word_interruption_rate_one_mid_word_cut() -> None:
    """A cut strictly inside a word → rate > 0."""
    words = [Word(start_frame=0, end_frame=30), Word(start_frame=40, end_frame=70)]
    rate = word_interruption_rate([45], words)  # 45 is inside [40, 70)
    assert rate == pytest.approx(1.0)


def test_word_interruption_rate_mixed() -> None:
    """One clean + one mid-word → rate = 0.5."""
    words = [Word(start_frame=0, end_frame=30), Word(start_frame=40, end_frame=70)]
    rate = word_interruption_rate([30, 45], words)
    assert rate == pytest.approx(0.5)


def test_word_interruption_rate_no_cuts() -> None:
    words = [Word(start_frame=0, end_frame=30)]
    assert word_interruption_rate([], words) == pytest.approx(0.0)


def test_word_interruption_rate_no_words() -> None:
    """No words → nothing to bisect → 0.0."""
    assert word_interruption_rate([10, 20], []) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# audio_jump_score
# ---------------------------------------------------------------------------


def test_audio_jump_flat_rms_zero_jump() -> None:
    """Uniform RMS → no jump at any cut."""
    rms = [0.5] * 50
    result = audio_jump_score([10, 25, 40], rms)
    assert result["mean_jump"] == pytest.approx(0.0)
    assert result["max_jump"] == pytest.approx(0.0)
    assert result["mean_jump_norm"] == pytest.approx(0.0)


def test_audio_jump_spike_rms() -> None:
    """A single large spike in RMS at the cut point → high jump score."""
    rms = [0.1] * 100
    rms[50] = 0.9  # spike at frame 50; cut at 50 → |rms[50]-rms[49]| = 0.8
    result = audio_jump_score([50], rms)
    assert result["mean_jump"] == pytest.approx(0.8)
    assert result["max_jump"] == pytest.approx(0.8)
    # std of mostly-flat rms is small but > 0 → norm > 0
    assert result["mean_jump_norm"] > 0.0


def test_audio_jump_edge_guard_frame_zero() -> None:
    """Cut at frame 0 → edge guard → jump = 0."""
    rms = [0.1, 0.9, 0.1, 0.1]
    result = audio_jump_score([0], rms)
    assert result["mean_jump"] == pytest.approx(0.0)


def test_audio_jump_edge_guard_last_frame() -> None:
    """Cut at len(rms) → edge guard → jump = 0."""
    rms = [0.1, 0.9, 0.1, 0.1]
    result = audio_jump_score([4], rms)  # index == len
    assert result["mean_jump"] == pytest.approx(0.0)


def test_audio_jump_empty_cuts() -> None:
    rms = [0.1, 0.2, 0.3]
    result = audio_jump_score([], rms)
    assert result == {"mean_jump": 0.0, "max_jump": 0.0, "mean_jump_norm": 0.0}


def test_audio_jump_empty_rms() -> None:
    result = audio_jump_score([10], [])
    assert result == {"mean_jump": 0.0, "max_jump": 0.0, "mean_jump_norm": 0.0}


def test_audio_jump_rms_too_short() -> None:
    """rms with only 1 element → too short → all zeros."""
    result = audio_jump_score([0], [0.5])
    assert result == {"mean_jump": 0.0, "max_jump": 0.0, "mean_jump_norm": 0.0}


def test_audio_jump_norm_zero_std() -> None:
    """Perfectly flat rms → std=0 → mean_jump_norm=0 (no div-by-zero)."""
    rms = [0.5] * 20
    result = audio_jump_score([5, 10, 15], rms)
    assert result["mean_jump_norm"] == pytest.approx(0.0)
