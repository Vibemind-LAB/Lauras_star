"""Stage 2 — editorial cut alignment against the transcript (pure, no IO).

The visual cut stage (hybrid detection + diff-peak snapping, see ``refine.py`` / ``eval_cut``)
places each shot boundary on the frame where the *picture* changes the most. That is exactly
right for the image, but it knows nothing about *speech*: a frame-exact visual cut can still
land in the middle of a spoken word, which sounds clipped and unprofessional.

This module nudges such a cut to the nearest **editorial-safe** frame — a silence between two
words (a "word gap") or, failing that, the nearer edge of the word it currently bisects — so
the edit never severs a word and tends to fall on a natural speech pause. It is purely
advisory: the move is bounded by ``±window`` and only ever *improves* placement. A cut already
sitting in a gap is left untouched, and an empty transcript leaves every cut where it was.

Frames here are **source-frame indices** of the asset (the same space the transcript words and
the shots are stored in), so a shot's ``src_in_frame`` is directly comparable to a word's
``start_frame``/``end_frame``. A word spans the half-open range ``[start_frame, end_frame)``
(end-exclusive, like every other range in Laura): consecutive words abut when
``words[i].end_frame == words[i + 1].start_frame``, and a real gap exists only when
``words[i].end_frame < words[i + 1].start_frame``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

# ~0.4s at 30fps — wide enough to reach the neighbouring pause, narrow enough that the cut
# stays visually where the shot detector put it.
DEFAULT_WINDOW = 12

# Outcome of aligning one cut. Ordered loosely best -> worst for readability:
#   already_clean            cut was already in a gap / at a word edge -> unchanged
#   word_gap                 moved to a silence between two words
#   word_edge                cut bisected a word; moved to that word's nearer edge
#   unchanged_out_of_window  the only safe frame was further than ``window`` -> left in place
AlignKind = str


@dataclass(frozen=True)
class Word:
    """Minimal typed view of a transcript word in source-frame space.

    Spans the half-open range ``[start_frame, end_frame)``. ``end_frame`` is exclusive, so a
    cut exactly on ``start_frame`` or on ``end_frame`` sits *between* words, not inside one.

    ``text`` and ``speaker`` are **optional** semantic enrichments (the spoken text — with any ASR
    punctuation — and the diarization speaker label). They default to ``None`` so every existing
    construction (``Word(start_frame=.., end_frame=..)``) keeps working unchanged; when present they
    feed :mod:`laura.analysis.semantic` (sentence ends / speaker turns). ``None`` simply means "no
    semantic signal", and placement degrades to the timing-only behaviour.
    """

    start_frame: int
    end_frame: int
    text: str | None = None
    speaker: str | None = None


def _covering_word(cut_frame: int, words: Sequence[Word]) -> Word | None:
    """The word strictly bisected by ``cut_frame`` (``start < cut < end``), or ``None``.

    A cut on a word boundary (``cut == start`` or ``cut == end``) is *not* covered — it already
    sits at an editorial-safe edge.
    """
    for w in words:
        if w.start_frame < cut_frame < w.end_frame:
            return w
    return None


def _gap_frames(words: Sequence[Word]) -> list[int]:
    """Frames that sit in a genuine silence *between* two words (the editorial sweet spot).

    A real gap exists only where consecutive words do not abut
    (``prev.end_frame < next.start_frame``); every frame in that closed interval
    ``[prev.end_frame, next.start_frame]`` is a safe place to cut (no word spans it). Abutting
    words (``prev.end_frame == next.start_frame``) yield no gap. The leading edge before the
    first word and the trailing edge after the last word are also pure silence, so they count.
    """
    if not words:
        return []
    ordered = sorted(words, key=lambda w: w.start_frame)
    frames: set[int] = {ordered[0].start_frame, ordered[-1].end_frame}
    for prev, nxt in zip(ordered, ordered[1:], strict=False):
        if prev.end_frame < nxt.start_frame:  # genuine pause
            frames.update(range(prev.end_frame, nxt.start_frame + 1))
    return sorted(frames)


def _nearest_within(cut_frame: int, candidates: Sequence[int], window: int) -> int | None:
    """Nearest candidate frame to ``cut_frame`` within ``±window``; ``None`` if none qualify.

    Ties (a candidate equidistant on each side) resolve to the lower frame, which keeps the cut
    from drifting later than necessary.
    """
    best: int | None = None
    best_dist = window + 1
    for c in candidates:
        dist = abs(c - cut_frame)
        if dist <= window and dist < best_dist:
            best, best_dist = c, dist
    return best


def align_cut(
    cut_frame: int, words: list[Word], *, window: int = DEFAULT_WINDOW
) -> tuple[int, AlignKind]:
    """Snap ``cut_frame`` to the nearest editorial-safe frame within ``±window``.

    Resolution order:

    * **already_clean** — ``cut_frame`` does not bisect any word (it is in a silence or on a
      word edge) -> returned unchanged.
    * **word_gap** — ``cut_frame`` bisects a word and the nearest reachable safe frame is a
      genuine silence between words -> snap there.
    * **word_edge** — ``cut_frame`` bisects a word and the nearest reachable safe frame is the
      bisected word's own start/end (no silence is closer within ``window``) -> snap there.
    * **unchanged_out_of_window** — no safe frame lies within ``window`` (e.g. deep inside one
      long word) -> left in place. Also covers an empty ``words`` list.

    Never moves further than ``window`` and never returns a frame that bisects a word. Defensive
    by design: when in doubt it leaves ``cut_frame`` where the visual stage put it.
    """
    if window < 0:
        raise ValueError("window must be >= 0")
    if not words:
        return cut_frame, "unchanged_out_of_window"

    covering = _covering_word(cut_frame, words)
    if covering is None:
        # Already in a silence / on a word edge — editorially clean as-is.
        return cut_frame, "already_clean"

    # Mid-word: the cut must move. Candidates are genuine silences (best) and the bisected
    # word's own edges (always safe). Snap to whichever safe frame is nearest within window.
    gap = _nearest_within(cut_frame, _gap_frames(words), window)
    edge = _nearest_within(cut_frame, (covering.start_frame, covering.end_frame), window)

    if gap is not None and (edge is None or abs(gap - cut_frame) <= abs(edge - cut_frame)):
        return gap, "word_gap"
    if edge is not None:
        return edge, "word_edge"
    return cut_frame, "unchanged_out_of_window"


def _safe_frames(words: Sequence[Word]) -> list[int]:
    """Every editorial-safe frame: genuine silences plus every word edge.

    A clean cut sits on one of these, so the distance from any cut to the nearest member is the
    distance the cut would have to travel to stop bisecting a word (0 when it is already clean).
    """
    frames = set(_gap_frames(words))
    for w in words:
        frames.add(w.start_frame)
        frames.add(w.end_frame)
    return sorted(frames)


def editorial_metrics(
    cuts: list[int],
    words: list[Word],
    *,
    silence: list[tuple[int, int]] | None = None,
    sentence_frames: set[int] | None = None,
    speaker_frames: set[int] | None = None,
) -> dict[str, float]:
    """Editorial-quality eval over a set of cuts (the editorial analogue of cut-exactness).

    Returns:

    * ``pct_mid_word`` — share of cuts that bisect a word (lower is better; 0.0 is ideal).
    * ``pct_clean`` — share of cuts sitting in a silence or on a word edge (higher is better).
    * ``mean_dist_to_word_gap`` — mean frame distance from each cut to the nearest editorial-safe
      frame (0.0 when every cut is already clean).
    * ``pct_on_silence`` — share of cuts landing inside a detected **real audio silence** interval
      (the editor's ideal cut point). Only present when ``silence`` intervals are supplied.
    * ``pct_on_sentence_end`` — share of cuts landing exactly on a **sentence-boundary** frame (the
      end of a sentence). Only present when ``sentence_frames`` is supplied.
    * ``pct_on_speaker_turn`` — share of cuts landing exactly on a **speaker-change** frame (a
      diarization turn). Only present when ``speaker_frames`` is supplied.

    ``silence`` is an optional list of end-exclusive source-frame ranges ``[start, end)`` from
    :func:`laura.analysis.silence.detect_silence`. ``sentence_frames`` / ``speaker_frames`` are
    optional source-frame sets from :func:`laura.analysis.semantic.sentence_end_frames` /
    :func:`laura.analysis.semantic.speaker_turn_frames`. Each extra metric is reported only when its
    input is supplied, so existing callers (passing none of them) are unaffected.

    With no cuts the percentages are 0.0; with no words there is nothing to bisect, so every cut
    counts as clean and the mean distance is 0.0 (the metric is vacuously satisfied) — but the
    silence/sentence/speaker percentages are still measured against any supplied inputs.
    """
    n = len(cuts)
    if n == 0:
        metrics = {"pct_mid_word": 0.0, "pct_clean": 0.0, "mean_dist_to_word_gap": 0.0}
        if silence is not None:
            metrics["pct_on_silence"] = 0.0
        if sentence_frames is not None:
            metrics["pct_on_sentence_end"] = 0.0
        if speaker_frames is not None:
            metrics["pct_on_speaker_turn"] = 0.0
        return metrics

    if not words:
        metrics = {"pct_mid_word": 0.0, "pct_clean": 1.0, "mean_dist_to_word_gap": 0.0}
    else:
        safe = _safe_frames(words)
        mid_word = sum(1 for c in cuts if _covering_word(c, words) is not None)
        total_dist = sum(min(abs(s - c) for s in safe) for c in cuts)
        metrics = {
            "pct_mid_word": mid_word / n,
            "pct_clean": (n - mid_word) / n,
            "mean_dist_to_word_gap": total_dist / n,
        }

    if silence is not None:
        on_silence = sum(
            1 for c in cuts if any(start <= c < end for start, end in silence)
        )
        metrics["pct_on_silence"] = on_silence / n
    if sentence_frames is not None:
        metrics["pct_on_sentence_end"] = sum(1 for c in cuts if c in sentence_frames) / n
    if speaker_frames is not None:
        metrics["pct_on_speaker_turn"] = sum(1 for c in cuts if c in speaker_frames) / n
    return metrics
