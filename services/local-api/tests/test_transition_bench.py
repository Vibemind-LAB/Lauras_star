"""Plan C / Task C5 — transition_bench scoring math (model runs are manual)."""

from __future__ import annotations

from laura.bench.transition_bench import BenchResult, label_agreement


def test_label_agreement_all_match() -> None:
    assert label_agreement(["jump_cut", "smooth"], ["jump_cut", "smooth"]) == 1.0


def test_label_agreement_partial() -> None:
    assert label_agreement(["jump_cut", "smooth"], ["jump_cut", "hard_jolt"]) == 0.5


def test_label_agreement_empty_is_zero() -> None:
    assert label_agreement([], []) == 0.0


def test_label_agreement_ragged_zips_to_shortest() -> None:
    assert label_agreement(["smooth"], ["smooth", "jump_cut"]) == 1.0


def test_bench_result_sorts_best_first() -> None:
    rows = [
        BenchResult("a", 0.5, 100, 4),
        BenchResult("b", 0.9, 800, 4),
        BenchResult("c", 0.9, 200, 4),
    ]
    rows.sort(key=lambda r: (-r.agreement, r.mean_latency_ms))
    assert [r.model for r in rows] == ["c", "b", "a"]  # best agreement, then lowest latency
