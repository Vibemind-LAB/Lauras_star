"""Build the canonical interchange model from DB rows and regenerate a timeline's OTIO.
Extracted so non-timeline routers (scenes) can keep OTIO as the source of truth without
importing private helpers from api/timelines.py."""
from __future__ import annotations

from typing import Any

from ..db import repos
from ..db.database import Database
from ..interchange.otio_io import timeline_to_otio_string
from ..interchange.timeline import Timeline, timeline_from_rows
from ..sequences.flatten import flatten_sequence


def resolve_clip_rows(db: Database, timeline_row: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the effective clip rows for a timeline.

    For ``kind="sequence"`` timelines the content is flattened from ordered scene
    references via ``flatten_sequence``; all other kinds use ``list_timeline_clips``
    directly (non-sequence path is unchanged — regression-safe).
    """
    if timeline_row.get("kind") == "sequence":
        return flatten_sequence(db, timeline_row["id"])
    return repos.list_timeline_clips(db, timeline_row["id"])


def build_model(db: Database, timeline_row: dict[str, Any]) -> Timeline:
    project = repos.get_project(db, timeline_row["project_id"])
    assert project is not None
    clip_rows = resolve_clip_rows(db, timeline_row)
    assets = {
        aid: a
        for aid in {c["asset_id"] for c in clip_rows}
        if (a := repos.get_asset(db, aid)) is not None
    }
    speakers = {
        sid: s
        for sid in {c["speaker_id"] for c in clip_rows if c.get("speaker_id")}
        if (s := repos.get_speaker(db, sid)) is not None
    }
    return timeline_from_rows(timeline_row, clip_rows, project, assets, speakers)


def rebuild_otio(db: Database, timeline_id: str) -> None:
    row = repos.get_timeline(db, timeline_id)
    if row is None:
        return
    repos.update_timeline_otio(db, timeline_id, timeline_to_otio_string(build_model(db, row)))
