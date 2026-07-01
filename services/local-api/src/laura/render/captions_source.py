"""Derive karaoke caption words from a timeline (or a single source range) for the ASS builder.

Maps transcript words (source frames) to the target frame space using the same integer
affine transform that the desktop timeline bar applies, then merges punctuation
tokens into the preceding word.

Two entry points share one merge helper:

* :func:`timeline_caption_words` — walks every clip of a timeline and maps each word
  to *sequence* frames (the assembled multi-clip timeline space).
* :func:`candidate_caption_words` — maps the words of one ``[start, end)`` source range of
  a single asset to *clip-local* frames (frame 0 == the candidate's first frame). Used by
  the shorts renderer, where the output is exactly one trimmed clip.

Invariant: all frame values are integer end-exclusive (CLAUDE.md §1-2).
"""

from __future__ import annotations

from typing import Any

from ..db import repos
from ..db.database import Database

# (text, start_frame, end_frame) — end-exclusive. For timeline_caption_words these are
# sequence frames; for candidate_caption_words they are clip-local frames.
Word = tuple[str, int, int]


def _append_word(
    result: list[Word], *, text: str, start: int, end: int, is_punct: bool
) -> None:
    """Append a mapped word to *result*, merging punctuation into the preceding token.

    A punctuation token (``is_punct``) is appended to the last kept word's text and
    extends that word's end if larger; punctuation with no preceding word is dropped.
    A normal word is appended as a new token. Shared by both public functions so the
    merge rule stays identical.
    """
    if is_punct:
        if result:
            prev_text, prev_start, prev_end = result[-1]
            result[-1] = (prev_text + text, prev_start, max(prev_end, end))
        # else: punctuation with no preceding word — drop silently.
    else:
        result.append((text, start, end))


def timeline_caption_words(db: Database, timeline_id: str) -> list[Word]:
    """Return caption words for *timeline_id* mapped to sequence frames.

    Algorithm (per clip, in seq order):
    1. Resolve the asset's latest analysis run.  Skip the clip if none exists.
    2. Fetch all transcript words for that run (flat, ordered by start_frame).
    3. For each word whose ``start_frame`` falls inside the clip's source range:
       - Map to sequence frames via the integer affine formula.
       - If the word is punctuation, merge its text onto the last kept word and
         extend that word's seq_end if larger.  Drop punctuation that has no
         preceding kept word.
       - Otherwise append a new Word token.
    4. Return the full list (words are already in seq order because clips are
       seq-ordered and words within each clip are source-ordered).
    """
    clips = repos.list_timeline_clips(db, timeline_id)
    if not clips:
        return []

    result: list[Word] = []

    for clip in clips:
        asset_id: str = clip["asset_id"]
        src_in: int = clip["src_in_frame"]
        src_out: int = clip["src_out_frame_exclusive"]
        seq_in: int = clip["seq_in_frame"]

        # Mirror api/analysis.py:110 — use the latest run for the asset.
        run = repos.get_latest_analysis_run(db, asset_id)
        if run is None:
            continue

        words = repos.list_words_for_run(db, asset_id, run["id"])

        for w in words:
            w_start: int = int(w["start_frame"])
            w_end: int = int(w["end_frame"])

            # Only include words whose start falls inside the clip's source range.
            if not (src_in <= w_start < src_out):
                continue

            # SRC → SEQ mapping (integer affine, end-exclusive clamped to clip boundary).
            seq_start: int = seq_in + (w_start - src_in)
            src_end_clamped: int = min(w_end, src_out)
            seq_end: int = seq_in + (src_end_clamped - src_in)

            _append_word(
                result,
                text=w["text"],
                start=seq_start,
                end=seq_end,
                is_punct=bool(w["is_punctuation"]),
            )

    return result


def candidate_caption_words(
    db: Database,
    asset_id: str,
    run_id: str,
    start_frame: int,
    end_frame_exclusive: int,
) -> list[Word]:
    """Caption words for one ``[start_frame, end_frame_exclusive)`` source range, clip-local.

    The shorts renderer trims a single clip ``(source, start_frame, end_frame_exclusive)`` and
    burns captions onto it, so word timings must be expressed relative to the *trimmed clip*
    (frame 0 == ``start_frame``), not the source.

    Algorithm:
    1. Fetch all transcript words for ``(asset_id, run_id)`` (flat, source-ordered).
    2. Keep words whose ``start_frame`` falls inside ``[start_frame, end_frame_exclusive)``.
    3. Offset to clip-local frames (``local = src - start_frame``) and clamp to ``[0, dur)``
       where ``dur = end_frame_exclusive - start_frame``.
    4. Merge punctuation into the preceding word (same rule as
       :func:`timeline_caption_words`).
    Returns the words in clip-local start order (already sorted: source words are ordered and
    the offset is monotonic). Empty when no run/words overlap the range.
    """
    duration: int = end_frame_exclusive - start_frame
    if duration <= 0:
        return []

    words: list[dict[str, Any]] = repos.list_words_for_run(db, asset_id, run_id)
    result: list[Word] = []

    for w in words:
        w_start: int = int(w["start_frame"])
        w_end: int = int(w["end_frame"])

        # Only include words whose start falls inside the candidate's source range.
        if not (start_frame <= w_start < end_frame_exclusive):
            continue

        # SRC → clip-local (frame 0 == start_frame), end clamped to the clip length.
        local_start: int = w_start - start_frame
        local_end: int = min(w_end, end_frame_exclusive) - start_frame
        # Clamp into [0, duration]; start can never be < 0 given the filter above.
        local_start = max(0, min(local_start, duration))
        local_end = max(0, min(local_end, duration))

        _append_word(
            result,
            text=w["text"],
            start=local_start,
            end=local_end,
            is_punct=bool(w["is_punctuation"]),
        )

    return result
