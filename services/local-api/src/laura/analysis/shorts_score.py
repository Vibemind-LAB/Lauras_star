"""Pure multimodal CutScore per ShortCandidate — the auto-shorts backbone scorer.

Generalises :func:`laura.analysis.joint.joint_place`'s blended placement from a single cut to a
whole window with two cuts plus interior signals.  For each :class:`ShortCandidate` this module
computes **raw feature components**, applies **robust z-normalisation** (median/MAD) per feature
family across the candidate set, then blends with manual weights to produce one
:class:`ShortScore` per candidate.

Feature components
------------------
* ``transcript_safety``    — average :func:`_editorial_score` at the two cut frames.
* ``audio_silence_at_boundaries`` — fraction of the two cuts that land inside a detected silence.
* ``visual_boundary``      — average proximity of each cut to the nearest shot boundary (1.0 on
                             a shot edge, decaying with distance; neutral 0 when no shots).
* ``semantic``             — share of cuts on a ``speaker_turn`` / ``sentence_end`` seam.
* ``hook_position``        — reward a strong opening seam: ``speaker_turn`` > ``sentence_end``.
* ``length_fit``           — triangular peak at ~30 s, within ``[0, 1]``.
* ``speech_density``       — spoken frames / total frames inside the window from word spans.
* ``visual_shift``         — clean visual change at the two cuts (``1 - cosine`` of the nearest
                             frame embeddings on either side of each cut, averaged).  Neutral
                             ``0`` when no frame embeddings are supplied (VE1/VE2 not run).
* ``visual_continuity``    — mean cosine of consecutive embeddings inside the window: rewards a
                             visually coherent short.  Neutral ``0`` without embeddings.

Penalties (subtractive)
-----------------------
* ``word_interruption``    — **HARD** constraint: if either cut bisects a word the candidate is
                             rejected (``rejected=True``, ``reject_reason='word_interruption'``,
                             ``total=-inf``).  Implemented via
                             :func:`laura.analysis.editorial.editorial_metrics`.
* ``audio_jump``           — caller-supplied soft penalty (loudness discontinuity).
* ``face_motion``          — caller-supplied soft penalty.
* ``duplicate_penalty``    — **batch-level** soft penalty: the maximum cosine similarity of this
                             candidate's mean segment-embedding to any *other* candidate's, so two
                             near-identical shorts cannot both rank high.  ``0`` without embeddings
                             or with a single candidate.

Graceful degradation
--------------------
Every optional signal (shots, silence, diarisation, face) degrades to a **neutral contribution**
when absent — the backend must run without GPU/models and without ffmpeg.

Normalisation
-------------
Each feature column is normalised with robust-z (``(v-median)/(1.4826*MAD)``).  When MAD is 0 or
there is only one candidate the z-score is 0 — no division-by-zero, no inf/nan.

Nothing in this module does IO, decodes frames, imports a heavy model, or writes to a DB.  It is
pure, deterministic, and side-effect free.
"""

from __future__ import annotations

import bisect
import logging
import math
from dataclasses import dataclass
from statistics import median

import numpy as np

from .editorial import Word, editorial_metrics
from .joint import (
    _SCORE_SENTENCE_END,
    _SCORE_SPEAKER_TURN,
    _editorial_score,
    _in_silence,
)
from .shorts_types import ShortCandidate, ShortScore
from .types import ShotResult

__all__ = [
    "ScoreWeights",
    "DEFAULT_WEIGHTS",
    "IDEAL_DURATION_S",
    "robust_z",
    "score_candidate_features",
    "score_candidates",
]

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IDEAL_DURATION_S: float = 30.0

# Decay constant for visual_boundary: distance in frames where the score halves.
# At 25 fps ~12 frames ≈ 0.5 s feels natural; override-able only internally.
_VB_HALF_LIFE_FRAMES: int = 12


