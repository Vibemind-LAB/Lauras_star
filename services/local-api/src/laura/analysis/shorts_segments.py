"""Pure candidate-window generator for the auto-shorts backbone (no IO, no DB).

Given already-loaded analysis data (transcript words, fps as ``rate_num``/``rate_den``,
``total_frames``, and pre-computed ``sentence_end`` / ``speaker_turn`` frame sets from
:mod:`laura.analysis.semantic`) plus a min/max duration in seconds, enumerates every
``[start, end)`` window whose START and END both fall on a **legal boundary frame** (a
``sentence_end`` or ``speaker_turn`` frame) and whose duration in frames lies within
``[min_frames, max_frames]``.

Transcript-safe BY CONSTRUCTION: a legal boundary frame is a word ``end_frame``
(end-exclusive), so it sits *between* words and can never sever one.

Deterministic order: by ``start_frame`` then ``end_frame_exclusive``.

Invariants (mirroring the rest of Laura's editorial layer):

* All frames are integer source-frame indices. Float seconds are converted to frames
  **once** via ``round(s * rate_num / rate_den)`` and never carried as state.
* All ranges are end-exclusive: ``[start_frame, end_frame_exclusive)``.
* The two public module-level constants are the defaults for the UI / agent callers.
"""

from __future__ import annotations

import logging

from .editorial import Word
from .shorts_types import BoundaryKind, ShortCandidate

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_MIN_DURATION_S",
    "DEFAULT_MAX_DURATION_S",
    "legal_boundary_frames",
    "_duration_bounds_frames",
    "generate_candidates",
]

# Public defaults — the UI / agent callers see these as the product's natural short length.
DEFAULT_MIN_DURATION_S: float = 15.0
DEFAULT_MAX_DURATION_S: float = 60.0


def legal_boundary_frames(
    sentence_frames: set[int],
    speaker_frames: set[int],
) -> list[tuple[int, BoundaryKind]]:
    """Sorted union of all legal boundary frames, each tagged with its strongest kind.

    ``speaker_turn`` outranks ``sentence_end`` when a frame appears in both sets (a
    speaker-change is also a sentence end in many transcripts; we always surface the
    stronger seam). Sorted ascending by frame index.

    Returns an empty list when both input sets are empty.
    """
    all_frames: set[int] = sentence_frames | speaker_frames
    result: list[tuple[int, BoundaryKind]] = []
    for frame in sorted(all_frames):
        kind: BoundaryKind = "speaker_turn" if frame in speaker_frames else "sentence_end"
        result.append((frame, kind))
    return result


def _duration_bounds_frames(
    min_duration_s: float,
    max_duration_s: float,
    *,
    rate_num: int,
    rate_den: int,
) -> tuple[int, int]:
    """Convert ``[min_duration_s, max_duration_s]`` to an integer frame span range.

    Uses ``round(s * rate_num / rate_den)`` — the same rounding used elsewhere in Laura's
    silence and transcript layers. Raises :exc:`ValueError` for invalid inputs.

    Args:
        min_duration_s: Minimum short duration in seconds. Must be > 0.
        max_duration_s: Maximum short duration in seconds. Must be >= ``min_duration_s``.
        rate_num: Frame-rate numerator (e.g. 30 for 30 fps, 24000 for NTSC).
        rate_den: Frame-rate denominator (e.g. 1 for integer fps, 1001 for NTSC). Must be > 0.

    Returns:
        ``(min_frames, max_frames)`` as a pair of non-negative integers.

    Raises:
        ValueError: When ``min_duration_s <= 0``, ``max_duration_s <= 0``,
            ``min_duration_s > max_duration_s``, or ``rate_den <= 0``.
    """
    if rate_den <= 0:
        raise ValueError(f"rate_den must be > 0, got {rate_den!r}")
    if min_duration_s <= 0:
        raise ValueError(f"min_duration_s must be > 0, got {min_duration_s!r}")
    if max_duration_s <= 0:
        raise ValueError(f"max_duration_s must be > 0, got {max_duration_s!r}")
    if min_duration_s > max_duration_s:
        raise ValueError(
            f"min_duration_s ({min_duration_s}) must be <= max_duration_s ({max_duration_s})"
        )
    fps_ratio = rate_num / rate_den
    min_frames = max(1, round(min_duration_s * fps_ratio))
    max_frames = round(max_duration_s * fps_ratio)
    return min_frames, max_frames


