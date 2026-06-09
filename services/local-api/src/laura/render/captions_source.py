"""Derive karaoke caption words from a timeline for the ASS builder.

Maps transcript words (source frames) to sequence frames using the same integer
affine transform that the desktop timeline bar applies, then merges punctuation
tokens into the preceding word.

Invariant: all frame values are integer end-exclusive (CLAUDE.md §1-2).
"""

from __future__ import annotations

from ..db import repos
from ..db.database import Database

# (text, seq_start_frame, seq_end_frame) — end-exclusive
Word = tuple[str, int, int]


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
            is_punct: bool = bool(w["is_punctuation"])

            # Only include words whose start falls inside the clip's source range.
            if not (src_in <= w_start < src_out):
                continue

            # SRC → SEQ mapping (integer affine, end-exclusive clamped to clip boundary).
            seq_start: int = seq_in + (w_start - src_in)
            src_end_clamped: int = min(w_end, src_out)
            seq_end: int = seq_in + (src_end_clamped - src_in)

            text: str = w["text"]

            if is_punct:
                if result:
                    # Merge into preceding word: append text, extend end if later.
                    prev_text, prev_start, prev_end = result[-1]
                    new_end = max(prev_end, seq_end)
                    result[-1] = (prev_text + text, prev_start, new_end)
                # else: punctuation with no preceding word — drop silently.
            else:
                result.append((text, seq_start, seq_end))

    return result
