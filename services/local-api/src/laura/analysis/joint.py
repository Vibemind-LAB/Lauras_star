"""Joint visual + editorial cut placement — ONE decision, not two snaps.

The visual stage (:func:`laura.analysis.refine.snap_boundaries`) places a cut on the frame
where the *picture* changes the most (``argmax(d)``). The editorial stage
(:func:`laura.analysis.editorial.align_cut`) then *separately* drags that cut onto the nearest
transcript word-gap. Run back-to-back, the second snap is all-or-nothing: it will trade away a
frame-exact visual peak to reach an editorially clean edge even when the clean edge is visually
dead. A real test showed exactly this — moving a cut 270 -> 273 went visual ``1.0`` -> editorial
clean but visual ``0.0``. The picture-vs-speech tradeoff was being resolved blindly in favour of
speech.

This module fuses the two into a **single per-cut quality maximisation**. For every candidate
frame ``f`` in ``[cut-window, cut+window]`` we score:

* ``visual_score(f)`` — the per-frame luma-change signal ``d(f)`` normalised to the window's max
  (so ``1.0`` sits on the visual peak, ``0.0`` where the picture is still). This is exactly the
  signal :mod:`laura.analysis.eval_cut` scores exactness against.
* ``editorial_score(f)`` — a tier over how good a place ``f`` is to *cut*, best -> worst:
  ``1.0`` inside a detected **real audio silence** (a breath / inter-sentence beat — the
  editor's actual target), ``0.85`` on a clean word-gap / word edge that is not a detected
  silence (editorially safe, but only an ASR proxy for a pause), and ``0.0`` when ``f`` bisects a
  spoken word. The silence intervals are optional: with no ``silence`` supplied the term collapses
  to the historical hard ``1.0`` (clean) / ``0.0`` (mid-word) verdict. We do not grade by
  distance-to-gap because the *whole point* is that a clean frame three frames away can be worth
  more than the visual peak, and that is captured by the blend weight, not by softening the term.

The chosen frame maximises the normalised blend

    score(f) = (w_visual * visual_score(f) + w_editorial * editorial_score(f))
               / (w_visual + w_editorial)

Ties resolve to the frame **closest to the original** ``cut_frame`` (least disruption; on a
further tie, the lower frame). With ``w_editorial == 0`` this is exactly the visual peak snap
(== ``refine`` behaviour); with empty ``words`` every frame is vacuously clean so the editorial
term is constant and the result again collapses to the visual peak. So joint placement is a
strict generalisation: it only ever *adds* the ability to trade a little visual strength for a
clean edge when — and only when — the blend says the trade is worth it.

The ``cut_frame``, ``words`` (``[start_frame, end_frame)``, end-exclusive) and the returned frame
all live in the asset's source-frame space, like everywhere else in Laura. The frame IO is
injected through ``frame_loader`` (mirroring ``eval_cut``/``refine``) so callers can either hand
in a precomputed ``diff`` signal or let this module decode it — and tests run without ffmpeg.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .editorial import Word, _covering_word
from .eval_cut import (
    DEFAULT_WINDOW,
    FrameLoader,
    _diff_signal,
    load_gray_frames_ffmpeg,
)

# Editorial-quality tiers for a candidate cut frame, best -> worst. The blend in
# ``joint_place`` weighs ``editorial_score(f)`` against the normalised visual score, so these are
# the relative editorial *desirabilities* of where a cut can land. A cut is best when it falls on a
# narrative seam (a speaker change or the end of a sentence) that also happens to be acoustically
# safe; failing that, on a real pause; failing that, on any clean word edge:
#
#   1.00  SPEAKER_TURN  f is a clean frame on a diarization speaker change — the strongest seam:
#                       the cut switches cleanly from one voice to another.
#   0.95  SENTENCE_END  f is a clean frame on a sentence boundary (".?!…") — a natural narrative
#                       break, far better than an arbitrary word-gap mid-thought.
#   0.85  SILENCE       f sits inside a detected real audio silence (a breath / inter-sentence
#                       beat) with no semantic seem on it — the editor's acoustic target.
#   0.70  WORD_EDGE     f is a clean word-gap / word edge (no word severed) but carries no semantic
#                       or silence signal. Editorially safe, but only a bare ASR proxy for a pause.
#   0.00  MID_WORD      f bisects a spoken word -> clipped, unprofessional. Never desirable.
#
# Backward compatibility: when NO richer context is supplied (no ``silence`` AND no semantic frame
# sets), the score collapses to exactly the original 1.0 (clean) / 0.0 (mid-word) verdict — the
# WORD_EDGE / SILENCE / semantic tiers are simply unreachable. The lesser 0.70 / 0.85 clean tiers
# only ever apply once *some* richer signal exists to rank against, so adding semantics never
# changes a caller that passes none of it.
_SCORE_SPEAKER_TURN = 1.0
_SCORE_SENTENCE_END = 0.95
_SCORE_SILENCE = 0.85
_SCORE_WORD_EDGE = 0.70
_SCORE_MID_WORD = 0.0

# The historical "clean" score when there is no richer context at all (no silence, no semantics):
# a clean frame is simply 1.0 and a mid-word frame 0.0, byte-for-byte the pre-silence scorer.
_SCORE_CLEAN_BARE = 1.0


def _editorial_clean(frame: int, words: Sequence[Word]) -> bool:
    """``True`` when ``frame`` does not bisect any word (in a gap / on an edge).

    Empty ``words`` -> vacuously clean (there is no speech to sever). Mirrors the clean/mid-word
    split of :func:`laura.analysis.editorial.editorial_metrics` exactly.
    """
    if not words:
        return True
    return _covering_word(frame, list(words)) is None


def _in_silence(frame: int, silence: Sequence[tuple[int, int]] | None) -> bool:
    """``True`` when ``frame`` lies inside a half-open silence interval ``[start, end)``.

    ``silence`` ranges are end-exclusive source-frame spans (see
    :func:`laura.analysis.silence.detect_silence`). ``None``/empty -> no silence info -> ``False``.
    """
    if not silence:
        return False
    return any(start <= frame < end for start, end in silence)


def _editorial_score(
    frame: int,
    words: Sequence[Word],
    silence: Sequence[tuple[int, int]] | None,
    sentence_frames: frozenset[int] | None = None,
    speaker_frames: frozenset[int] | None = None,
) -> float:
    """The tiered editorial desirability of cutting at ``frame``, best -> worst.

    * ``1.0`` (SPEAKER_TURN) — a clean frame on a diarization speaker change.
    * ``0.95`` (SENTENCE_END) — a clean frame on a sentence boundary (``.?!…``).
    * ``0.85`` (SILENCE) — inside a detected real audio silence, no semantic seam on it.
    * ``0.70`` (WORD_EDGE) — a clean word-gap / word edge with no semantic or silence signal.
    * ``0.0`` (MID_WORD) — ``frame`` bisects a spoken word.

    A semantic seam (speaker turn / sentence end) only counts when ``frame`` is also editorially
    clean — we never reward cutting *into* a word just because a sentence happens to end there.

    Backward compatibility: when no richer context is supplied at all (``silence`` ``None``/empty
    AND both semantic sets ``None``/empty), the result collapses to exactly the pre-silence scorer
    — ``1.0`` for a clean frame, ``0.0`` for a mid-word frame. The lesser SILENCE / WORD_EDGE clean
    tiers only apply once some richer signal exists to rank against.
    """
    clean = _editorial_clean(frame, words)
    if clean:
        # Semantic seams rank highest, but only on an acoustically clean frame.
        if speaker_frames and frame in speaker_frames:
            return _SCORE_SPEAKER_TURN
        if sentence_frames and frame in sentence_frames:
            return _SCORE_SENTENCE_END
    if _in_silence(frame, silence):
        return _SCORE_SILENCE
    if clean:
        # A bare clean frame. With NO richer context (no silence, no semantics) keep the historical
        # 1.0 so that path is byte-for-byte the old scorer; once any richer signal exists, demote it
        # to the lesser WORD_EDGE tier so a silence / sentence-end / speaker-turn outranks it.
        has_context = bool(silence) or bool(sentence_frames) or bool(speaker_frames)
        return _SCORE_WORD_EDGE if has_context else _SCORE_CLEAN_BARE
    return _SCORE_MID_WORD


def _candidate_range(
    cut_frame: int, window: int, total_frames: int | None
) -> tuple[int, int]:
    """The inclusive ``[lo, hi]`` band of candidate cut frames, clamped to the valid range.

    Like ``eval_cut._evaluate_one`` / ``refine._snap_one``: the first usable candidate is frame 1
    (``d`` is undefined at frame 0, which has no predecessor), and ``hi`` is clamped below
    ``total_frames`` when known so we never propose a frame past the asset.
    """
    lo = max(cut_frame - window, 1)
    hi = cut_frame + window
    if total_frames is not None:
        hi = min(hi, total_frames - 1)
    return lo, hi


def _visual_scores(
    video_path: Path | str,
    lo: int,
    hi: int,
    *,
    frame_loader: FrameLoader,
) -> dict[int, float] | None:
    """Normalised ``d(f)`` per candidate frame in ``[lo, hi]`` (peak -> 1.0); ``None`` on IO fail.

    Decodes ``[lo-1, hi]`` so ``d`` is defined on the first candidate, computes the inter-frame
    luma diff, then scales by the window max so the strongest change is ``1.0``. A flat window
    (max ``0``) yields all-``0.0`` visual scores, which lets the editorial term decide alone.
    """
    if hi < lo:
        return None
    load_lo = lo - 1  # one predecessor for d(lo)
    load_hi = hi + 1  # end-exclusive
    try:
        frames = frame_loader(video_path, load_lo, load_hi)
    except Exception:
        # IO / decode error -> caller falls back to the editorial-only / unchanged path.
        return None
    if len(frames) < 2:
        return None
    diffs = _diff_signal(frames)  # diffs[k] -> candidate frame load_lo+1+k == lo+k
    peak = max(diffs)
    scores: dict[int, float] = {}
    for k, d in enumerate(diffs):
        frame = lo + k
        if frame > hi:
            break
        scores[frame] = (d / peak) if peak > 0 else 0.0
    return scores


def joint_place(
    cut_frame: int,
    words: list[Word],
    diff: Sequence[float] | None = None,
    *,
    window: int = DEFAULT_WINDOW,
    w_visual: float = 0.6,
    w_editorial: float = 0.4,
    silence: list[tuple[int, int]] | None = None,
    sentence_frames: set[int] | None = None,
    speaker_frames: set[int] | None = None,
    video_path: Path | str | None = None,
    total_frames: int | None = None,
    frame_loader: FrameLoader = load_gray_frames_ffmpeg,
) -> tuple[int, float]:
    """Place a cut by maximising one blended visual + editorial quality over the window.

    Scans candidate frames ``f`` in ``[cut_frame-window, cut_frame+window]`` (clamped to a valid
    frame range) and returns the ``(frame, score)`` that maximises

        score(f) = (w_visual*visual_score(f) + w_editorial*editorial_score(f))
                   / (w_visual + w_editorial)

    where ``visual_score(f)`` is ``d(f)`` normalised to the window peak (``1.0`` at the visual
    peak) and ``editorial_score(f)`` is a tier over how good a place ``f`` is to cut:

    * ``1.0`` when ``f`` is a clean frame on a **speaker change** (a ``speaker_frames`` turn) — the
      strongest seam: the cut switches cleanly between two voices.
    * ``0.95`` when ``f`` is a clean frame on a **sentence boundary** (a ``sentence_frames`` end) —
      a natural narrative break.
    * ``0.85`` when ``f`` lies inside a detected **real audio silence** (``silence`` intervals) with
      no semantic seam on it — the editor's acoustic target (a breath / inter-sentence beat).
    * ``0.70`` when ``f`` is a clean word-gap / word edge (no word severed) carrying no semantic or
      silence signal — editorially safe, but only a bare ASR proxy for a pause.
    * ``0.0`` when ``f`` bisects a spoken word.

    ``silence`` is an optional list of end-exclusive source-frame silence ranges ``[start, end)``
    from :func:`laura.analysis.silence.detect_silence`. ``sentence_frames`` / ``speaker_frames`` are
    optional source-frame sets from :func:`laura.analysis.semantic.sentence_end_frames` /
    :func:`laura.analysis.semantic.speaker_turn_frames`. When ALL of these are ``None``/empty the
    richer tiers are unreachable and the editorial term collapses to exactly the historical ``1.0``
    (clean) / ``0.0`` (mid-word) scoring — so callers that pass none of them are fully
    backward-compatible.

    The per-frame visual signal comes from one of two seams (mirroring ``eval_cut``):

    * **precomputed** — pass ``diff`` as the inter-frame change values aligned to the candidate
      band, i.e. ``diff[i]`` is ``d`` at frame ``lo+i`` where ``lo`` is the first candidate
      (``max(cut_frame-window, 1)``). Pure, no IO — used by the unit tests.
    * **decoded** — pass ``video_path`` (and optionally ``total_frames``) and the frames are
      decoded via ``frame_loader`` around the cut. ``frame_loader`` defaults to the ffmpeg loader.

    Behaviour at the edges:

    * ``w_editorial == 0`` -> pure visual peak (exactly ``refine``'s choice).
    * empty ``words`` -> every frame is vacuously clean, the editorial term is constant, and the
      result collapses to the visual peak — graceful, identical to the visual-only result today.
    * no usable visual signal (no ``diff``, no readable video, or a degenerate window) -> visual
      scores are treated as all-zero, so the editorial term alone decides; if that is also flat
      (clean everywhere / empty words) the original ``cut_frame`` is returned unchanged.

    Ties (equal blended score) resolve to the frame nearest ``cut_frame`` (least disruption), and
    on a further tie to the lower frame. ``Raises`` ``ValueError`` for a negative ``window`` or a
    non-positive weight sum.
    """
    if window < 0:
        raise ValueError("window must be >= 0")
    if w_visual < 0 or w_editorial < 0:
        raise ValueError("weights must be non-negative")
    weight_sum = w_visual + w_editorial
    if weight_sum <= 0:
        raise ValueError("at least one weight must be positive")

    # Freeze the semantic frame sets once so membership tests in the hot loop are cheap and the
    # scorer never mutates the caller's sets. ``None``/empty -> ``None`` (no semantic signal).
    sentences = frozenset(sentence_frames) if sentence_frames else None
    speakers = frozenset(speaker_frames) if speaker_frames else None

    lo, hi = _candidate_range(cut_frame, window, total_frames)
    if hi < lo:
        # No valid candidate in range (e.g. cut at frame 0 with window 0) -> leave it in place.
        return cut_frame, _blended_score(
            cut_frame, words, silence, sentences, speakers,
            {}, w_visual, w_editorial, weight_sum,
        )

    # Resolve the per-frame visual scores, normalised so the window peak == 1.0.
    visual: dict[int, float]
    if diff is not None:
        peak = max(diff) if len(diff) else 0.0
        visual = {
            lo + i: (float(d) / peak if peak > 0 else 0.0)
            for i, d in enumerate(diff)
            if lo + i <= hi
        }
    elif video_path is not None:
        decoded = _visual_scores(video_path, lo, hi, frame_loader=frame_loader)
        visual = decoded if decoded is not None else {}
    else:
        visual = {}

    best_frame = cut_frame
    best_score = -1.0
    best_dist = window + 1
    for f in range(lo, hi + 1):
        score = _blended_score(
            f, words, silence, sentences, speakers,
            visual, w_visual, w_editorial, weight_sum,
        )
        dist = abs(f - cut_frame)
        # Strictly-better score wins; on a tie prefer the least-disruptive (nearest, then lower)
        # frame so a clean edge only displaces the visual peak when it is genuinely worth more.
        if score > best_score or (score == best_score and dist < best_dist):
            best_frame, best_score, best_dist = f, score, dist
    return best_frame, best_score


def _blended_score(
    frame: int,
    words: Sequence[Word],
    silence: Sequence[tuple[int, int]] | None,
    sentence_frames: frozenset[int] | None,
    speaker_frames: frozenset[int] | None,
    visual: dict[int, float],
    w_visual: float,
    w_editorial: float,
    weight_sum: float,
) -> float:
    """The normalised visual+editorial blend at one ``frame`` (visual defaults to 0 when absent).

    The editorial term is the speaker-turn>sentence-end>silence>word-edge>mid-word tier from
    :func:`_editorial_score`.
    """
    v = visual.get(frame, 0.0)
    e = _editorial_score(frame, words, silence, sentence_frames, speaker_frames)
    return (w_visual * v + w_editorial * e) / weight_sum


def bias_to_weights(cut_bias: float | None) -> tuple[float, float]:
    """Map a ``cut_bias`` knob in ``[0, 1]`` to ``(w_visual, w_editorial)``.

    ``cut_bias`` is the editor's picture-vs-sound preference: ``0`` = picture-first (keep the
    frame-exact visual cut), ``1`` = sound-first (favour the clean word edge). ``None`` -> the
    product default ``(0.6, 0.4)`` (a great cut is both, leaning picture). The knob maps linearly
    so ``w_editorial == cut_bias`` and ``w_visual == 1 - cut_bias``; the blend normalises by the
    sum anyway, so only the ratio matters. Out-of-range values are clamped to ``[0, 1]``.
    """
    if cut_bias is None:
        return 0.6, 0.4
    b = min(1.0, max(0.0, cut_bias))
    return 1.0 - b, b