def generate_candidates(
    words: list[Word],
    sentence_frames: set[int],
    speaker_frames: set[int],
    *,
    rate_num: int,
    rate_den: int,
    total_frames: int | None = None,
    min_duration_s: float = DEFAULT_MIN_DURATION_S,
    max_duration_s: float = DEFAULT_MAX_DURATION_S,
    max_candidates: int | None = None,
) -> list[ShortCandidate]:
    """Enumerate every in-range ``[start, end)`` candidate window whose endpoints are legal.

    A *legal boundary frame* is any member of ``sentence_frames | speaker_frames``.  These
    are always word ``end_frame`` values (end-exclusive from the transcript), so a cut there
    never bisects a spoken word — transcript safety is a construction-time guarantee, not a
    post-hoc filter.

    Duration bounds are converted from seconds to frames once via
    :func:`_duration_bounds_frames` and kept as integers throughout.

    When ``total_frames`` is provided, the boundary set is filtered differently for
    start vs. end positions: a frame is eligible as a **start** only if it is
    ``< total_frames`` (a short cannot begin on or after the asset end); a frame is
    eligible as an **end** (end-exclusive) if it is ``<= total_frames`` — a value of
    exactly ``total_frames`` is the canonical, legal way to close a short on the asset's
    final content frame, matching the convention in :mod:`laura.analysis.shorts_qa`.

    When ``max_candidates`` is not ``None``, at most that many candidates are emitted **per
    start frame**, keeping the *longest* in-range window for each start (ties broken by
    largest ``end_frame_exclusive``). With ``max_candidates=1`` this keeps one candidate per
    start: the widest window that still fits.

    Args:
        words: Transcript words in source-frame space.  Accepted for interface symmetry
            with the other shorts modules and reserved for future use; transcript safety
            is guaranteed by construction from the input boundary frame sets, so this
            parameter is not read by the current implementation.
        sentence_frames: Pre-computed sentence-end frame set from
            :func:`laura.analysis.semantic.sentence_end_frames`.
        speaker_frames: Pre-computed speaker-turn frame set from
            :func:`laura.analysis.semantic.speaker_turn_frames`.
        rate_num: Frame-rate numerator.
        rate_den: Frame-rate denominator (must be > 0).
        total_frames: Total frame count of the asset.  Start boundaries ``>= total_frames``
            are excluded; end boundaries ``> total_frames`` are excluded (``== total_frames``
            is legal — it closes on the last content frame).  ``None`` means no clamping.
        min_duration_s: Minimum short duration in seconds (default 15.0).
        max_duration_s: Maximum short duration in seconds (default 60.0).
        max_candidates: If set, emit at most this many candidates per start frame (longest
            windows first). ``None`` emits all in-range windows.

    Returns:
        A list of :class:`~laura.analysis.shorts_types.ShortCandidate` instances, sorted by
        ``(start_frame, end_frame_exclusive)``. Returns ``[]`` when there are fewer than two
        distinct legal boundary frames or no in-range pair exists.
    """
    min_frames, max_frames = _duration_bounds_frames(
        min_duration_s, max_duration_s, rate_num=rate_num, rate_den=rate_den
    )

    # Build the sorted list of (frame, kind) pairs and optionally clamp to asset length.
    # Invariant: end-exclusive ranges — a boundary at exactly total_frames is the canonical
    # legal way to close a short on the asset's last content frame (matches shorts_qa.py).
    # Therefore: START eligibility requires f < total_frames; END eligibility allows
    # f <= total_frames.  We build the full (unfiltered) sorted list first so we can
    # consult it for both roles, then derive per-role sets.
    boundaries = legal_boundary_frames(sentence_frames, speaker_frames)
    if total_frames is not None:
        # Keep only frames that are legal in at least one role.
        boundaries = [(f, k) for f, k in boundaries if f <= total_frames]
        start_eligible: set[int] = {f for f, _ in boundaries if f < total_frames}
        end_eligible: set[int] = {f for f, _ in boundaries if f <= total_frames}
    else:
        start_eligible = {f for f, _ in boundaries}
        end_eligible = {f for f, _ in boundaries}

    if not start_eligible or not end_eligible:
        logger.debug(
            "shorts_segments: fewer than 2 legal boundary frames after clamping "
            "(total_frames=%r) — returning []",
            total_frames,
        )
        return []

    # Build a lookup: frame -> kind for O(1) tag retrieval.
    kind_of: dict[int, BoundaryKind] = {f: k for f, k in boundaries}
    # Sorted frames eligible as START positions.
    sorted_starts = sorted(start_eligible)
    # Sorted frames eligible as END positions (superset when total_frames is set).
    sorted_ends = sorted(end_eligible)

    candidates: list[ShortCandidate] = []

    for start in sorted_starts:
        start_kind = kind_of[start]
        # Collect all valid end frames for this start (sorted ascending, all > start).
        valid_ends: list[int] = []
        for end in sorted_ends:
            span = end - start
            if span < min_frames:
                continue  # too short — keep looking (a later frame may be in range)
            if span > max_frames:
                break  # sorted: all further ends are also too long
            valid_ends.append(end)

        if not valid_ends:
            continue

        # Apply max_candidates cap: keep the longest (largest end) windows first.
        if max_candidates is not None and len(valid_ends) > max_candidates:
            # Sort descending by end frame (= descending by duration from fixed start).
            valid_ends_capped = sorted(valid_ends, reverse=True)[:max_candidates]
            # Re-sort ascending so output remains (start, end) ordered.
            valid_ends_to_emit = sorted(valid_ends_capped)
        else:
            valid_ends_to_emit = valid_ends

        for end in valid_ends_to_emit:
            end_kind = kind_of[end]
            candidates.append(
                ShortCandidate(
                    start_frame=start,
                    end_frame_exclusive=end,
                    start_boundary=start_kind,
                    end_boundary=end_kind,
                )
            )

    logger.debug(
        "shorts_segments: generated %d candidates from %d legal boundary frames",
        len(candidates),
        len(boundaries),
    )
    return candidates
