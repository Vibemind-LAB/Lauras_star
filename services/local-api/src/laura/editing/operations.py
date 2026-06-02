"""Pure timeline operations on EditClip lists (frame-accurate, end-exclusive).

Every function returns a NEW clip list (no mutation), so a sequence of operations
yields deterministic, testable deltas. Source ranges are kept in sync with sequence
ranges when clips are trimmed (1:1 mapping). Retiming (``set_speed``) decouples the
two via the clip's speed ratio, with the sequence length projected by the time core.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from ..timebase import FrameRange, retimed_seq_length


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
    speed_num: int = 1
    speed_den: int = 1

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
            speed_num=row.get("speed_num") or 1,
            speed_den=row.get("speed_den") or 1,
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
            "speed_num": self.speed_num,
            "speed_den": self.speed_den,
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


def set_speed(
    clips: list[EditClip], at_seq_frame: int, speed_num: int, speed_den: int
) -> list[EditClip]:
    """Retime the clip starting at ``at_seq_frame`` to ``speed_num/speed_den`` and ripple
    later clips by the duration delta. Speed > 1 shortens the clip on the timeline; the
    source range is unchanged (same media, played faster/slower). The new sequence length
    is projected deterministically by the time core."""
    if speed_num <= 0 or speed_den <= 0:
        raise ValueError("speed must be positive")
    target = next((c for c in ordered(clips) if c.seq_in_frame == at_seq_frame), None)
    if target is None:
        raise ValueError(f"no clip starts at seq frame {at_seq_frame}")

    new_len = retimed_seq_length(target.src_length, speed_num, speed_den)
    old_end = target.seq_out_frame_exclusive
    delta = (target.seq_in_frame + new_len) - old_end

    result: list[EditClip] = []
    for c in clips:
        if c is target:
            result.append(
                replace(
                    c, speed_num=speed_num, speed_den=speed_den,
                    seq_out_frame_exclusive=c.seq_in_frame + new_len,
                )
            )
        elif c.seq_in_frame >= old_end:
            result.append(
                replace(
                    c, seq_in_frame=c.seq_in_frame + delta,
                    seq_out_frame_exclusive=c.seq_out_frame_exclusive + delta,
                )
            )
        else:
            result.append(c)
    return result
