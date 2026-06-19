"""Transition-smoothness review — pure core (Plan B).

Datatypes + boundary identity + frame-strip planning for the optional VLM review that judges
how fluid each cut is. The model itself is optional (Plan C / ``[vlm]`` extra); everything here
is deterministic and fully testable with the :class:`StubVlmBackend` — no model, no ffmpeg.

Frames are integer source-frame indices, ranges end-exclusive (invariants #1/#2). The cache
identity of a boundary is its **semantic** source-frame pair, never the sequence position (which
drifts when upstream clips are edited) — see :func:`boundary_signature` and spec §3.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

TimelineKind = Literal["rough_cut", "scene", "sequence"]
FixKind = Literal["none", "resnap", "transition"]
TransitionStyle = Literal["crossfade", "fade"]
SmoothnessLabel = Literal["smooth", "jump_cut", "hard_jolt", "motion_break"]


@dataclass(frozen=True)
class Boundary:
    """One cut between two adjacent lane-0 clips, in source-frame space (end-exclusive)."""

    timeline_id: str
    kind: TimelineKind
    asset_a: str
    asset_b: str
    src_in_a: int
    src_out_a: int
    src_in_b: int
    src_out_b: int
    seq_in_a: int
    seq_out_a: int            # == boundary_seq_frame (denormalised; NOT part of identity)
    removed_gap_frames: int   # max(0, src_in_b - src_out_a) when same asset, else 0
    same_source: bool         # asset_a == asset_b AND src_in_b == src_out_a (contiguous source)


@dataclass(frozen=True)
class SuggestedFix:
    kind: FixKind
    resnap_delta_frames: int = 0
    transition_style: TransitionStyle = "crossfade"
    transition_frames: int = 0


@dataclass(frozen=True)
class TransitionVerdict:
    smoothness: float
    label: SmoothnessLabel
    reason: str
    suggested_fix: SuggestedFix


def boundary_signature(boundary: Boundary, k: int, proxy_version: str) -> str:
    """Stable hash of a boundary's *semantic* identity + the inputs that change what the model sees.

    Excludes the sequence position (drifts on upstream edits) so a re-review after an unrelated
    edit is a cache hit; includes ``k`` and ``proxy_version`` because they change the extracted
    frames. Spec §3."""
    raw = "|".join(
        str(x)
        for x in (
            boundary.timeline_id,
            boundary.asset_a,
            boundary.asset_b,
            boundary.src_out_a,
            boundary.src_in_b,
            boundary.removed_gap_frames,
            int(boundary.same_source),
            k,
            proxy_version,
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def frame_strip_plan(boundary: Boundary, k: int) -> list[tuple[str, int]]:
    """Ordered ``(asset_id, src_frame)`` refs across the boundary: ≤k frames each side.

    A-side ends at ``src_out_a - 1`` inclusive (end-exclusive range ``[src_out_a-k, src_out_a)``);
    B-side starts at ``src_in_b``. Shorter clips yield fewer frames (no padding)."""
    a_start = max(boundary.src_in_a, boundary.src_out_a - k)
    a_refs = [(boundary.asset_a, f) for f in range(a_start, boundary.src_out_a)]
    b_end = min(boundary.src_in_b + k, boundary.src_out_b)
    b_refs = [(boundary.asset_b, f) for f in range(boundary.src_in_b, b_end)]
    return a_refs + b_refs
