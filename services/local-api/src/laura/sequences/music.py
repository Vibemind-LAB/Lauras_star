"""Resolve per-scene music tracks for a sequence timeline.

Each scene in the sequence that has a ``music_asset_id`` contributes one
music track, positioned at the scene's place (``[offset, offset+scene_len)``)
in the flattened sequence.  The caller can pass these tracks directly to
``render_clips_mp4(music_tracks=...)``.
"""
from __future__ import annotations

from pathlib import Path

from ..db import repos
from ..db.database import Database


def sequence_music_tracks(
    db: Database,
    sequence_timeline_id: str,
) -> list[tuple[Path, int, int, int]]:
    """Return music track descriptors for every scene in *sequence_timeline_id*
    that has music assigned.

    Returns a list of ``(path, seq_in_frame, seq_out_frame_exclusive,
    gain_percent)`` tuples — one per scene-with-music, in sequence order.
    The ``[seq_in, seq_out_exclusive)`` interval describes where the scene sits
    inside the fully-flattened sequence (same offset logic as
    :func:`~laura.sequences.flatten.flatten_sequence`).

    Scenes without a ``music_asset_id``, or whose scene timeline is not yet
    materialised, are silently skipped.
    """
    tracks: list[tuple[Path, int, int, int]] = []
    offset = 0
    for item in repos.list_sequence_items(db, sequence_timeline_id):
        scene = repos.get_scene(db, item["scene_id"])
        if scene is None or not scene.get("scene_timeline_id"):
            # Not materialised — still advance the offset if we can.
            # We cannot know the scene length without a materialised timeline,
            # so we skip the offset advance too (mirrors flatten_sequence).
            continue
        clips = repos.list_timeline_clips(db, scene["scene_timeline_id"])
        scene_len = max((c["seq_out_frame_exclusive"] for c in clips), default=0)

        if scene.get("music_asset_id"):
            asset = repos.get_asset(db, scene["music_asset_id"])
            if asset is not None:
                gain = int(scene.get("music_gain_percent") or 100)
                tracks.append((
                    Path(asset["source_path"]),
                    offset,
                    offset + scene_len,
                    gain,
                ))

        offset += scene_len
    return tracks
