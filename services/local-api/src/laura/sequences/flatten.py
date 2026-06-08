"""Resolve a sequence (ordered scene references) into a flat clip list at runtime, so scene
edits propagate. Each scene contributes its materialized timeline's clips, offset by the
running sequence position."""
from __future__ import annotations

from typing import Any

from ..db import repos
from ..db.database import Database


def flatten_sequence(db: Database, sequence_timeline_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    for item in repos.list_sequence_items(db, sequence_timeline_id):
        scene = repos.get_scene(db, item["scene_id"])
        if scene is None or not scene.get("scene_timeline_id"):
            continue  # not materialized -> skip (the assemble PUT materializes scenes)
        clips = repos.list_timeline_clips(db, scene["scene_timeline_id"])
        scene_len = max((c["seq_out_frame_exclusive"] for c in clips), default=0)
        for c in clips:
            rows.append({
                **c,
                "seq_in_frame": offset + c["seq_in_frame"],
                "seq_out_frame_exclusive": offset + c["seq_out_frame_exclusive"],
            })
        offset += scene_len
    return rows
