"""Unit tests for Stage 2 editorial cut alignment (:mod:`laura.analysis.editorial`).

Pure and deterministic — no IO, no ffmpeg. Words are hand-built in source-frame space, each
spanning the half-open range ``[start_frame, end_frame)``. The tests pin every branch of
``align_cut`` (already-clean, word-gap, word-edge, out-of-window, empty) and the three
``editorial_metrics`` figures.
"""

from __future__ import annotations

import pytest

from laura.analysis.editorial import Word, align_cut, editorial_metrics


def _words() -> list[Word]:
    """Three words with a real silence gap between word 2 and word 3.

      w0 [10,20)   w1 [20,40)   <gap 40..60>   w2 [60,80)

    Word 0 and word 1 abut (no gap); a genuine pause spans frames 40..60.
    """
    return [
        Word(start_frame=10, end_frame=20),
        Word(start_frame=20, end_frame=40),
        Word(start_frame=60, end_frame=80),
    ]


def test_mid_word_cut_moves_to_word_edge() -> None:
    # Four abutting words, no silences anywhere. A cut at 22 sits inside c [20,30); the only
    # safe frames within window are c's own edges 20 (dist 2) and 30 (dist 8). Nearest is 20,
    # and since it is a word boundary (not a genuine silence) the kind is word_edge.
    words = [
        Word(start_frame=0, end_frame=10),
        Word(start_frame=10, end_frame=20),
        Word(start_frame=20, end_frame=30),
        Word(start_frame=30, end_frame=40),
    ]
    aligned, kind = align_cut(22, words, window=6)
    assert kind == "word_edge"
    assert aligned == 20


def test_mid_word_prefers_nearest_gap_over_edge() -> None:
    # Cut at 38 is inside w1 [20,40); the silence starts at 40 (dist 2) — nearer than w1's
    # start edge 20 — so it snaps to the genuine gap.
    aligned, kind = align_cut(38, _words(), window=12)
    assert kind == "word_gap"
    assert aligned == 40


def test_mid_word_cut_snaps_to_nearest_gap_when_reachable() -> None:
    # Cut at 62 is inside w2 [60,80); the silence ends at 60 (dist 2), the nearest safe frame.
    aligned, kind = align_cut(62, _words(), window=12)
    assert kind == "word_gap"
    assert aligned == 60


def test_cut_already_in_gap_unchanged() -> None:
    aligned, kind = align_cut(50, _words(), window=12)
    assert kind == "already_clean"
    assert aligned == 50


def test_cut_on_word_edge_is_clean() -> None:
    # Exactly on a word boundary (end-exclusive) -> between words, clean, untouched.
    aligned, kind = align_cut(20, _words(), window=12)
    assert kind == "already_clean"
    assert aligned == 20


def test_nearest_gap_is_chosen() -> None:
    # Two gaps around a mid-word cut: pick the nearer one.
    words = [
        Word(start_frame=0, end_frame=10),    # gap 10..20
        Word(start_frame=20, end_frame=30),   # cut lands inside here
        Word(start_frame=40, end_frame=50),   # gap 30..40
    ]
    # Cut at 27 is inside w[1] [20,30). Nearest safe frame: end 30 (dist 3) beats gap-10/20.
    aligned, kind = align_cut(27, words, window=12)
    assert kind == "word_gap"
    assert aligned == 30


def test_window_respected_gap_just_outside_left_unchanged() -> None:
    # Cut deep inside a long word with the only edges/gaps beyond the window -> unchanged.
    words = [Word(start_frame=0, end_frame=100)]
    aligned, kind = align_cut(50, words, window=5)
    assert kind == "unchanged_out_of_window"
    assert aligned == 50


def test_empty_words_unchanged() -> None:
    aligned, kind = align_cut(42, [], window=12)
    assert kind == "unchanged_out_of_window"
    assert aligned == 42


def test_negative_window_rejected() -> None:
    with pytest.raises(ValueError, match="window"):
        align_cut(10, _words(), window=-1)


def test_editorial_metrics_on_handbuilt_words() -> None:
    words = _words()  # w0 [10,20) w1 [20,40) gap 40..60 w2 [60,80)
    # cut 30 -> inside w1 (mid-word); cut 50 -> in gap (clean); cut 70 -> inside w2 (mid-word).
    cuts = [30, 50, 70]
    m = editorial_metrics(cuts, words)
    assert m["pct_mid_word"] == pytest.approx(2 / 3)
    assert m["pct_clean"] == pytest.approx(1 / 3)
    # distances to nearest safe frame: 30->{20 or 40}=10, 50->0 (in gap), 70->{60 or 80}=10.
    assert m["mean_dist_to_word_gap"] == pytest.approx((10 + 0 + 10) / 3)


def test_editorial_metrics_all_clean() -> None:
    words = _words()
    cuts = [20, 40, 60]  # all on word edges -> clean
    m = editorial_metrics(cuts, words)
    assert m["pct_mid_word"] == 0.0
    assert m["pct_clean"] == 1.0
    assert m["mean_dist_to_word_gap"] == 0.0


def test_editorial_metrics_no_cuts() -> None:
    m = editorial_metrics([], _words())
    assert m == {"pct_mid_word": 0.0, "pct_clean": 0.0, "mean_dist_to_word_gap": 0.0}


def test_editorial_metrics_no_words_counts_all_clean() -> None:
    m = editorial_metrics([1, 2, 3], [])
    assert m["pct_mid_word"] == 0.0
    assert m["pct_clean"] == 1.0
    assert m["mean_dist_to_word_gap"] == 0.0
