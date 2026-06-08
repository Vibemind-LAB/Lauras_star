"""Run the ground-truth cut benchmark + knob sweep and print the results tables.

    uv run --no-sync python -m laura.bench.bench_run

Generates the synthetic suite (needs ffmpeg), runs the visual knob grid against the known true
cuts, prints the per-(case, knob) GT table and the aggregate knob ranking, then runs the PURE
editorial trade-off sweep and prints the visual-vs-editorial curve. Read-only and side-effect
free apart from a throwaway temp dir; no app restart needed. Output goes to stdout via the
project logger pattern (a plain ``print`` here is acceptable in a CLI entrypoint, mirroring
``eval_cut_cli``).
"""

from __future__ import annotations

import sys

from laura.bench.cut_bench import (
    BenchCase,
    CaseResult,
    KnobScore,
    build_suite,
    editorial_scenarios,
    editorial_tradeoff_curve,
    run_sweep,
)


def _format_gt_table(results: list[CaseResult]) -> str:
    """Per-(case, knob) GT report: detected frames, offsets, false-pos, misses."""
    header = (
        f"{'case':<16} {'knob':<26} {'detected':<18} "
        f"{'mean|off|':>9} {'med':>4} {'w1%':>5} {'FP':>3} {'miss':>4}"
    )
    lines = [header, "-" * len(header)]
    for r in results:
        rep = r.report
        det = ",".join(str(d) for d in r.detected) or "-"
        if len(det) > 17:
            det = det[:14] + "..."
        lines.append(
            f"{r.case:<16} {r.knob.label():<26} {det:<18} "
            f"{rep.mean_abs_offset:>9.2f} {rep.median_abs_offset:>4.0f} "
            f"{rep.pct_within1 * 100:>4.0f}% {rep.false_positives:>3d} {rep.misses:>4d}"
        )
    return "\n".join(lines)


def _format_knob_ranking(scores: list[KnobScore]) -> str:
    """Aggregate knob ranking over the hard cases, best (lowest cost) first."""
    header = (
        f"{'rank':<4} {'knob':<26} {'mean|off|':>9} {'within1':>8} "
        f"{'FP':>3} {'miss':>4} {'drift':>6} {'cost':>7}"
    )
    lines = ["", "=== Knob ranking (hard cases, lower cost is better) ===", header,
             "-" * len(header)]
    for i, s in enumerate(scores, start=1):
        lines.append(
            f"{i:<4} {s.knob.label():<26} {s.mean_abs_offset:>9.2f} "
            f"{s.mean_pct_within1 * 100:>7.0f}% {s.total_false_positives:>3d} "
            f"{s.total_misses:>4d} {s.total_drift:>6.0f} {s.cost:>7.2f}"
        )
    return "\n".join(lines)


def _format_tradeoff() -> str:
    """The editorial visual-vs-editorial trade-off curve (pure, no ffmpeg)."""
    curve = editorial_tradeoff_curve()
    lines = [
        "",
        "=== Editorial blend trade-off (divergence scenario; bias-slider justification) ===",
        "visual peak @270 (mid-word, clipped) vs editorial ideal @273 (speaker turn in silence)",
        f"{'w_editorial':>11} {'chosen':>7} {'off_visual_peak':>16} {'off_editorial':>14}",
        "-" * 52,
    ]
    for p in curve:
        lines.append(
            f"{p.w_editorial:>11.1f} {p.chosen_frame:>7d} "
            f"{p.offset_to_visual_peak:>16d} {p.offset_to_editorial_ideal:>14d}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    from laura.analysis.transnet import transnetv2_available

    have_transnet = transnetv2_available()
    suite: list[BenchCase] = build_suite()

    tn = "available (hybrid included)" if have_transnet else "SKIPPED (no model)"
    print("=== Ground-truth cut benchmark ===")
    print(f"cases       : {', '.join(c.name for c in suite)}")
    print(f"transnet    : {tn}")
    print()

    results, scores = run_sweep(suite, include_hybrid=have_transnet)
    print(_format_gt_table(results))
    print(_format_knob_ranking(scores))
    if scores:
        best = scores[0]
        print(f"\nbest visual knob: {best.knob.label()}  (cost {best.cost:.2f})")

    print(_format_tradeoff())
    print(f"\neditorial scenarios: {', '.join(s.name for s in editorial_scenarios())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
