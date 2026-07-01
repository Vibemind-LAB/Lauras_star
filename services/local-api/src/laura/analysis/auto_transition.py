"""Auto-apply transitions (#1) — run the transition-review heuristic over every lane-0
boundary and write each suggested crossfade, preserving any manual (non-hard) transition.

Pure reuse of :mod:`laura.analysis.transition_review`: the same ``StubVlmBackend`` heuristic
that powers "Übergänge prüfen" decides here too (metadata-only, no ffmpeg), so there is ONE
rule set, not two. A real VLM backend can be injected later — that path needs frame extraction
(``run_transition_review``); the default heuristic does not.
"""

from __future__ import annotations

from typing import Any

from ..db import repos
from ..db.database import Database
from .transition_review import (
    Boundary,
    StubVlmBackend,
    VlmBackend,
    apply_fix,
    enumerate_boundaries,
)

# Default auto-crossfade duration (timeline frames) written at a detected jump-cut boundary.
DEFAULT_CROSSFADE_FRAMES = 8


def _a_clip_transition_kind(clips: list[dict[str, Any]], boundary: Boundary) -> str | None:
    """Current ``transition_after_kind`` of the A-clip at ``boundary`` (lane-0, ends at the cut)."""
    for c in clips:
        if int(c.get("lane") or 0) == 0 and int(c["seq_out_frame_exclusive"]) == boundary.seq_out_a:
            kind = c.get("transition_after_kind")
            return str(kind) if kind is not None else None
    return None


def auto_apply_transitions(
    db: Database,
    timeline_id: str,
    *,
    backend: VlmBackend | None = None,
    crossfade_frames: int = DEFAULT_CROSSFADE_FRAMES,
) -> dict[str, int]:
    """Decide + apply a transition for every lane-0 boundary of ``timeline_id``.

    Uses the transition-review heuristic (``StubVlmBackend`` by default — metadata-only, no
    ffmpeg). A suggested ``transition`` fix is applied ONLY where the A-clip currently has a hard
    cut, so manual transitions are never overwritten. Idempotent: a re-run leaves already-set
    transitions untouched. Returns ``{"boundaries", "applied", "skipped_manual"}``.
    """
    active: VlmBackend = backend or StubVlmBackend()
    boundaries = enumerate_boundaries(db, timeline_id)
    # Transition fixes never move clips, so the lane-0 geometry is stable across the loop.
    clips = repos.list_timeline_clips(db, timeline_id)
    applied = 0
    skipped_manual = 0
    for b in boundaries:
        verdict = active.review(
            [],
            {
                "same_source": b.same_source,
                "removed_gap_frames": b.removed_gap_frames,
                "k": crossfade_frames,  # the Stub uses this as the crossfade duration
                "a_count": 0,
                "b_count": 0,
            },
        )
        fix = verdict.suggested_fix
        if fix.kind != "transition":
            continue
        current = _a_clip_transition_kind(clips, b)
        if current not in (None, "hard"):
            skipped_manual += 1
            continue
        result = apply_fix(
            db,
            timeline_id=timeline_id,
            identity={
                "asset_a": b.asset_a,
                "asset_b": b.asset_b,
                "src_out_a": b.src_out_a,
                "src_in_b": b.src_in_b,
            },
            fix=fix,
        )
        if result.get("status") == "ok" and result.get("applied") == "transition":
            applied += 1
    return {"boundaries": len(boundaries), "applied": applied, "skipped_manual": skipped_manual}
