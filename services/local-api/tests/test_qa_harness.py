"""Unit tests for :mod:`laura.analysis.qa_harness`.

Covers: fixture loading, evaluate_case plausibility, check_regressions
(ok path and regression-detected path for both higher-is-better and
lower-is-better metric families), and run_suite integration.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from laura.analysis.editorial import Word
from laura.analysis.qa_harness import (
    GoldenCase,
    check_regressions,
    evaluate_case,
    load_cases,
    run_suite,
)

# Directory containing the bundled synthetic smoke fixtures.
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "qa"


# ---------------------------------------------------------------------------
# GoldenCase dataclass
# ---------------------------------------------------------------------------


def _good_case() -> GoldenCase:
    """Minimal perfect case: predicted == gold, cuts on word boundaries, flat RMS."""
    words = [
        Word(start_frame=0, end_frame=30),
        Word(start_frame=40, end_frame=70),
        Word(start_frame=80, end_frame=110),
    ]
    rms = [0.10] * 151
    return GoldenCase(
        name="synthetic_good",
        words=words,
        predicted_cuts=[30, 80],
        gold_cuts=[30, 80],
        rms=rms,
        baselines={"f1": 1.0, "word_interruption_rate": 0.0, "mean_jump_norm": 0.1},
        tol_frames=6,
    )


def _bad_case() -> GoldenCase:
    """Case with a mid-word cut and high audio jump; baselines match a perfect cutter."""
    words = [
        Word(start_frame=0, end_frame=30),
        Word(start_frame=40, end_frame=70),
        Word(start_frame=80, end_frame=110),
    ]
    rms = [0.10] * 151
    rms[45] = 0.95  # spike at frame 45 to cause high jump
    return GoldenCase(
        name="synthetic_bad",
        words=words,
        predicted_cuts=[45],       # mid-word cut [40,70)
        gold_cuts=[30, 80],
        rms=rms,
        # Tight baselines a BAD cutter cannot meet → regression is detected
        baselines={"f1": 0.95, "word_interruption_rate": 0.0, "mean_jump_norm": 0.05},
        tol_frames=6,
    )


# ---------------------------------------------------------------------------
# evaluate_case
# ---------------------------------------------------------------------------


def test_evaluate_case_good_returns_plausible_values() -> None:
    case = _good_case()
    metrics = evaluate_case(case)
    assert metrics["f1"] == pytest.approx(1.0)
    assert metrics["precision"] == pytest.approx(1.0)
    assert metrics["recall"] == pytest.approx(1.0)
    assert metrics["word_interruption_rate"] == pytest.approx(0.0)
    assert metrics["mean_jump"] == pytest.approx(0.0)
    assert metrics["mean_jump_norm"] == pytest.approx(0.0)
    assert metrics["mean_abs_offset"] == pytest.approx(0.0)
    assert metrics["max_abs_offset"] == pytest.approx(0.0)


def test_evaluate_case_bad_shows_problems() -> None:
    case = _bad_case()
    metrics = evaluate_case(case)
    # f1 < 1 because predicted cuts don't match gold well
    assert metrics["f1"] < 1.0
    # word_interruption_rate > 0 because frame 45 is inside [40,70)
    assert metrics["word_interruption_rate"] > 0.0
    # audio jump > 0 because of spike at frame 45
    assert metrics["mean_jump"] > 0.0


def test_evaluate_case_keys_complete() -> None:
    case = _good_case()
    metrics = evaluate_case(case)
    expected_keys = {
        "f1", "precision", "recall",
        "mean_abs_offset", "max_abs_offset",
        "word_interruption_rate",
        "mean_jump", "max_jump", "mean_jump_norm",
        # Flattened alias — must be present so baseline JSON using this key is checked.
        "audio_jump_mean_norm",
    }
    assert set(metrics.keys()) == expected_keys


def test_evaluate_case_alias_matches_mean_jump_norm() -> None:
    """audio_jump_mean_norm must equal mean_jump_norm (they are the same value)."""
    case = _bad_case()
    metrics = evaluate_case(case)
    assert metrics["audio_jump_mean_norm"] == pytest.approx(metrics["mean_jump_norm"])


# ---------------------------------------------------------------------------
# check_regressions — ok path
# ---------------------------------------------------------------------------


def test_check_regressions_good_case_no_regressions() -> None:
    """Good metrics meeting all baselines → empty regression list."""
    metrics = evaluate_case(_good_case())
    regressions = check_regressions(metrics, _good_case().baselines)
    assert regressions == []


def test_check_regressions_higher_is_better_ok() -> None:
    regressions = check_regressions(
        {"f1": 0.95},
        {"f1": 0.90},  # metric >= baseline - tol (0.02) → ok
        tol=0.02,
    )
    assert regressions == []


def test_check_regressions_lower_is_better_ok() -> None:
    regressions = check_regressions(
        {"word_interruption_rate": 0.01},
        {"word_interruption_rate": 0.05},  # metric <= baseline + tol → ok
        tol=0.02,
    )
    assert regressions == []


# ---------------------------------------------------------------------------
# check_regressions — regression-detected path
# ---------------------------------------------------------------------------


def test_check_regressions_bad_case_detects_regression() -> None:
    """Bad metrics fail at least one baseline check."""
    metrics = evaluate_case(_bad_case())
    regressions = check_regressions(metrics, _bad_case().baselines)
    assert len(regressions) > 0


def test_check_regressions_higher_is_better_regression() -> None:
    """f1 drops below baseline - tol → regression detected."""
    regressions = check_regressions(
        {"f1": 0.80},
        {"f1": 0.95},
        tol=0.02,
    )
    assert len(regressions) == 1
    assert "f1" in regressions[0]
    assert "higher-is-better" in regressions[0]


def test_check_regressions_lower_is_better_regression() -> None:
    """word_interruption_rate exceeds baseline + tol → regression detected."""
    regressions = check_regressions(
        {"word_interruption_rate": 0.50},
        {"word_interruption_rate": 0.0},
        tol=0.02,
    )
    assert len(regressions) == 1
    assert "word_interruption_rate" in regressions[0]
    assert "lower-is-better" in regressions[0]


def test_check_regressions_audio_jump_lower_is_better_regression() -> None:
    """mean_jump_norm exceeds baseline + tol → regression detected."""
    regressions = check_regressions(
        {"mean_jump_norm": 5.0},
        {"mean_jump_norm": 0.05},
        tol=0.02,
    )
    assert len(regressions) == 1
    assert "mean_jump_norm" in regressions[0]


def test_check_regressions_within_tol_is_not_regression() -> None:
    """A metric 1 tol unit below baseline is accepted."""
    regressions = check_regressions(
        {"f1": 0.93},
        {"f1": 0.95},
        tol=0.02,
    )
    assert regressions == []


def test_check_regressions_missing_metric_key_ignored() -> None:
    """A baseline key absent from metrics dict → silently skipped, not a regression."""
    regressions = check_regressions(
        {},                      # no metrics computed
        {"f1": 0.95},
        tol=0.02,
    )
    assert regressions == []


def test_audio_jump_mean_norm_alias_is_checked_not_skipped() -> None:
    """GoldenCase baseline using 'audio_jump_mean_norm' is actually evaluated.

    Previously evaluate_case emitted only 'mean_jump_norm', so a baseline written
    with the documented alias 'audio_jump_mean_norm' hit the 'key not in metrics'
    guard in check_regressions and silently passed — masking audio-jump regressions.

    This test uses _bad_case (which has a spike at frame 45) and sets a very tight
    audio_jump_mean_norm baseline; the regression must be detected.
    """
    case = _bad_case()
    metrics = evaluate_case(case)

    # Confirm the alias key is present in evaluate_case output.
    assert "audio_jump_mean_norm" in metrics, (
        "evaluate_case must emit 'audio_jump_mean_norm' for baseline checks to work"
    )

    # The bad case has a spike → mean_jump_norm > 0; baseline is intentionally tight.
    regressions = check_regressions(
        metrics,
        {"audio_jump_mean_norm": 0.001},  # very tight: bad cut cannot meet this
        tol=0.001,
    )
    assert len(regressions) == 1, (
        f"Expected regression on audio_jump_mean_norm, got: {regressions}"
    )
    assert "audio_jump_mean_norm" in regressions[0]


# ---------------------------------------------------------------------------
# load_cases
# ---------------------------------------------------------------------------


def test_load_cases_loads_both_fixtures() -> None:
    cases = load_cases(FIXTURES_DIR)
    assert len(cases) == 2


def test_load_cases_names_correct() -> None:
    cases = load_cases(FIXTURES_DIR)
    names = {c.name for c in cases}
    assert "good_cut" in names
    assert "bad_cut" in names


def test_load_cases_good_cut_structure() -> None:
    cases = load_cases(FIXTURES_DIR)
    good = next(c for c in cases if c.name == "good_cut")
    assert len(good.words) == 4
    assert good.predicted_cuts == [30, 80, 120]
    assert good.gold_cuts == [30, 80, 120]
    assert good.tol_frames == 6
    assert "f1" in good.baselines
    assert "word_interruption_rate" in good.baselines


def test_load_cases_empty_dir(tmp_path: Path) -> None:
    """An empty directory yields an empty list, no errors."""
    cases = load_cases(tmp_path)
    assert cases == []


# ---------------------------------------------------------------------------
# run_suite
# ---------------------------------------------------------------------------


def test_run_suite_good_fixture_passes() -> None:
    """The good_cut fixture meets all its baselines → empty regression list."""
    results = run_suite(FIXTURES_DIR)
    assert "good_cut" in results
    assert results["good_cut"] == [], (
        f"good_cut had unexpected regressions: {results['good_cut']}"
    )


def test_run_suite_bad_fixture_triggers_regression() -> None:
    """The bad_cut fixture deliberately fails some baselines → non-empty regression list."""
    results = run_suite(FIXTURES_DIR)
    assert "bad_cut" in results
    assert len(results["bad_cut"]) > 0, (
        "bad_cut should have detected at least one regression"
    )


def test_run_suite_returns_all_cases() -> None:
    results = run_suite(FIXTURES_DIR)
    assert set(results.keys()) == {"good_cut", "bad_cut"}


def test_run_suite_empty_dir(tmp_path: Path) -> None:
    """Empty fixtures dir → empty result dict, no errors."""
    results = run_suite(tmp_path)
    assert results == {}