# ---------------------------------------------------------------------------
# Weight dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoreWeights:
    """Manual blend weights for the multimodal score.

    ``transcript_safety`` is intentionally the highest weight — clean boundaries are the
    product's core promise.  All weights are non-negative; ``word_interruption`` is a HARD
    gate whose weight documents intent but is never used in a soft blend.
    """

    transcript_safety: float = 1.0
    audio_silence_at_boundaries: float = 0.85
    visual_boundary: float = 0.6
    semantic: float = 0.95
    hook_position: float = 0.5
    length_fit: float = 0.4
    speech_density: float = 0.3
    visual_shift: float = 0.5
    visual_continuity: float = 0.3
    word_interruption: float = 1.0  # hard; weight documents intent, rejection is absolute
    audio_jump: float = 0.5
    face_motion: float = 0.3
    duplicate_penalty: float = 0.6


DEFAULT_WEIGHTS: ScoreWeights = ScoreWeights()

# The complete ordered list of component keys (used to ensure breakdown completeness).
_ALL_KEYS: list[str] = [
    "transcript_safety",
    "audio_silence_at_boundaries",
    "visual_boundary",
    "semantic",
    "hook_position",
    "length_fit",
    "speech_density",
    "visual_shift",
    "visual_continuity",
    "word_interruption",
    "audio_jump",
    "face_motion",
    "duplicate_penalty",
]


# ---------------------------------------------------------------------------
# Robust z-normalisation
# ---------------------------------------------------------------------------


def robust_z(values: list[float]) -> list[float]:
    """Robust z-score: ``(v - median) / (1.4826 * MAD)`` for each value.

    Guarantees:
    * Empty list → ``[]``.
    * Single element → ``[0.0]`` (no spread to measure).
    * MAD == 0 (all identical) → ``[0.0, …]`` (avoid division by zero).
    """
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [0.0]

    med = median(values)
    mad = median([abs(v - med) for v in values])
    if mad == 0.0:
        return [0.0] * n

    scale = 1.4826 * mad
    return [(v - med) / scale for v in values]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _length_fit(
    duration_frames: int,
    *,
    rate_num: int,
    rate_den: int,
    ideal_s: float = IDEAL_DURATION_S,
    min_s: float,
    max_s: float,
) -> float:
    """Triangular length fitness peaking at ``ideal_s``, clamped to ``[0, 1]``.

    Returns 1.0 at the ideal duration, 0.0 at or beyond ``min_s`` / ``max_s``, with a linear
    ramp in between.  A candidate outside ``[min_s, max_s]`` returns 0.0.
    """
    den = rate_den if rate_den > 0 else 1
    num = rate_num if rate_num > 0 else 1
    duration_s = duration_frames * den / num

    if duration_s <= min_s or duration_s >= max_s:
        return 0.0

    if duration_s <= ideal_s:
        # Rising slope: 0 at min_s, 1 at ideal_s
        span = ideal_s - min_s
        if span <= 0.0:
            return 1.0
        return (duration_s - min_s) / span
    else:
        # Falling slope: 1 at ideal_s, 0 at max_s
        span = max_s - ideal_s
        if span <= 0.0:
            return 1.0
        return (max_s - duration_s) / span


def _visual_boundary_at(
    cut_frame: int,
    shots: list[ShotResult],
) -> float:
    """Proximity of ``cut_frame`` to the nearest shot boundary in ``shots`` (``[0, 1]``).

    A cut exactly on a shot edge (``src_in_frame`` or ``src_out_frame_exclusive``) returns 1.0.
    The score decays exponentially with frame distance (half-life = ``_VB_HALF_LIFE_FRAMES``).
    When ``shots`` is empty returns 0.0 (neutral).
    """
    if not shots:
        return 0.0

    min_dist: float = math.inf
    for shot in shots:
        dist_in = abs(cut_frame - shot.src_in_frame)
        dist_out = abs(cut_frame - shot.src_out_frame_exclusive)
        dist = min(dist_in, dist_out)
        if dist < min_dist:
            min_dist = dist

    if min_dist == 0:
        return 1.0
    # Exponential decay: score = 0.5^(dist / half_life) = exp(-dist * ln2 / half_life)
    return math.exp(-min_dist * math.log(2) / _VB_HALF_LIFE_FRAMES)


