"""Pure timeline operations on EditClip lists (frame-accurate, end-exclusive).

Every function returns a NEW clip list (no mutation), so a sequence of operations
yields deterministic, testable deltas. Source ranges are kept in sync with sequence
ranges when clips are trimmed (1:1 mapping). Retiming (``set_speed``) decouples the
two via the clip's speed ratio, with the sequence length projected by the time core.

L/J split state — ``audio_offset_samples`` (invariant #3, 2-lane m2)
------------------------------------------------------------------
Every clip carries a signed ``audio_offset_samples``: the LEADING-edge audio-vs-video shift in
SAMPLES of the cut that BEGINS the clip (``audio - video``; ``0`` = hard cut). It is a property of
the clip's HEAD (its in-cut), so the editing ops must preserve / recompute it — never silently reset
it to ``0`` — as the live source of truth (the OTIO blob is a derived cache, not the truth).

Per-op rule (the cut is the boundary between a clip and its predecessor):

* ``trim_clip`` — PRESERVES the offset. Trimming the source in/out changes the media shown, not the
  intentional editorial lead/trail at that cut.
* ``set_speed`` — PRESERVES the offset (samples). Retiming reprojects the picture; the head-cut
  audio relationship is a cut LOCATION, not retimed content, so the sample shift is unchanged.
* ``split_clip`` — the original clip KEEPS its leading offset; the new SECOND clip's head is a
  brand-new hard cut → ``0``.
* ``move_clip`` / reorder — the offset TRAVELS with the clip (it's a head property). After the
  re-pack the first-clip-0 invariant is enforced, so a clip moved to position 0 loses its leading
  cut (→ ``0``) and a clip that leaves position 0 keeps whatever offset it carried.
* ``delete_range`` / ``lift_range`` — an offset keyed to a removed clip's head disappears with it.
  When a clip is sliced by the removed range, only the FIRST surviving sub-piece inherits the
  leading offset; later sub-pieces start at internal cuts → ``0``. First-clip-0 is then enforced.
* ``insert_clip`` / ``append_clip`` — the inserted/appended clip carries its OWN offset (the
  ``EditClip`` default ``0`` = hard cut) unless the caller supplied one.

After every op the result is normalised so ``ordered(result)[0].audio_offset_samples == 0`` and
every offset stays sample-quantized (it is only ever copied or zeroed, never scaled to a fraction).

Lane-aware packing (P1, multi-track design spec §2)
---------------------------------------------------
Every geometry op is lane-scoped: it operates only on clips in the TARGET lane and leaves all
other lanes byte-identical.  Helper functions ``clips_on_lane`` / ``replace_lane`` implement
the partition/merge pattern.  ``place_clip`` (§1) sets one clip's absolute ``(seq_in_frame, lane)``
with NO ripple or re-pack of any other clip.

``_normalize_offsets`` treats the Lane-0 clip with the smallest ``seq_in_frame`` as the sequence
head (only lane-0 clips carry L/J offsets; clips on lanes ≥ 1 carry ``audio_offset_samples = 0``).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from ..timebase import FrameRange, Rounding, div_round, retimed_seq_length

# Maximum lane index for v1 (8 video lanes, indices 0..8). Keeps render filtergraph
# complexity bounded; the check is in ``place_clip`` validation (spec §1.4).
MAX_LANE: int = 8


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
    # Signed per-clip LEADING-edge audio-vs-video offset in SAMPLES (invariant #3): the L/J split
    # shift of the cut that BEGINS this clip, (audio - video). 0 = hard cut. Carried through every
    # op so the column stays the live source of truth across edits (see module docstring).
    audio_offset_samples: int = 0
    # Clip role: "base" (normal editorial clip) or "replace" (overlay/replace compositing clip).
    # Carried intact through every op so a placed replace-overlay never silently reverts to "base".
    role: str = "base"

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
            audio_offset_samples=row.get("audio_offset_samples") or 0,
            role=row.get("role") or "base",
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
            "audio_offset_samples": self.audio_offset_samples,
            "role": self.role,
        }


def sequence_length(clips: list[EditClip]) -> int:
    """Total sequence length = max seq_out over ALL lanes (spec §2.4)."""
    return max((c.seq_out_frame_exclusive for c in clips), default=0)


def ordered(clips: list[EditClip]) -> list[EditClip]:
    """Sort by (seq_in_frame, lane) — serialisation order, not packing (spec §2.4)."""
    return sorted(clips, key=lambda c: (c.seq_in_frame, c.lane))


# ---------------------------------------------------------------------------
# Lane helpers (spec §2.1)
# ---------------------------------------------------------------------------


def clips_on_lane(clips: list[EditClip], lane: int) -> list[EditClip]:
    """Return all clips on ``lane``, sorted by seq_in_frame."""
    return sorted((c for c in clips if c.lane == lane), key=lambda c: c.seq_in_frame)


def replace_lane(
    clips: list[EditClip], lane: int, new_lane_clips: list[EditClip]
) -> list[EditClip]:
    """Replace all clips on ``lane`` with ``new_lane_clips``; other lanes are untouched."""
    other = [c for c in clips if c.lane != lane]
    return other + new_lane_clips


def lane_length(clips: list[EditClip], lane: int) -> int:
    """Max seq_out_frame_exclusive on ``lane`` (0 if the lane is empty)."""
    return max((c.seq_out_frame_exclusive for c in clips if c.lane == lane), default=0)


def _normalize_offsets(clips: list[EditClip]) -> list[EditClip]:
    """Enforce the first-clip-0 L/J invariant on the Lane-0 head clip.

    The leading audio offset is the shift of the cut between a clip and its PREDECESSOR; the
    sequence-first clip has no predecessor, so it can never carry a leading cut and its
    ``audio_offset_samples`` must be ``0``. Every other clip keeps whatever sample offset it carried
    (offsets only ever travel or get zeroed by the ops — never scaled — so quantization is intact).

    Returned as a NEW list to keep the ops non-mutating; clip objects are only ``replace``-d when an
    actual change is needed, so the common case is a cheap copy.

    Lane semantics (spec §1.2): only Lane-0 clips carry L/J offsets.  The head is the Lane-0 clip
    with the smallest ``seq_in_frame``; clips on lanes ≥ 1 always carry ``audio_offset_samples = 0``
    (they have no inter-clip cut structure in the contiguous sense).
    """
    if not clips:
        return list(clips)
    # Only the lane-0 clip with the smallest seq_in is the sequence head.
    lane0 = [i for i, c in enumerate(clips) if c.lane == 0]
    if not lane0:
        return list(clips)
    head = min(lane0, key=lambda i: clips[i].seq_in_frame)
    out = list(clips)
    if out[head].audio_offset_samples != 0:
        out[head] = replace(out[head], audio_offset_samples=0)
    return out


def append_clip(clips: list[EditClip], clip: EditClip) -> list[EditClip]:
    """Append a clip at the end of the TARGET LANE (spec §2.2).

    ``start`` is the length of the clip's own lane (not the global sequence length), so clips on
    different lanes do not push each other.  Lane-0 behaviour is byte-identical to the previous
    implementation when only lane-0 clips exist
    (``lane_length`` == ``sequence_length`` in that case).
    """
    start = lane_length(clips, clip.lane)
    placed = replace(clip, seq_in_frame=start, seq_out_frame_exclusive=start + clip.src_length)
    # The appended clip keeps its own leading offset (default 0 = hard cut). When it lands first
    # (empty timeline) the normalise resets it; otherwise its head is a fresh cut after the tail.
    return _normalize_offsets([*clips, placed])


def insert_clip(clips: list[EditClip], clip: EditClip, at_seq_frame: int) -> list[EditClip]:
    """Insert a clip at ``at_seq_frame``, rippling later clips in THE SAME LANE to the right.

    Only clips on ``clip.lane`` are shifted; other lanes are left byte-identical (spec §2.2).
    Lane-0 behaviour is byte-identical to the previous implementation when only lane-0 clips exist.
    """
    dur = clip.src_length
    target_lane = clip.lane
    shifted = [
        replace(c, seq_in_frame=c.seq_in_frame + dur,
                seq_out_frame_exclusive=c.seq_out_frame_exclusive + dur)
        if c.lane == target_lane and c.seq_in_frame >= at_seq_frame
        else c
        for c in clips
    ]
    placed = replace(clip, seq_in_frame=at_seq_frame, seq_out_frame_exclusive=at_seq_frame + dur)
    # The inserted clip carries its own leading offset (default 0). Rippled clips keep theirs (their
    # head cuts are unchanged); normalise only re-asserts the first-clip-0 invariant (e.g. an insert
    # at frame 0 makes this clip the new sequence head -> its leading cut clears to a hard cut).
    return _normalize_offsets([*shifted, placed])


def remove_range(
    clips: list[EditClip], seq_in: int, seq_out: int, *, ripple: bool, lane: int = 0
) -> list[EditClip]:
    """Remove a sequence range on the TARGET LANE.

    ``ripple`` closes the gap; otherwise it is a lift.
    Only clips on ``lane`` are cut / rippled; all other lanes are byte-identical (spec §2.2).
    Defaults to ``lane=0`` so existing callers are unchanged and lane-0 behaviour is preserved.
    """
    if seq_out < seq_in:
        raise ValueError("seq_out must be >= seq_in")
    rng = FrameRange(seq_in, seq_out)
    pieces: list[EditClip] = []
    for clip in ordered(clips):
        if clip.lane != lane:
            # Not on the target lane — pass through untouched.
            pieces.append(clip)
            continue
        clip_range = FrameRange(clip.seq_in_frame, clip.seq_out_frame_exclusive)
        if not clip_range.overlaps(rng):
            pieces.append(clip)
            continue
        for sub in clip_range.subtract(rng):
            offset = sub.start - clip.seq_in_frame
            # Only the sub-piece that still starts at the clip's ORIGINAL head keeps the leading
            # audio offset; any later sub-piece begins at the removed range's far edge (a fresh
            # internal cut), so its head is a hard cut -> 0. ``offset == 0`` is exactly "this
            # sub-piece is the clip's surviving original head".
            head_survives = offset == 0
            pieces.append(
                replace(
                    clip,
                    seq_in_frame=sub.start,
                    seq_out_frame_exclusive=sub.end_exclusive,
                    src_in_frame=clip.src_in_frame + offset,
                    src_out_frame_exclusive=clip.src_in_frame + offset + sub.length,
                    audio_offset_samples=(
                        clip.audio_offset_samples if head_survives else 0
                    ),
                )
            )
    if ripple:
        amount = seq_out - seq_in
        pieces = [
            replace(c, seq_in_frame=c.seq_in_frame - amount,
                    seq_out_frame_exclusive=c.seq_out_frame_exclusive - amount)
            if c.lane == lane and c.seq_in_frame >= seq_out
            else c
            for c in pieces
        ]
    # An offset keyed to a removed clip's head vanished with that clip; the clip that newly follows
    # the gap keeps its own head offset (or 0 if it becomes the sequence-first clip).
    return _normalize_offsets(pieces)


def delete_range(
    clips: list[EditClip], seq_in: int, seq_out: int, *, lane: int = 0
) -> list[EditClip]:
    """Delete range on ``lane`` and ripple later clips on that lane left (spec §2.2)."""
    return remove_range(clips, seq_in, seq_out, ripple=True, lane=lane)


def lift_range(
    clips: list[EditClip], seq_in: int, seq_out: int, *, lane: int = 0
) -> list[EditClip]:
    """Lift (remove without ripple) range on ``lane``; leaves other lanes untouched (spec §2.2)."""
    return remove_range(clips, seq_in, seq_out, ripple=False, lane=lane)


def set_speed(
    clips: list[EditClip], at_seq_frame: int, speed_num: int, speed_den: int
) -> list[EditClip]:
    """Retime the clip starting at ``at_seq_frame`` to ``speed_num/speed_den`` and ripple
    later clips IN THE SAME LANE by the duration delta (spec §2.2). Speed > 1 shortens the clip on
    the timeline; the source range is unchanged (same media, played faster/slower). The new sequence
    length is projected deterministically by the time core."""
    if speed_num <= 0 or speed_den <= 0:
        raise ValueError("speed must be positive")
    target = next((c for c in ordered(clips) if c.seq_in_frame == at_seq_frame), None)
    if target is None:
        raise ValueError(f"no clip starts at seq frame {at_seq_frame}")

    target_lane = target.lane
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
        elif c.lane == target_lane and c.seq_in_frame >= old_end:
            # Only ripple clips on the SAME lane (spec §2.2).
            result.append(
                replace(
                    c, seq_in_frame=c.seq_in_frame + delta,
                    seq_out_frame_exclusive=c.seq_out_frame_exclusive + delta,
                )
            )
        else:
            result.append(c)
    # The head-cut audio offset (samples) is the cut LOCATION relationship, not retimed content, so
    # it is preserved unscaled across the retime (``replace`` above keeps it). Normalise only
    # re-asserts the first-clip-0 invariant.
    return _normalize_offsets(result)


def split_clip(clips: list[EditClip], at_seq_frame: int) -> list[EditClip]:
    """Split the clip that strictly contains ``at_seq_frame`` into two adjacent clips at
    that sequence frame. The source split point honours the clip's speed (1/1 = identity).
    Splitting on a clip edge raises so the UI can surface the no-op."""
    target = next(
        (
            c
            for c in ordered(clips)
            if c.seq_in_frame < at_seq_frame < c.seq_out_frame_exclusive
        ),
        None,
    )
    if target is None:
        raise ValueError(f"no clip strictly contains seq frame {at_seq_frame}")
    seq_offset = at_seq_frame - target.seq_in_frame
    # sequence offset -> source offset via the speed ratio (src = seq * speed_num/speed_den)
    src_mid = target.src_in_frame + div_round(
        seq_offset * target.speed_num, target.speed_den, Rounding.HALF_EVEN
    )
    # The left (original) clip keeps its leading offset; the right clip's head is a brand-new hard
    # cut introduced by the split, so its leading offset is 0.
    left = replace(
        target, src_out_frame_exclusive=src_mid, seq_out_frame_exclusive=at_seq_frame
    )
    right = replace(
        target, src_in_frame=src_mid, seq_in_frame=at_seq_frame, audio_offset_samples=0
    )
    return _normalize_offsets([*(left if c is target else c for c in clips), right])


def move_clip(
    clips: list[EditClip], at_seq_frame: int, to_seq_frame: int
) -> list[EditClip]:
    """Reorder the clip that starts at ``at_seq_frame`` to the position ``to_seq_frame``,
    then re-pack THE SAME LANE contiguously (back-to-back, preserving each clip's sequence
    length, source range, and speed). Other lanes are left byte-identical (spec §2.2).
    Raises ValueError if no clip starts at at_seq_frame.

    The target index is the number of OTHER clips on the same lane whose ``seq_in_frame <
    to_seq_frame``, so dragging a clip onto another clip's start drops it just before that clip;
    a to_seq_frame at/after the end of the lane appends it last.
    """
    # Find the clip to move (must start at at_seq_frame, any lane).
    all_ordered = ordered(clips)
    idx = next((i for i, c in enumerate(all_ordered) if c.seq_in_frame == at_seq_frame), None)
    if idx is None:
        raise ValueError(f"no clip starts at {at_seq_frame}")
    moving = all_ordered[idx]
    target_lane = moving.lane

    # Work only on the target lane; other lanes pass through unchanged.
    lane_clips = clips_on_lane(clips, target_lane)
    lane_idx = next(i for i, c in enumerate(lane_clips) if c.seq_in_frame == at_seq_frame)
    lane_clips.pop(lane_idx)
    target_pos = sum(1 for c in lane_clips if c.seq_in_frame < to_seq_frame)
    lane_clips.insert(target_pos, moving)

    # Re-pack only this lane: walk lane_clips, assign contiguous seq positions preserving each
    # clip's length. The leading audio offset TRAVELS with the clip (it's a head property;
    # ``replace`` keeps it), so a clip's L/J split rides along to its new position. Normalise
    # then enforces first-clip-0: a clip moved to position 0 loses its leading cut (-> 0);
    # a clip that leaves position 0 keeps its offset.
    repacked: list[EditClip] = []
    offset = 0
    for c in lane_clips:
        length = c.seq_out_frame_exclusive - c.seq_in_frame
        repacked.append(replace(c, seq_in_frame=offset, seq_out_frame_exclusive=offset + length))
        offset += length
    return _normalize_offsets(replace_lane(clips, target_lane, repacked))


def trim_clip(
    clips: list[EditClip], at_seq_frame: int, new_src_in: int, new_src_out: int
) -> list[EditClip]:
    """Set the source range of the clip starting at ``at_seq_frame`` and ripple later clips
    IN THE SAME LANE by the resulting duration delta (speed preserved) (spec §2.2)."""
    if new_src_out <= new_src_in:
        raise ValueError("new source range must be non-empty")
    target = next((c for c in ordered(clips) if c.seq_in_frame == at_seq_frame), None)
    if target is None:
        raise ValueError(f"no clip starts at seq frame {at_seq_frame}")
    target_lane = target.lane
    new_len = retimed_seq_length(new_src_out - new_src_in, target.speed_num, target.speed_den)
    old_end = target.seq_out_frame_exclusive
    delta = (target.seq_in_frame + new_len) - old_end
    result: list[EditClip] = []
    for c in clips:
        if c is target:
            result.append(
                replace(
                    c, src_in_frame=new_src_in, src_out_frame_exclusive=new_src_out,
                    seq_out_frame_exclusive=c.seq_in_frame + new_len,
                )
            )
        elif c.lane == target_lane and c.seq_in_frame >= old_end:
            # Ripple only within the same lane (spec §2.2).
            result.append(
                replace(
                    c, seq_in_frame=c.seq_in_frame + delta,
                    seq_out_frame_exclusive=c.seq_out_frame_exclusive + delta,
                )
            )
        else:
            result.append(c)
    # Trimming the source in/out changes the media shown, not the intentional editorial lead/trail
    # at this cut, so the leading audio offset is PRESERVED (``replace`` above keeps it). Normalise
    # only re-asserts the first-clip-0 invariant.
    return _normalize_offsets(result)


def roll_boundary(
    clips: list[EditClip], boundary_seq_frame: int, delta_frames: int
) -> list[EditClip]:
    """Roll the cut between the two adjacent lane-0 clips meeting at ``boundary_seq_frame``.

    Clip A (which ENDS at the boundary) gains ``delta_frames`` of source; clip B (which STARTS
    there) loses them. The total sequence length and every other clip are unchanged — only the cut
    point moves. ``delta_frames`` is in source frames and may be negative. Speed-1 clips only (a
    resnap target); a retimed clip raises. Raises ``ValueError`` on a missing boundary or a delta
    that would empty either clip (valid range ``[-(len_A - 1), len_B - 1]``)."""
    a = next(
        (c for c in clips if c.lane == 0 and c.seq_out_frame_exclusive == boundary_seq_frame), None
    )
    b = next(
        (c for c in clips if c.lane == 0 and c.seq_in_frame == boundary_seq_frame), None
    )
    if a is None or b is None:
        raise ValueError(f"no lane-0 boundary at seq frame {boundary_seq_frame}")
    if (a.speed_num, a.speed_den) != (1, 1) or (b.speed_num, b.speed_den) != (1, 1):
        raise ValueError("roll_boundary supports speed-1 clips only")
    lo, hi = -(a.src_length - 1), (b.src_length - 1)
    if not (lo <= delta_frames <= hi):
        raise ValueError(f"roll delta {delta_frames} out of range [{lo}, {hi}]")
    a2 = replace(
        a,
        src_out_frame_exclusive=a.src_out_frame_exclusive + delta_frames,
        seq_out_frame_exclusive=a.seq_out_frame_exclusive + delta_frames,
    )
    b2 = replace(
        b,
        src_in_frame=b.src_in_frame + delta_frames,
        seq_in_frame=b.seq_in_frame + delta_frames,
    )
    # Only the source/seq ranges of A and B change; offsets and every other clip are preserved.
    return _normalize_offsets([a2 if c is a else b2 if c is b else c for c in clips])


def set_audio_offset(
    clips: list[EditClip],
    at_seq_frame: int,
    audio_offset_samples: int,
    *,
    samples_per_frame: int,
) -> list[EditClip]:
    """Set the LEADING-edge L/J audio offset (samples) of the clip starting at ``at_seq_frame``.

    This is the editing op behind the manual 2-lane drag (m3): it writes the clip's
    ``audio_offset_samples`` HEAD offset directly from a UI gesture, going through the SAME
    ``apply_operation`` snapshot path as trim / move / split, so undo/redo round-trips it. It
    mirrors what the accept endpoint („Übernehmen") persists, but as a composable op rather than a
    wholesale re-post — a drag and an accepted recommendation both land on one
    ``audio_offset_samples`` column, so they reconcile (last write wins).

    Geometry is untouched: only the head offset changes (the picture stays frame-exact, invariant
    #1; the audio relationship is canonical in samples, invariant #3). Invariants enforced here:

    * the clip MUST start at ``at_seq_frame`` (else :class:`ValueError`, surfaced as 422);
    * a sub-perception offset ``|offset| < 1 frame`` (``< samples_per_frame``) is clamped to ``0``
      (a hard cut) — mirrors the accept endpoint's ``|offset| <= 1`` hard rule at sample resolution;
    * the sequence-first clip has no predecessor, so its leading cut is always ``0``: setting an
      offset on it is a no-op (``_normalize_offsets`` re-asserts it).
    """
    target = next((c for c in ordered(clips) if c.seq_in_frame == at_seq_frame), None)
    if target is None:
        raise ValueError(f"no clip starts at seq frame {at_seq_frame}")
    if samples_per_frame <= 0:
        raise ValueError("samples_per_frame must be positive")
    # Hard-clamp a sub-perception drag (within one frame either side) back to a hard cut.
    offset = 0 if abs(audio_offset_samples) < samples_per_frame else int(audio_offset_samples)
    result = [
        replace(c, audio_offset_samples=offset) if c is target else c for c in clips
    ]
    # If the target is the sequence head, normalise zeroes it again (first-clip-0 is mandatory).
    return _normalize_offsets(result)


# ---------------------------------------------------------------------------
# place_clip — absolute free placement (spec §1)
# ---------------------------------------------------------------------------


def place_clip(
    clips: list[EditClip],
    *,
    at_seq_frame: int,
    lane_src: int,
    to_seq_frame: int,
    lane_dst: int,
) -> list[EditClip]:
    """Set one clip's absolute ``(seq_in_frame, lane)`` with NO ripple or re-pack (spec §1).

    Identifies the clip by its CURRENT ``(at_seq_frame, lane_src)`` — which is unique under the
    "no intra-lane overlap" policy (spec §1.3 v1-Entscheidung).  Places it at ``(to_seq_frame,
    lane_dst)`` while preserving its sequence duration, source range, and speed.  All other clips
    are returned byte-identical — this is the entire point of the op and the key difference from
    ``move_clip`` (which re-packs the lane contiguously).

    Audio offset rule (spec §1.2):
    * Moving TO lane ≥ 1 → ``audio_offset_samples`` set to ``0`` (no L/J structure on free lanes).
    * Moving TO lane 0  → offset preserved; ``_normalize_offsets`` may zero it if it becomes the
      new lane-0 head.

    Validation (spec §1.4):
    1. Exactly one clip at ``(at_seq_frame, lane_src)`` — else ``ValueError``.
    2. ``to_seq_frame >= 0`` — else ``ValueError``.
    3. ``0 <= lane_dst <= MAX_LANE`` — else ``ValueError``.
    4. The placed clip ``[to_seq_frame, to_seq_frame + dur)`` must NOT overlap any EXISTING clip on
       ``lane_dst`` (excluding the clip being moved itself) — else ``ValueError``.
    """
    # 1. Find the source clip.
    candidates = [c for c in clips if c.seq_in_frame == at_seq_frame and c.lane == lane_src]
    if len(candidates) == 0:
        raise ValueError(
            f"no clip at (seq_in_frame={at_seq_frame}, lane={lane_src})"
        )
    if len(candidates) > 1:
        raise ValueError(
            f"ambiguous: multiple clips at (seq_in_frame={at_seq_frame}, lane={lane_src})"
        )
    target = candidates[0]

    # 2. Non-negative destination.
    if to_seq_frame < 0:
        raise ValueError(f"to_seq_frame must be >= 0, got {to_seq_frame}")

    # 3. Lane bounds.
    if not (0 <= lane_dst <= MAX_LANE):
        raise ValueError(f"lane_dst {lane_dst} out of range [0, {MAX_LANE}]")

    # 4. Intra-lane overlap check on the DESTINATION lane.
    dur = target.seq_out_frame_exclusive - target.seq_in_frame
    new_range = FrameRange(to_seq_frame, to_seq_frame + dur)
    for c in clips:
        if c is target:
            # Ignore the clip being moved (it vacates its current slot).
            continue
        if c.lane != lane_dst:
            continue
        existing = FrameRange(c.seq_in_frame, c.seq_out_frame_exclusive)
        if existing.overlaps(new_range):
            raise ValueError(
                f"overlap on lane {lane_dst} at [{to_seq_frame}, {to_seq_frame + dur}): "
                f"conflicts with clip [{c.seq_in_frame}, {c.seq_out_frame_exclusive})"
            )

    # Preserve audio_offset_samples only when staying on lane 0 or moving back to lane 0;
    # clips on lanes ≥ 1 always carry 0 (no L/J cut structure on free lanes, spec §1.2).
    audio_offset = target.audio_offset_samples if lane_dst == 0 else 0

    placed = replace(
        target,
        seq_in_frame=to_seq_frame,
        seq_out_frame_exclusive=to_seq_frame + dur,
        lane=lane_dst,
        audio_offset_samples=audio_offset,
    )
    result = [placed if c is target else c for c in clips]
    return _normalize_offsets(result)
