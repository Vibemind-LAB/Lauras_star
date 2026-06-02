"""Pure timeline operations on EditClip lists (frame-accurate, end-exclusive).

Every function returns a NEW clip list (no mutation), so a sequence of operations
yields deterministic, testable deltas. Source ranges are kept in sync with sequence
ranges when clips are trimmed (1:1 mapping; speed changes are out of MVP scope).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from ..timebase import FrameRange


@dataclass(frozen=True)
class EditClip:
    asset_id: str
    src_in_frame: int
    src_out_frame_exclusive: int
    seq_in_frame: int
    seq_out_frame_exclusive: int
    lane: int = 0
    speaker_id: str | None = None
    origin_word_start_id: str | None = None
    origin_word_end_id: str | None = None

    @property
    def src_length(self) -> int:
        return self.src_out_frame_exclusive - self.src_in_frame

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> EditClip:
        return cls(
            asset_id=row["asset_id"],
            src_in_frame=row["src_in_frame"],
            src_out_frame_exclusive=row["src_out_frame_exclusive"],
            seq_in_frame=row["seq_in_frame"],
            seq_out_frame_exclusive=row["seq_out_frame_exclusive"],
            lane=row.get("lane", 0),
            speaker_id=row.get("speaker_id"),
            origin_word_start_id=row.get("origin_word_start_id"),
            origin_word_end_id=row.get("origin_word_end_id"),
        )

    def to_row(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "src_in_frame": self.src_in_frame,
            "src_out_frame_exclusive": self.src_out_frame_exclusive,
            "seq_in_frame": self.seq_in_frame,
            "seq_out_frame_exclusive": self.seq_out_frame_exclusive,
            "lane": self.lane,
            "speaker_id": self.speaker_id,
            "origin_word_start_id": self.origin_word_start_id,
            "origin_word_end_id": self.origin_word_end_id,
        }


def sequence_length(clips: list[EditClip]) -> int:
    return max((c.seq_out_frame_exclusive for c in clips), default=0)


def ordered(clips: list[EditClip]) -> list[EditClip]:
    return sorted(clips, key=lambda c: (c.seq_in_frame, c.lane))


def append_clip(clips: list[EditClip], clip: EditClip) -> list[EditClip]:
    """Append a clip at the end of the sequence (its src length sets the duration)."""
    start = sequence_length(clips)
    placed = replace(clip, seq_in_frame=start, seq_out_frame_exclusive=start + clip.src_length)
    return [*clips, placed]


def insert_clip(clips: list[EditClip], clip: EditClip, at_seq_frame: int) -> list[EditClip]:
    """Insert a clip at ``at_seq_frame``, rippling later clips to the right."""
    dur = clip.src_length
    shifted = [
        replace(c, seq_in_frame=c.seq_in_frame + dur,
                seq_out_frame_exclusive=c.seq_out_frame_exclusive + dur)
        if c.seq_in_frame >= at_seq_frame
        else c
        for c in clips
    ]
    placed = replace(clip, seq_in_frame=at_seq_frame, seq_out_frame_exclusive=at_seq_frame + dur)
    return [*shifted, placed]


def remove_range(
    clips: list[EditClip], seq_in: int, seq_out: int, *, ripple: bool
) -> list[EditClip]:
    """Remove a sequence range. ``ripple`` closes the gap; otherwise it is a lift."""
    if seq_out < seq_in:
        raise ValueError("seq_out must be >= seq_in")
    rng = FrameRange(seq_in, seq_out)
    pieces: list[EditClip] = []
    for clip in ordered(clips):
        clip_range = FrameRange(clip.seq_in_frame, clip.seq_out_frame_exclusive)
        if not clip_range.overlaps(rng):
            pieces.append(clip)
            continue
        for sub in clip_range.subtract(rng):
            offset = sub.start - clip.seq_in_frame
            pieces.append(
                replace(
                    clip,
                    seq_in_frame=sub.start,
                    seq_out_frame_exclusive=sub.end_exclusive,
                    src_in_frame=clip.src_in_frame + offset,
                    src_out_frame_exclusive=clip.src_in_frame + offset + sub.length,
                )
            )
    if ripple:
        amount = seq_out - seq_in
        pieces = [
            replace(c, seq_in_frame=c.seq_in_frame - amount,
                    seq_out_frame_exclusive=c.seq_out_frame_exclusive - amount)
            if c.seq_in_frame >= seq_out
            else c
            for c in pieces
        ]
    return pieces


def delete_range(clips: list[EditClip], seq_in: int, seq_out: int) -> list[EditClip]:
    return remove_range(clips, seq_in, seq_out, ripple=True)


def lift_range(clips: list[EditClip], seq_in: int, seq_out: int) -> list[EditClip]:
    return remove_range(clips, seq_in, seq_out, ripple=False)
