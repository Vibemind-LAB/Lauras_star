"""VE4 — visual embedding features in :mod:`laura.analysis.shorts_score`.

Pure and deterministic — no ffmpeg, no DB, no model. Frame embeddings are tiny
float32 numpy vectors with hand-chosen cosine relationships so every assertion is
exact arithmetic, not a learned-model heuristic.

The central guarantee under test is **backward compatibility**: when ``embeddings``
is ``None`` the three new columns (``visual_shift``, ``visual_continuity``,
``duplicate_penalty``) are all-zero for every candidate, their robust-z is ``0.0``,
and both ``total`` and ``breakdown`` are byte-identical to the pre-VE4 behaviour.
"""

from __future__ import annotations

import numpy as np
import pytest

from laura.analysis.editorial import Word
from laura.analysis.shorts_score import (
    _cosine,
    _nearest_emb,
    _segment_repr,
    _visual_continuity,
    _visual_shift_at,
    score_candidate_features,
    score_candidates,
)
from laura.analysis.shorts_types import ShortCandidate

RATE_NUM = 25
RATE_DEN = 1


def _candidate(start: int, end: int) -> ShortCandidate:
    return ShortCandidate(
        start_frame=start,
        end_frame_exclusive=end,
        start_boundary="sentence_end",
        end_boundary="sentence_end",
    )


def _vec(*values: float) -> np.ndarray:
    return np.array(values, dtype=np.float32)


# Two orthogonal unit vectors and a duplicate of the first.
_E_X = _vec(1.0, 0.0)
_E_Y = _vec(0.0, 1.0)
_E_X2 = _vec(1.0, 0.0)
_E_DIAG = _vec(1.0, 1.0)  # 45° between _E_X and _E_Y


# ---------------------------------------------------------------------------
# _cosine
# ---------------------------------------------------------------------------


def test_cosine_orthogonal_is_zero() -> None:
    assert _cosine(_E_X, _E_Y) == pytest.approx(0.0)


def test_cosine_identical_is_one() -> None:
    assert _cosine(_E_X, _E_X2) == pytest.approx(1.0)


def test_cosine_zero_vector_is_zero() -> None:
    assert _cosine(_vec(0.0, 0.0), _E_X) == 0.0
    assert _cosine(_E_X, _vec(0.0, 0.0)) == 0.0


def test_cosine_45_degrees() -> None:
    # cos(45°) = 1/sqrt(2)
    assert _cosine(_E_X, _E_DIAG) == pytest.approx(1.0 / np.sqrt(2.0))


# ---------------------------------------------------------------------------
# _nearest_emb
# ---------------------------------------------------------------------------


def test_nearest_emb_before_and_after() -> None:
    embeddings = {0: _E_X, 10: _E_Y, 20: _E_X}
    fs = sorted(embeddings)
    # exactly on a sample
    assert _nearest_emb(10, fs, embeddings, side="before") is embeddings[10]
    assert _nearest_emb(10, fs, embeddings, side="after") is embeddings[10]
    # between samples
    assert _nearest_emb(15, fs, embeddings, side="before") is embeddings[10]
    assert _nearest_emb(15, fs, embeddings, side="after") is embeddings[20]
    # past the ends
    assert _nearest_emb(-1, fs, embeddings, side="before") is None
    assert _nearest_emb(99, fs, embeddings, side="after") is None


def test_nearest_emb_empty_returns_none() -> None:
    assert _nearest_emb(5, [], {}, side="before") is None
    assert _nearest_emb(5, [], {}, side="after") is None


# ---------------------------------------------------------------------------
# _visual_shift_at
# ---------------------------------------------------------------------------


def test_visual_shift_orthogonal_is_one() -> None:
    """before/after orthogonal → shift ≈ 1 (clean visual change)."""
    embeddings = {0: _E_X, 10: _E_Y}
    fs = sorted(embeddings)
    # cut at 5: before=_E_X (frame 0), after=_E_Y (frame 10) → 1 - 0 = 1
    assert _visual_shift_at(5, fs, embeddings) == pytest.approx(1.0)


def test_visual_shift_identical_is_zero() -> None:
    """before/after identical → shift ≈ 0 (no visual change)."""
    embeddings = {0: _E_X, 10: _E_X2}
    fs = sorted(embeddings)
    assert _visual_shift_at(5, fs, embeddings) == pytest.approx(0.0)


def test_visual_shift_missing_side_is_zero() -> None:
    embeddings = {10: _E_X}
    fs = sorted(embeddings)
    # cut at 5: no sample at-or-before → 0.0 (neutral, not negative)
    assert _visual_shift_at(5, fs, embeddings) == 0.0


# ---------------------------------------------------------------------------
# _visual_continuity
# ---------------------------------------------------------------------------


