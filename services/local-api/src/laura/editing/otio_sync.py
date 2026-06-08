"""Build the canonical interchange model from DB rows and regenerate a timeline's OTIO.
Extracted so non-timeline routers (scenes) can keep OTIO as the source of truth without
importing private helpers from api/timelines.py."""
from __future__ import annotations

from typing import Any

from ..api.otio_split import (
    AcceptedSplit,
    accepted_offsets_from_otio,
    apply_split_cuts,
)
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


def _audio_sample_rate(db: Database, timeline_row: dict[str, Any]) -> int | None:
    """The audio sample rate to project split-edit boundaries onto samples (invariant #3).

    Picks the first clip asset that declares an ``audio_sample_rate`` (rough cuts are single-asset
    today). ``None`` when no asset carries one — the split is then frame-only and the sample
    projection is simply omitted, never guessed.
    """
    for clip_row in resolve_clip_rows(db, timeline_row):
        asset = repos.get_asset(db, clip_row["asset_id"])
        if asset and asset.get("audio_sample_rate"):
            return int(asset["audio_sample_rate"])
    return None


def serialize_timeline_otio(
    db: Database,
    timeline_row: dict[str, Any],
    *,
    accepted: list[AcceptedSplit] | None = None,
) -> str:
    """Serialise a timeline to OTIO, carrying any accepted L/J split edits (migration-free).

    The stored ``otio_json`` is a cache regenerated from the clips table on every edit, so accepted
    split offsets must be re-applied at build time. They are recovered from the PREVIOUS blob's
    ``metadata["laura"]["accepted_split_offsets"]`` (so a regenerate never clobbers a split back to
    a hard cut) unless an explicit ``accepted`` list is supplied (e.g. a fresh accept call).

    With no accepted splits this delegates to the byte-for-byte single-track writer Laura uses
    today — the split representation is purely additive.
    """
    model = build_model(db, timeline_row)
    if accepted is None:
        accepted = accepted_offsets_from_otio(timeline_row.get("otio_json") or "")
    meaningful = [s for s in accepted if not s.is_hard()]
    if not meaningful:
        return timeline_to_otio_string(model)
    return apply_split_cuts(
        model, meaningful, audio_sample_rate=_audio_sample_rate(db, timeline_row)
    )


def rebuild_otio(
    db: Database, timeline_id: str, *, accepted: list[AcceptedSplit] | None = None
) -> None:
    row = repos.get_timeline(db, timeline_id)
    if row is None:
        return
    repos.update_timeline_otio(
        db, timeline_id, serialize_timeline_otio(db, row, accepted=accepted)
    )
