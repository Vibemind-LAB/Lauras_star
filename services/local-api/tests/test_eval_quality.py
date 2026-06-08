"""Unified rough-cut quality score — the weighted blend of visual exactness + editorial clean.

The visual half (``evaluate_boundaries``) is the only impure piece; here it is pinned two ways:

* via the ``frame_loader`` seam (a black->white sequence with a hard luma jump at a known frame,
  so ``argmax(d)`` lands exactly there and exactness is deterministic), and
* via monkeypatching ``evaluate_boundaries`` to a stub that returns a chosen ``exactness_score``,

so the blend arithmetic can be asserted independently of any pixel decoding. The editorial half is
pure already and is driven with hand-built words in source-frame space.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from laura.analysis import eval_quality
from laura.analysis.editorial import Word
from laura.analysis.eval_cut import CutEvalReport, FrameLoader
from laura.analysis.eval_quality import RoughCutQuality, evaluate_rough_cut

# --- visual seam: a synthetic black->white sequence with one hard cut at frame K ----------------

H, W = 8, 8
N_FRAMES = 40
HARD_K = 20


def _make_sequence(k: int = HARD_K, n: int = N_FRAMES) -> list[np.ndarray]:
    """Black before frame ``k``, white from ``k`` on -> a single luma spike at ``k``."""
    return [np.full((H, W), 0 if f < k else 255, dtype=np.uint8) for f in range(n)]


def _loader_for(frames: list[np.ndarray]) -> FrameLoader:
    def loader(_video: Path | str, lo: int, hi: int) -> list[np.ndarray]:
        lo = max(0, lo)
        hi = min(len(frames), hi)
        return [frames[i] for i in range(lo, hi)]

    return loader


def _stub_report(exactness: float, n: int = 3) -> CutEvalReport:
    """A minimal CutEvalReport carrying a chosen ``exactness_score`` (other fields are filler)."""
    return CutEvalReport(
        n_boundaries=n,
        mean_abs_offset=0.0,
        pct_exact=exactness,
        pct_within1=exactness,
        pct_within2=exactness,
        n_imprecise=0,
        exactness_score=exactness,
        worst=(),
        per_boundary=(),
    )


# --- words: w0 [10,20) w1 [20,40)  <gap 40..60>  w2 [60,80) -------------------------------------


def _words() -> list[Word]:
    return [
        Word(start_frame=10, end_frame=20),
        Word(start_frame=20, end_frame=40),
        Word(start_frame=60, end_frame=80),
    ]


# === blend arithmetic via the monkeypatched visual seam =========================================


def test_overall_is_weighted_blend(monkeypatch: pytest.MonkeyPatch) -> None:
    # Visual stubbed to 0.5. Cuts: 30 -> mid w1, 50 -> in gap (clean), 70 -> mid w2.
    # editorial pct_clean = 1/3. overall = 0.6*0.5 + 0.4*(1/3) = 0.3 + 0.13333... = 0.43333...
    monkeypatch.setattr(eval_quality, "evaluate_boundaries", lambda *a, **k: _stub_report(0.5))
    q = evaluate_rough_cut(Path("x.mp4"), [30, 50, 70], _words())
    assert q.n_cuts == 3
    assert q.visual_exactness == pytest.approx(0.5)
    assert q.editorial_clean == pytest.approx(1 / 3)
    assert q.overall == pytest.approx(0.6 * 0.5 + 0.4 * (1 / 3))
    assert isinstance(q, RoughCutQuality)
    assert q.editorial["pct_mid_word"] == pytest.approx(2 / 3)


def test_all_mid_word_drops_editorial_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    # Every cut bisects a word -> editorial_clean == 0; overall is purely the visual fraction.
    monkeypatch.setattr(eval_quality, "evaluate_boundaries", lambda *a, **k: _stub_report(1.0))
    # 15 -> mid w0, 30 -> mid w1, 70 -> mid w2 (all strictly inside a word).
    q = evaluate_rough_cut(Path("x.mp4"), [15, 30, 70], _words())
    assert q.editorial_clean == 0.0
    assert q.overall == pytest.approx(0.6 * 1.0 + 0.4 * 0.0)  # == 0.6, visual-only
    assert q.overall < q.visual_exactness  # editorial penalty pulled the headline down


def test_perfect_both_is_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(eval_quality, "evaluate_boundaries", lambda *a, **k: _stub_report(1.0))
    # All cuts on word edges -> clean; visual stubbed perfect -> overall 1.0.
    q = evaluate_rough_cut(Path("x.mp4"), [20, 40, 60], _words())
    assert q.editorial_clean == 1.0
    assert q.visual_exactness == 1.0
    assert q.overall == pytest.approx(1.0)


# === empty transcript: overall collapses to the visual score exactly ============================


def test_empty_words_overall_equals_visual(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(eval_quality, "evaluate_boundaries", lambda *a, **k: _stub_report(0.42))
    q = evaluate_rough_cut(Path("x.mp4"), [5, 15, 25], [])
    assert q.editorial_clean == 1.0  # vacuously clean, reported for transparency
    assert q.overall == pytest.approx(0.42)  # exactly the visual score, NOT 0.6*0.42 + 0.4
    assert q.overall == pytest.approx(q.visual_exactness)


def test_empty_words_no_cuts_overall_equals_visual(monkeypatch: pytest.MonkeyPatch) -> None:
    # No cuts and no transcript: visual is the empty report (exactness 0.0) and overall matches it.
    monkeypatch.setattr(
        eval_quality, "evaluate_boundaries", lambda *a, **k: CutEvalReport.empty()
    )
    q = evaluate_rough_cut(Path("x.mp4"), [], [])
    assert q.n_cuts == 0
    assert q.editorial_clean == 1.0
    assert q.overall == 0.0
    assert q.overall == pytest.approx(q.visual_exactness)


# === weights normalise; any non-negative pair is accepted =======================================


def test_weights_normalise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(eval_quality, "evaluate_boundaries", lambda *a, **k: _stub_report(0.8))
    # editorial_clean = 1/3 (same cuts as the blend test). Unnormalised weights 3 and 1 must give
    # the same answer as 0.75 and 0.25.
    q = evaluate_rough_cut(Path("x.mp4"), [30, 50, 70], _words(), w_visual=3.0, w_editorial=1.0)
    expected = (3.0 * 0.8 + 1.0 * (1 / 3)) / 4.0
    assert q.overall == pytest.approx(expected)
    assert q.overall == pytest.approx(0.75 * 0.8 + 0.25 * (1 / 3))


def test_visual_only_weight(monkeypatch: pytest.MonkeyPatch) -> None:
    # w_editorial = 0 -> overall ignores the editorial term entirely (even with mid-word cuts).
    monkeypatch.setattr(eval_quality, "evaluate_boundaries", lambda *a, **k: _stub_report(0.7))
    q = evaluate_rough_cut(
        Path("x.mp4"), [30, 50, 70], _words(), w_visual=1.0, w_editorial=0.0
    )
    assert q.overall == pytest.approx(0.7)


def test_editorial_only_weight(monkeypatch: pytest.MonkeyPatch) -> None:
    # w_visual = 0 -> overall is purely editorial_clean (1/3 for these cuts).
    monkeypatch.setattr(eval_quality, "evaluate_boundaries", lambda *a, **k: _stub_report(0.7))
    q = evaluate_rough_cut(
        Path("x.mp4"), [30, 50, 70], _words(), w_visual=0.0, w_editorial=1.0
    )
    assert q.overall == pytest.approx(1 / 3)


def test_both_weights_zero_rejected() -> None:
    with pytest.raises(ValueError, match="weight"):
        evaluate_rough_cut(Path("x.mp4"), [30], _words(), w_visual=0.0, w_editorial=0.0)


def test_negative_weight_rejected() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        evaluate_rough_cut(Path("x.mp4"), [30], _words(), w_visual=-1.0, w_editorial=1.0)


# === end-to-end through the real visual loader (frame_loader seam, no monkeypatch) ==============


def test_end_to_end_via_frame_loader() -> None:
    # Real evaluate_boundaries over a synthetic sequence: one exact cut at K -> exactness 1.0.
    # No words -> overall collapses to the visual score (1.0).
    frames = _make_sequence()
    q = evaluate_rough_cut(
        Path("dummy.mp4"), [HARD_K], [], frame_loader=_loader_for(frames)
    )
    assert q.visual_exactness == 1.0
    assert q.editorial_clean == 1.0
    assert q.overall == pytest.approx(1.0)
    assert q.visual.n_boundaries == 1


def test_end_to_end_visual_imprecise_plus_editorial() -> None:
    # Cut placed 2 frames off the real luma peak -> not within1 -> visual exactness 0.0.
    # The same cut frame is also mid-word -> editorial_clean 0.0 -> overall 0.0.
    frames = _make_sequence()
    cut = HARD_K + 2  # offset -2, |offset| > 1 -> exactness_score 0.0
    words = [Word(start_frame=cut - 3, end_frame=cut + 3)]  # cut strictly inside -> mid-word
    q = evaluate_rough_cut(
        Path("dummy.mp4"), [cut], words, frame_loader=_loader_for(frames)
    )
    assert q.visual_exactness == 0.0
    assert q.editorial_clean == 0.0
    assert q.overall == 0.0


def test_rough_cut_quality_is_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(eval_quality, "evaluate_boundaries", lambda *a, **k: _stub_report(0.5))
    q = evaluate_rough_cut(Path("x.mp4"), [30], _words())
    with pytest.raises(AttributeError):
        q.overall = 1.0  # type: ignore[misc]