def test_visual_continuity_coherent_high() -> None:
    """A window of near-identical embeddings → continuity ≈ 1."""
    embeddings = {0: _E_X, 5: _E_X2, 10: _E_X}
    fs = sorted(embeddings)
    assert _visual_continuity(0, 11, fs, embeddings) == pytest.approx(1.0)


def test_visual_continuity_alternating_low() -> None:
    """Alternating orthogonal embeddings → continuity ≈ 0."""
    embeddings = {0: _E_X, 5: _E_Y, 10: _E_X}
    fs = sorted(embeddings)
    assert _visual_continuity(0, 11, fs, embeddings) == pytest.approx(0.0)


def test_visual_continuity_fewer_than_two_is_zero() -> None:
    embeddings = {0: _E_X}
    fs = sorted(embeddings)
    assert _visual_continuity(0, 11, fs, embeddings) == 0.0


def test_coherent_window_beats_alternating_window() -> None:
    """End-to-end: the coherent candidate scores higher on visual_continuity."""
    words: list[Word] = []
    coherent = {0: _E_X, 5: _E_X2, 9: _E_X}
    alternating = {100: _E_X, 105: _E_Y, 109: _E_X}
    embeddings = {**coherent, **alternating}

    cand_coherent = _candidate(0, 10)
    cand_alt = _candidate(100, 110)

    scores = score_candidates(
        [cand_coherent, cand_alt],
        words,
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
        embeddings=embeddings,
    )
    vc_coherent = scores[0].components["visual_continuity"]
    vc_alt = scores[1].components["visual_continuity"]
    assert vc_coherent > vc_alt


# ---------------------------------------------------------------------------
# _segment_repr
# ---------------------------------------------------------------------------


def test_segment_repr_mean_inside_window() -> None:
    embeddings = {0: _E_X, 5: _E_Y}
    fs = sorted(embeddings)
    rep = _segment_repr(0, 10, fs, embeddings)
    assert rep is not None
    # mean of (1,0) and (0,1) = (0.5, 0.5)
    assert rep == pytest.approx(np.array([0.5, 0.5], dtype=np.float32))


def test_segment_repr_falls_back_to_after() -> None:
    embeddings = {20: _E_Y}
    fs = sorted(embeddings)
    # no sample inside [0, 10) → nearest after frame 0 is frame 20
    rep = _segment_repr(0, 10, fs, embeddings)
    assert rep is not None
    assert rep == pytest.approx(_E_Y)


def test_segment_repr_none_when_nothing_at_or_after() -> None:
    embeddings = {0: _E_X}
    fs = sorted(embeddings)
    # window starts past the only sample → no repr
    assert _segment_repr(50, 60, fs, embeddings) is None


# ---------------------------------------------------------------------------
# duplicate_penalty (batch-level)
# ---------------------------------------------------------------------------


def test_duplicate_penalty_identical_reprs_high() -> None:
    """Two candidates with identical segment reprs → both get a high penalty."""
    words: list[Word] = []
    # Both windows contain the same single embedding direction.
    embeddings = {0: _E_X, 5: _E_X, 100: _E_X2, 105: _E_X2}
    cand_a = _candidate(0, 10)
    cand_b = _candidate(100, 110)

    scores = score_candidates(
        [cand_a, cand_b],
        words,
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
        embeddings=embeddings,
    )
    dp_a = scores[0].components["duplicate_penalty"]
    dp_b = scores[1].components["duplicate_penalty"]
    assert dp_a == pytest.approx(1.0)
    assert dp_b == pytest.approx(1.0)


def test_duplicate_penalty_distinct_reprs_low() -> None:
    """Two candidates with orthogonal segment reprs → ~0 penalty each."""
    words: list[Word] = []
    embeddings = {0: _E_X, 5: _E_X, 100: _E_Y, 105: _E_Y}
    cand_a = _candidate(0, 10)
    cand_b = _candidate(100, 110)

    scores = score_candidates(
        [cand_a, cand_b],
        words,
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
        embeddings=embeddings,
    )
    dp_a = scores[0].components["duplicate_penalty"]
    dp_b = scores[1].components["duplicate_penalty"]
    assert dp_a == pytest.approx(0.0)
    assert dp_b == pytest.approx(0.0)


def test_duplicate_penalty_single_candidate_is_zero() -> None:
    """A single candidate has no peer → penalty stays 0.0."""
    words: list[Word] = []
    embeddings = {0: _E_X, 5: _E_X}
    scores = score_candidates(
        [_candidate(0, 10)],
        words,
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
        embeddings=embeddings,
    )
    assert scores[0].components["duplicate_penalty"] == 0.0


# ---------------------------------------------------------------------------
# Breakdown completeness (the three new keys)
# ---------------------------------------------------------------------------


