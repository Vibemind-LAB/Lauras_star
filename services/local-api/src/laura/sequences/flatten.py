"""Resolve a sequence (ordered scene references) into a flat clip list at runtime, so scene
edits propagate.

Flatten model — Modus B1 (spec §3.3):
- **Lane 0 (scene clips):** Each scene's lane-0 clips are re-offset by the running cumulative
  scene length so that scenes are contiguous on the primary track.  This is the unchanged
  pre-B1 behaviour for lane 0.
- **Lane ≥ 1 (sequence-global overlays):** These clips live *directly* on the sequence
  timeline (not inside any scene) with absolute ``seq_in_frame`` values.  They are collected
  from ``list_timeline_clips(sequence_timeline_id)`` and appended without any re-offset.
  Lane-≥1 clips from inside scene timelines are intentionally ignored by the flatten step
  because they have no well-defined absolute position once scenes are stitched together.
"""
from __future__ import annotations

from typing import Any

from ..db import repos
from ..db.database import Database


def flatten_sequence(db: Database, sequence_timeline_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0

    # --- Lane 0: scene clips, re-offset to be contiguous (unchanged behaviour) ---
    for item in repos.list_sequence_items(db, sequence_timeline_id):
        scene = repos.get_scene(db, item["scene_id"])
        if scene is None or not scene.get("scene_timeline_id"):
            continue  # not materialized -> skip (the assemble PUT materializes scenes)
        clips = repos.list_timeline_clips(db, scene["scene_timeline_id"])
        # Only lane-0 clips contribute to the contiguous primary track.
        lane0_clips = [c for c in clips if c.get("lane", 0) == 0]
        scene_len = max((c["seq_out_frame_exclusive"] for c in lane0_clips), default=0)
        for c in lane0_clips:
            rows.append({
                **c,
                "seq_in_frame": offset + c["seq_in_frame"],
                "seq_out_frame_exclusive": offset + c["seq_out_frame_exclusive"],
            })
        offset += scene_len

    # --- Lane ≥ 1: sequence-global overlays, absolute positions, no re-offset ---
    for c in repos.list_timeline_clips(db, sequence_timeline_id):
        if c.get("lane", 0) >= 1:
            rows.append(c)

    return rows