def _speech_density(
    start_frame: int,
    end_frame_exclusive: int,
    words: list[Word],
) -> float:
    """Fraction of frames inside ``[start_frame, end_frame_exclusive)`` covered by spoken words.

    Returns 0.0 when the window has no frames or when ``words`` is empty.  Words that partially
    overlap the window are clipped to the window boundary (only the part inside counts).
    """
    total = end_frame_exclusive - start_frame
    if total <= 0 or not words:
        return 0.0

    spoken = 0
    for w in words:
        # Overlap of [w.start_frame, w.end_frame) with [start_frame, end_frame_exclusive)
        lo = max(w.start_frame, start_frame)
        hi = min(w.end_frame, end_frame_exclusive)
        if hi > lo:
            spoken += hi - lo

    return min(1.0, spoken / total)


# ---------------------------------------------------------------------------
# Visual embedding helpers (VE4)
# ---------------------------------------------------------------------------
#
# These operate on a *sparse* frame -> embedding map (typically 1 fps plus shot
# boundaries) produced by the VE1/VE2 pipeline.  They are written so that an
# empty / absent map degrades to a strictly neutral ``0.0`` contribution: when
# ``embeddings`` is ``None`` or empty, ``frames_sorted`` is ``[]`` and every
# function below returns ``0.0`` / ``None`` — so the new columns are all-zero,
# their robust-z is ``0.0`` for every candidate, and ``total`` is byte-identical
# to the pre-VE4 behaviour.


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity of two 1-D vectors; ``0.0`` if either is a zero vector."""
    a_norm = float(np.linalg.norm(a))
    b_norm = float(np.linalg.norm(b))
    if a_norm == 0.0 or b_norm == 0.0:
        return 0.0
    return float(np.dot(a, b) / (a_norm * b_norm))


def _nearest_emb(
    frame: int,
    frames_sorted: list[int],
    embeddings: dict[int, np.ndarray],
    *,
    side: str,
) -> np.ndarray | None:
    """Nearest sampled embedding at-or-before / at-or-after ``frame``.

    ``side="before"`` returns the embedding of the greatest sampled frame ``<= frame``;
    ``side="after"`` returns the embedding of the least sampled frame ``>= frame``.
    Returns ``None`` when no embedding exists on the requested side (or at all).
    """
    if not frames_sorted:
        return None

    if side == "before":
        # rightmost sampled frame <= frame
        idx = bisect.bisect_right(frames_sorted, frame) - 1
        if idx < 0:
            return None
        return embeddings.get(frames_sorted[idx])
    if side == "after":
        # leftmost sampled frame >= frame
        idx = bisect.bisect_left(frames_sorted, frame)
        if idx >= len(frames_sorted):
            return None
        return embeddings.get(frames_sorted[idx])
    raise ValueError(f"side must be 'before' or 'after', got {side!r}")


def _visual_shift_at(
    cut: int,
    frames_sorted: list[int],
    embeddings: dict[int, np.ndarray],
) -> float:
    """``1 - cosine(before, after)`` at ``cut``; ``0.0`` when either side is missing.

    A large value means the visual content changes sharply across the cut (a clean
    visual seam); ``0`` means visually identical content (or no embedding context).
    """
    before = _nearest_emb(cut, frames_sorted, embeddings, side="before")
    after = _nearest_emb(cut, frames_sorted, embeddings, side="after")
    if before is None or after is None:
        return 0.0
    return 1.0 - _cosine(before, after)


def _visual_continuity(
    start: int,
    end_excl: int,
    frames_sorted: list[int],
    embeddings: dict[int, np.ndarray],
) -> float:
    """Mean cosine of consecutive embeddings whose frame is in ``[start, end_excl)``.

    Rewards a visually coherent short.  Returns ``0.0`` when fewer than two sampled
    embeddings fall inside the window (no pair to compare).
    """
    inside = [f for f in frames_sorted if start <= f < end_excl]
    if len(inside) < 2:
        return 0.0
    sims = [
        _cosine(embeddings[inside[i]], embeddings[inside[i + 1]])
        for i in range(len(inside) - 1)
    ]
    return float(sum(sims) / len(sims))


def _segment_repr(
    start: int,
    end_excl: int,
    frames_sorted: list[int],
    embeddings: dict[int, np.ndarray],
) -> np.ndarray | None:
    """Mean embedding of the frames inside ``[start, end_excl)``.

    Falls back to ``_nearest_emb(start, …, side="after")`` when no embedding lands
    inside the window, and to ``None`` only when there is no embedding at all on or
    after ``start``.  Used for the batch-level ``duplicate_penalty``.
    """
    inside = [f for f in frames_sorted if start <= f < end_excl]
    if inside:
        stacked = np.stack([embeddings[f] for f in inside], axis=0)
        return np.asarray(stacked.mean(axis=0), dtype=np.float32)
    return _nearest_emb(start, frames_sorted, embeddings, side="after")


# ---------------------------------------------------------------------------
# Per-candidate feature computation
# ---------------------------------------------------------------------------


def score_candidate_features(
    cand: ShortCandidate,
    words: list[Word],
    *,
    rate_num: int,
    rate_den: int,
    shots: list[ShotResult] | None = None,
    silence: list[tuple[int, int]] | None = None,
    sentence_frames: set[int] | None = None,
    speaker_frames: set[int] | None = None,
    audio_jump: float = 0.0,
    face_motion: float = 0.0,
    embeddings: dict[int, np.ndarray] | None = None,
    frames_sorted: list[int] | None = None,
    min_duration_s: float = 15.0,
    max_duration_s: float = 60.0,
) -> tuple[dict[str, float], bool, str | None]:
    """Compute raw feature components for one :class:`ShortCandidate`.

    Returns ``(raw_components, rejected, reject_reason)``.  When ``rejected`` is ``True``,
    ``reject_reason`` is the stable string ``'word_interruption'`` and the components dict
    still contains all keys (with ``word_interruption=1.0`` and everything else at its raw
    value) for debugging, but the caller must treat the candidate as discarded.

    Hard rejection check
    --------------------
    ``editorial_metrics`` is called with the two cut frames as the ``cuts`` list.  If
    ``pct_mid_word > 0`` (either frame bisects a word) the candidate is rejected.
    """
    start = cand.start_frame
    end = cand.end_frame_exclusive

    # --- Hard rejection check (editorial_metrics) ---
    cuts = [start, end]
    metrics = editorial_metrics(
        cuts,
        words,
        silence=silence,
        sentence_frames=sentence_frames,
        speaker_frames=speaker_frames,
    )
    if metrics["pct_mid_word"] > 0.0:
        # Build a partial component dict so breakdown is complete even for rejected candidates
        raw: dict[str, float] = {k: 0.0 for k in _ALL_KEYS}
        raw["word_interruption"] = 1.0
        return raw, True, "word_interruption"

    # Freeze frame-sets for O(1) membership
    sf = frozenset(sentence_frames) if sentence_frames else None
    spk = frozenset(speaker_frames) if speaker_frames else None

    # --- transcript_safety: average _editorial_score at both cuts ---
    ed_start = _editorial_score(start, words, silence, sf, spk)
    ed_end = _editorial_score(end, words, silence, sf, spk)
    transcript_safety = (ed_start + ed_end) / 2.0

    # --- audio_silence_at_boundaries: fraction of cuts inside a silence ---
    if silence is not None:
        n_in_silence = sum(1 for c in cuts if _in_silence(c, silence))
        audio_silence_val = n_in_silence / len(cuts)
    else:
        audio_silence_val = 0.0  # neutral when no silence info

    # --- visual_boundary: average proximity to a shot edge ---
    if shots is not None:
        vb_start = _visual_boundary_at(start, shots)
        vb_end = _visual_boundary_at(end, shots)
        visual_boundary = (vb_start + vb_end) / 2.0
    else:
        visual_boundary = 0.0  # neutral when no shots

    # --- semantic: share of cuts on a speaker_turn / sentence_end seam ---
    n_on_seam = 0
    for c in cuts:
        if (spk and c in spk) or (sf and c in sf):
            n_on_seam += 1
    semantic = n_on_seam / len(cuts)

    # --- hook_position: reward a strong opening seam ---
    # Verify actual frame membership rather than trusting the label string; a candidate
    # whose start_boundary label says 'speaker_turn' but whose start_frame is not in
    # speaker_frames must not receive full hook credit.
    if spk is not None and start in spk:
        hook_position = _SCORE_SPEAKER_TURN
    elif sf is not None and start in sf:
        hook_position = _SCORE_SENTENCE_END
    elif spk is None and sf is None:
        # No frame-set context provided: fall back to trusting the label (by-construction
        # invariant — callers that omit speaker_frames/sentence_frames are expected to
        # produce correct labels, e.g. the candidate generator that creates these sets).
        if cand.start_boundary == "speaker_turn":
            hook_position = _SCORE_SPEAKER_TURN
        elif cand.start_boundary == "sentence_end":
            hook_position = _SCORE_SENTENCE_END
        else:
            hook_position = 0.0
    else:
        hook_position = 0.0

    # --- length_fit ---
    lf = _length_fit(
        cand.duration_frames,
        rate_num=rate_num,
        rate_den=rate_den,
        ideal_s=IDEAL_DURATION_S,
        min_s=min_duration_s,
        max_s=max_duration_s,
    )

    # --- speech_density ---
    speech_density = _speech_density(start, end, words)

    # --- visual_shift / visual_continuity (VE4) -----------------------------
    # Graceful neutral: when no embeddings are supplied ``fs`` is empty and both
    # helpers return 0.0, so these columns are all-zero across the batch and their
    # robust-z is 0.0 — identical to the pre-VE4 behaviour. ``duplicate_penalty`` is
    # a batch-level signal filled by :func:`score_candidates`; here it is 0.0.
    if embeddings:
        fs = frames_sorted if frames_sorted is not None else sorted(embeddings)
        visual_shift = (
            _visual_shift_at(start, fs, embeddings)
            + _visual_shift_at(end, fs, embeddings)
        ) / 2.0
        visual_continuity = _visual_continuity(start, end, fs, embeddings)
    else:
        visual_shift = 0.0
        visual_continuity = 0.0

    # Penalties (soft, subtractive)
    raw_components: dict[str, float] = {
        "transcript_safety": transcript_safety,
        "audio_silence_at_boundaries": audio_silence_val,
        "visual_boundary": visual_boundary,
        "semantic": semantic,
        "hook_position": hook_position,
        "length_fit": lf,
        "speech_density": speech_density,
        "visual_shift": visual_shift,
        "visual_continuity": visual_continuity,
        "word_interruption": 0.0,   # hard gate: 0 means "not triggered"
        "audio_jump": audio_jump,
        "face_motion": face_motion,
        "duplicate_penalty": 0.0,   # batch-level: filled by score_candidates
    }
    return raw_components, False, None


# ---------------------------------------------------------------------------
# Batch scoring with robust-z normalisation
# ---------------------------------------------------------------------------


def score_candidates(
    candidates: list[ShortCandidate],
    words: list[Word],
    *,
    rate_num: int,
    rate_den: int,
    shots: list[ShotResult] | None = None,
    silence: list[tuple[int, int]] | None = None,
    sentence_frames: set[int] | None = None,
    speaker_frames: set[int] | None = None,
    audio_jumps: dict[int, float] | None = None,
    face_motions: dict[int, float] | None = None,
    embeddings: dict[int, np.ndarray] | None = None,
    weights: ScoreWeights = DEFAULT_WEIGHTS,
    min_duration_s: float = 15.0,
    max_duration_s: float = 60.0,
) -> list[ShortScore]:
    """Score all candidates and return one :class:`ShortScore` per input in the same order.

    Process
    -------
    1. Compute raw feature components for every candidate via
       :func:`score_candidate_features`.
    2. Apply **robust z-normalisation** column-by-column across the candidate set for each
       feature (rejected candidates contribute their raw value to the normalisation but
       receive ``total=-inf`` regardless).
    3. Blend the z-normalised components with ``weights`` to produce ``total``.  Soft
       penalties (``audio_jump``, ``face_motion``) are subtracted; ``word_interruption``
       forces ``total=-inf`` when non-zero.

    Ordering guarantee
    ------------------
    The returned list is in the same order as the input ``candidates`` list.  Ranking is the
    caller's responsibility.
    """
    n = len(candidates)
    if n == 0:
        return []

    # Precompute the sorted sampled-frame index once. Empty (None / no embeddings) →
    # the visual columns stay all-zero and the duplicate pass below is skipped, so
    # the result is byte-identical to the pre-VE4 behaviour.
    frames_sorted: list[int] = sorted(embeddings) if embeddings else []

    # Step 1: compute raw components for every candidate
    all_raw: list[dict[str, float]] = []
    all_rejected: list[bool] = []
    all_reasons: list[str | None] = []

    for i, cand in enumerate(candidates):
        aj = audio_jumps[i] if audio_jumps and i in audio_jumps else 0.0
        fm = face_motions[i] if face_motions and i in face_motions else 0.0
        raw, rejected, reason = score_candidate_features(
            cand,
            words,
            rate_num=rate_num,
            rate_den=rate_den,
            shots=shots,
            silence=silence,
            sentence_frames=sentence_frames,
            speaker_frames=speaker_frames,
            audio_jump=aj,
            face_motion=fm,
            embeddings=embeddings,
            frames_sorted=frames_sorted,
            min_duration_s=min_duration_s,
            max_duration_s=max_duration_s,
        )
        all_raw.append(raw)
        all_rejected.append(rejected)
        all_reasons.append(reason)

    # Step 1b: batch-level duplicate_penalty. For each candidate the penalty is the
    # max cosine similarity of its mean segment-embedding to ANY other candidate's;
    # two near-identical shorts cannot then both rank high. Skipped entirely when no
    # embeddings or a single candidate → penalty stays 0.0 for everyone (neutral).
    if embeddings and n > 1:
        reprs: list[np.ndarray | None] = [
            _segment_repr(
                cand.start_frame, cand.end_frame_exclusive, frames_sorted, embeddings
            )
            for cand in candidates
        ]
        for i in range(n):
            ri = reprs[i]
            if ri is None:
                continue  # no representation → leave penalty at 0.0
            best = 0.0
            for j in range(n):
                if j == i:
                    continue
                rj = reprs[j]
                if rj is None:
                    continue
                sim = _cosine(ri, rj)
                if sim > best:
                    best = sim
            all_raw[i]["duplicate_penalty"] = best

    # Step 2: robust z-normalisation per feature column
    z_by_key: dict[str, list[float]] = {}
    for key in _ALL_KEYS:
        col = [all_raw[i][key] for i in range(n)]
        z_by_key[key] = robust_z(col)

    # Step 3: assemble ShortScore per candidate
    results: list[ShortScore] = []

    for i in range(n):
        raw = all_raw[i]
        rejected = all_rejected[i]
        reason = all_reasons[i]

        # Build breakdown (post-z, pre-weight values) and components (pre-z raw values)
        breakdown: dict[str, float] = {key: z_by_key[key][i] for key in _ALL_KEYS}
        components: dict[str, float] = {key: raw[key] for key in _ALL_KEYS}

        if rejected:
            results.append(
                ShortScore(
                    total=-math.inf,
                    breakdown=breakdown,
                    components=components,
                    rejected=True,
                    reject_reason=reason,
                )
            )
            continue

        # Blend: positive features add, soft penalties subtract
        total = 0.0

        # Positive components (z-normalised, weighted)
        positive_keys = [
            "transcript_safety",
            "audio_silence_at_boundaries",
            "visual_boundary",
            "semantic",
            "hook_position",
            "length_fit",
            "speech_density",
            "visual_shift",
            "visual_continuity",
        ]
        for key in positive_keys:
            w = getattr(weights, key)
            total += w * breakdown[key]

        # Soft subtractive penalties (audio_jump, face_motion, duplicate_penalty)
        total -= weights.audio_jump * breakdown["audio_jump"]
        total -= weights.face_motion * breakdown["face_motion"]
        total -= weights.duplicate_penalty * breakdown["duplicate_penalty"]

        # word_interruption contributes 0 to total (it is a hard gate only)
        # Its breakdown entry is 0.0 for non-rejected candidates

        results.append(
            ShortScore(
                total=total,
                breakdown=breakdown,
                components=components,
                rejected=False,
                reject_reason=None,
            )
        )

    return results