def test_breakdown_contains_new_visual_keys() -> None:
    words = [Word(start_frame=10, end_frame=50), Word(start_frame=60, end_frame=100)]
    embeddings = {0: _E_X, 5: _E_Y}
    scores = score_candidates(
        [_candidate(0, 110)],
        words,
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
        embeddings=embeddings,
    )
    s = scores[0]
    for key in ("visual_shift", "visual_continuity", "duplicate_penalty"):
        assert key in s.breakdown
        assert key in s.components


# ---------------------------------------------------------------------------
# BACKWARD COMPATIBILITY — embeddings=None == prior behaviour
# ---------------------------------------------------------------------------


def _two_candidates() -> list[ShortCandidate]:
    return [_candidate(0, 750), _candidate(0, 400)]


def _words() -> list[Word]:
    return [Word(start_frame=10, end_frame=50), Word(start_frame=60, end_frame=100)]


def test_embeddings_none_equals_no_argument() -> None:
    """Passing ``embeddings=None`` yields identical total + breakdown to omitting it."""
    cands = _two_candidates()
    words = _words()

    scores_omitted = score_candidates(
        cands, words, rate_num=RATE_NUM, rate_den=RATE_DEN
    )
    scores_none = score_candidates(
        cands, words, rate_num=RATE_NUM, rate_den=RATE_DEN, embeddings=None
    )

    assert len(scores_omitted) == len(scores_none) == 2
    for a, b in zip(scores_omitted, scores_none, strict=True):
        assert a.total == b.total
        assert a.breakdown == b.breakdown
        assert a.components == b.components
        assert a.rejected == b.rejected
        assert a.reject_reason == b.reject_reason


def test_embeddings_none_new_columns_are_zero() -> None:
    """Without embeddings the three new components and their z-scores are all 0.0."""
    scores = score_candidates(
        _two_candidates(), _words(), rate_num=RATE_NUM, rate_den=RATE_DEN
    )
    for s in scores:
        for key in ("visual_shift", "visual_continuity", "duplicate_penalty"):
            assert s.components[key] == 0.0
            # robust_z of an all-zero column is 0.0 for every candidate
            assert s.breakdown[key] == 0.0


def test_embeddings_empty_dict_equals_none() -> None:
    """An empty embeddings dict is treated exactly like None (neutral)."""
    cands = _two_candidates()
    words = _words()
    scores_none = score_candidates(
        cands, words, rate_num=RATE_NUM, rate_den=RATE_DEN, embeddings=None
    )
    scores_empty = score_candidates(
        cands, words, rate_num=RATE_NUM, rate_den=RATE_DEN, embeddings={}
    )
    for a, b in zip(scores_none, scores_empty, strict=True):
        assert a.total == b.total
        assert a.breakdown == b.breakdown


def test_score_candidate_features_embeddings_none_neutral() -> None:
    """The per-candidate fn sets the three new keys to 0.0 without embeddings."""
    raw, rejected, reason = score_candidate_features(
        _candidate(0, 750),
        _words(),
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
    )
    assert not rejected and reason is None
    assert raw["visual_shift"] == 0.0
    assert raw["visual_continuity"] == 0.0
    assert raw["duplicate_penalty"] == 0.0


def test_embeddings_change_total_when_present() -> None:
    """Sanity: with *distinct* embeddings the visual columns actually move ``total``.

    Two candidates with different raw visual signals: ``cand_a`` straddles a sharp
    visual change (high visual_shift); ``cand_b`` sits in a visually flat region (zero
    shift). Because the raw column then varies across candidates, robust-z is non-zero
    and ``total`` must differ from the no-embeddings baseline for at least one of them.

    Samples are placed *between* the cut frames (not exactly on them): a sample sitting
    exactly on a cut makes the at-or-before and at-or-after lookups resolve to the same
    sample, which is a 0 shift by construction.
    """
    words: list[Word] = []
    embeddings = {
        # cand_a window [0, 20): a hard cut in the middle → before/after a cut differ
        2: _E_X, 8: _E_X, 12: _E_Y, 18: _E_Y,
        # cand_b window [100, 120): all identical → flat, zero shift/continuity delta
        102: _E_X, 108: _E_X, 112: _E_X, 118: _E_X,
    }
    cand_a = _candidate(0, 20)
    cand_b = _candidate(100, 120)

    base = score_candidates(
        [cand_a, cand_b], words, rate_num=RATE_NUM, rate_den=RATE_DEN
    )
    with_emb = score_candidates(
        [cand_a, cand_b], words, rate_num=RATE_NUM, rate_den=RATE_DEN,
        embeddings=embeddings,
    )
    # Raw visual columns must differ between the two candidates (continuity differs:
    # cand_a alternates X→Y once, cand_b is constant).
    assert (
        with_emb[0].components["visual_continuity"]
        != with_emb[1].components["visual_continuity"]
    )
    # And that difference, once z-normalised, must move at least one total.
    assert any(b.total != w.total for b, w in zip(base, with_emb, strict=True))
