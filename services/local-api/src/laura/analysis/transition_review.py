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
from typing import Literal, Protocol

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


class VlmBackend(Protocol):
    """A model (or stub) that judges a single transition from frames + metadata.

    ``review`` receives the boundary frame strip (JPEG bytes, A-side then B-side) and a ``meta``
    dict (``same_source``, ``removed_gap_frames``, ``k``, ``a_count``, ``b_count``) and returns a
    structured verdict. ``model_digest`` pins the verdict's identity for the cache (spec §3)."""

    def available(self) -> bool: ...
    def model_id(self) -> str: ...
    def model_digest(self) -> str: ...
    def review(self, frames: list[bytes], meta: dict[str, object]) -> TransitionVerdict: ...


class StubVlmBackend:
    """Deterministic, model-free backend — the default in tests (no model, no ffmpeg).

    Heuristic: a **contiguous same-source** cut (``same_source`` and ``removed_gap_frames == 0``)
    is the canonical dead-air jump → propose a crossfade. Everything else reads as a clean cut
    between distinct material → no fix. (Note: ``same_source`` already implies a zero gap; the gap
    guard is defensive.)"""

    def available(self) -> bool:
        return True

    def model_id(self) -> str:
        return "stub"

    def model_digest(self) -> str:
        return "stub-v1"

    def review(self, frames: list[bytes], meta: dict[str, object]) -> TransitionVerdict:
        same_source = bool(meta.get("same_source"))
        gap_raw = meta.get("removed_gap_frames", 0)
        gap = gap_raw if isinstance(gap_raw, int) else 0
        if same_source and gap == 0:
            k_raw = meta.get("k", 6)
            k = k_raw if isinstance(k_raw, int) else 6
            return TransitionVerdict(
                smoothness=0.2,
                label="jump_cut",
                reason="contiguous same-source cut (dead-air jump)",
                suggested_fix=SuggestedFix(
                    kind="transition", transition_style="crossfade", transition_frames=k
                ),
            )
        return TransitionVerdict(
            smoothness=0.9,
            label="smooth",
            reason="distinct material",
            suggested_fix=SuggestedFix(kind="none"),
        )


def default_backend() -> VlmBackend | None:
    """The configured real backend, or ``None`` when the ``[vlm]`` extra is absent.

    Plan B always returns ``None`` (no model); Plan C provides the Ollama-backed implementation.
    The review job takes a backend argument, so tests inject :class:`StubVlmBackend` directly."""
    return None


def vlm_available() -> bool:
    return default_backend() is not None
