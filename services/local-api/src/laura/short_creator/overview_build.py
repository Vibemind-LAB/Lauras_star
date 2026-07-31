"""Write the overview montage into the existing timeline/scene/sequence tables (spec
2026-07-31-auto-overview-design.md §5).

Three levels, no migration and no new vessel:

1. one ``kind="overview"`` source timeline holding one clip per window,
2. one scene per clip over that timeline, materialized,
3. one NEW ``kind="sequence"`` timeline referencing those scenes in order.

Two properties this arrangement buys, both load-bearing:

* ``kind="overview"`` keeps the timeline out of ``repos.get_asset_rough_cut``
  (``kind='rough_cut' AND created_from=<asset_id>``), so an overview can never be mistaken for
  a video's rough cut.
* scenes are listed by ``source_timeline_id`` (:func:`repos.list_scenes`), so the videos' own
  scene lists — and the scene NUMBERS auto-short resolves through ``order_index + 1`` — stay
  exactly as they were.
"""

from __future__ import annotations

import logging
from typing import TypedDict

from ..db import repos
from ..db.database import Database
from ..editing.otio_sync import rebuild_otio
from ..scenes.materialize import materialize_scene
from .overview_windows import Candidate

logger = logging.getLogger(__name__)

# Timeline names are shown in the UI; keep the topic readable but bounded.
_NAME_TOPIC_CHARS = 60


class OverviewBuild(TypedDict):
    sequence_id: str
    source_timeline_id: str
    scene_ids: list[str]


def _name(topic: str) -> str:
    trimmed = topic.strip()[:_NAME_TOPIC_CHARS]
    return f"Überblick: {trimmed}"


def build_overview(
    db: Database, *, project_id: str, topic: str, clips: list[Candidate]
) -> OverviewBuild:
    """Write *clips* as a self-contained, editable montage and return its ids.

    *clips* empty is a programming error (the endpoint 422s before calling) -> ``ValueError``.
    """
    if not clips:
        raise ValueError("clips is empty — nothing to build an overview from")

    name = _name(topic)
    source = repos.create_timeline(db, project_id=project_id, name=name, kind="overview")
    source_id = str(source["id"])

    rows = []
    ranges: list[tuple[int, int]] = []
    offset = 0
    for clip in clips:
        length = clip.length_frames
        rows.append(
            {
                "asset_id": clip.asset_id,
                "src_in_frame": clip.start_frame,
                "src_out_frame_exclusive": clip.end_frame_exclusive,
                "seq_in_frame": offset,
                "seq_out_frame_exclusive": offset + length,
            }
        )
        ranges.append((offset, offset + length))
        offset += length
    repos.replace_timeline_clips(db, source_id, rows)
    rebuild_otio(db, source_id)

    # One scene per clip. replace_scenes assigns order_index positionally, so list_scenes
    # returns them in exactly the clip order built above.
    repos.replace_scenes(db, project_id, source_id, ranges)
    scene_ids: list[str] = []
    for scene in repos.list_scenes(db, source_id):
        materialize_scene(db, scene)
        scene_ids.append(str(scene["id"]))

    sequence = repos.create_timeline(
        db, project_id=project_id, name=name, kind="sequence", created_from=source_id
    )
    sequence_id = str(sequence["id"])
    repos.replace_sequence_items(db, sequence_id, scene_ids)
    rebuild_otio(db, sequence_id)

    logger.info(
        "auto-overview built: sequence=%s source=%s clips=%d", sequence_id, source_id, len(clips)
    )
    return {
        "sequence_id": sequence_id,
        "source_timeline_id": source_id,
        "scene_ids": scene_ids,
    }
