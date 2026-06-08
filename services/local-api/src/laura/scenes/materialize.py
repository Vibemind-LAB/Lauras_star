"""Lazily materialize a scene (marker over a rough_cut) into its own kind="scene" timeline:
the scene's clip slice, re-based to sequence 0. Idempotent — never re-materializes."""

from __future__ import annotations

from typing import Any

from ..db import repos
from ..db.database import Database
from ..editing.otio_sync import rebuild_otio


def materialize_scene(db: Database, scene: dict[str, Any]) -> dict[str, Any]:
    """Return the kind="scene" timeline for *scene*, creating it if it doesn't exist yet.

    Idempotent: if ``scene["scene_timeline_id"]`` already points to an existing timeline,
    that timeline is returned unchanged.
    """
    existing = scene.get("scene_timeline_id")
    if existing:
        row = repos.get_timeline(db, existing)
        if row is not None:
            return row
    tl = repos.create_timeline(
        db,
        project_id=scene["project_id"],
        name=scene["name"],
        kind="scene",
        created_from=scene["source_timeline_id"],
    )
    base: int = scene["seq_in_frame"]
    rows: list[dict[str, Any]] = []
    for c in repos.list_timeline_clips(db, scene["source_timeline_id"]):
        if scene["seq_in_frame"] <= c["seq_in_frame"] < scene["seq_out_frame_exclusive"]:
            rows.append(
                {
                    **c,
                    "seq_in_frame": c["seq_in_frame"] - base,
                    "seq_out_frame_exclusive": c["seq_out_frame_exclusive"] - base,
                }
            )
    repos.replace_timeline_clips(db, tl["id"], rows)
    rebuild_otio(db, tl["id"])
    repos.set_scene_timeline(db, scene["id"], tl["id"])
    out = repos.get_timeline(db, tl["id"])
    assert out is not None
    return out
