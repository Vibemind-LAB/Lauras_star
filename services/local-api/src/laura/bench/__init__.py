"""Ground-truth cut benchmark harness (committed, reproducible).

Unlike :mod:`laura.analysis.eval_cut` — which scores a detected boundary against the *pixels*
(self-supervised, no labels) — this package measures the pipeline against **known true cut
frames** that we construct by concatenating solid/test scenes of a fixed length. The true cuts
are therefore exact integers we control, which lets us compute real false positives / misses
and tune the pipeline's knobs (snap window, fuse tolerance, detector, editorial blend) against
an objective target.

See :mod:`laura.bench.cut_bench` for the synthetic suite, the GT comparator, the pure editorial
scenarios, and :func:`laura.bench.cut_bench.run_sweep` driving the knob grid.
"""
