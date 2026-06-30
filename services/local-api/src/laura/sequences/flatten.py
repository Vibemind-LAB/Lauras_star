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

Scene-window mapping (lane-0 edit routing)
------------------------------------------
Because the flattened lane-0 clips live in each scene's *own* ``scene_timeline_id`` (re-offset by
the running cumulative scene length) and NOT on the sequence timeline, a lane-0 timeline edit posted
against the sequence timeline has to be routed back to the underlying scene timeline.
:func:`sequence_scene_windows` exposes the exact same offset walk as a list of contiguous
``SceneWindow`` spans so the edit router can find which scene a flattened ``seq_in_frame`` falls
into and translate the op to scene-local frames.  Keeping it in this module guarantees the router
uses one source of truth with :func:`flatten_sequence` (same offsets, same lane-0 length).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..db import repos
from ..db.database import Database


@dataclass(frozen=True)
class SceneWindow:
    """One scene's contiguous span on the flattened sequence (end-exclusive frames).

    ``[seq_in_frame, seq_out_frame_exclusive)`` is the scene's window in flattened-sequence space;
    ``offset`` is its start (== ``seq_in_frame``, kept named for the translate math); ``length`` is
    the scene's lane-0 length (``offset + length == seq_out_frame_exclusive``).
    ``scene_timeline_id`` is the timeline the lane-0 clips actually live on.
    """

    scene_id: str
    scene_timeline_id: str
    offset: int
    length: int

    @property
    def seq_in_frame(self) -> int:
        return self.offset

    @property
    def seq_out_frame_exclusive(self) -> int:
        return self.offset + self.length

    def contains(self, seq_frame: int) -> bool:
        return self.offset <= seq_frame < self.offset + self.length


def sequence_scene_windows(db: Database, sequence_timeline_id: str) -> list[SceneWindow]:
    """The ordered lane-0 scene windows of a sequence, using the SAME offset walk as
    :func:`flatten_sequence` (single source of truth for where each scene sits).

    A scene that is not materialized (no ``scene_timeline_id``) is skipped exactly as the flatten
    step skips it, so the running offset stays identical between the two.
    """
    windows: list[SceneWindow] = []
    offset = 0
    for item in repos.list_sequence_items(db, sequence_timeline_id):
        scene = repos.get_scene(db, item["scene_id"])
        if scene is None or not scene.get("scene_timeline_id"):
            continue  # not materialized -> skip (matches flatten_sequence)
        scene_timeline_id = scene["scene_timeline_id"]
        clips = repos.list_timeline_clips(db, scene_timeline_id)
        lane0_clips = [c for c in clips if c.get("lane", 0) == 0]
        scene_len = max((c["seq_out_frame_exclusive"] for c in lane0_clips), default=0)
        windows.append(
            SceneWindow(
                scene_id=scene["id"],
                scene_timeline_id=scene_timeline_id,
                offset=offset,
                length=scene_len,
            )
        )
        offset += scene_len
    return windows


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
