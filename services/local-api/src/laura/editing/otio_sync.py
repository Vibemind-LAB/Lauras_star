"""Build the canonical interchange model from DB rows and regenerate a timeline's OTIO.
Extracted so non-timeline routers (scenes) can keep OTIO as the source of truth without
importing private helpers from api/timelines.py."""
from __future__ import annotations

from typing import Any

from ..api.otio_split import (
    AcceptedSplit,
    AudioClip,
    accepted_offsets_from_otio,
    apply_split_cuts,
    split_audio_clips,
)
from ..db import repos
from ..db.database import Database
from ..interchange.otio_io import timeline_to_otio_string
from ..interchange.timeline import Timeline, timeline_from_rows
from ..sequences.flatten import flatten_sequence
from ..timebase.sampling import sample_to_frame


def resolve_clip_rows(db: Database, timeline_row: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the effective clip rows for a timeline.

    For ``kind="sequence"`` timelines the content is flattened from ordered scene
    references via ``flatten_sequence``; replace-overlay clips on the sequence itself
    are collected separately and applied via ``apply_overlay_precedence``.

    For all other timeline kinds, base and overlay rows are split by ``role`` and
    ``apply_overlay_precedence`` is called when overlays are present; otherwise the
    full ``list_timeline_clips`` result is returned unchanged (regression-safe).
    """
    if timeline_row.get("kind") == "sequence":
        base = flatten_sequence(db, timeline_row["id"])
        overlays = [
            c
            for c in repos.list_timeline_clips(db, timeline_row["id"])
            if c.get("role") == "replace"
        ]
    else:
        rows = repos.list_timeline_clips(db, timeline_row["id"])
        base = [c for c in rows if c.get("role", "base") != "replace"]
        overlays = [c for c in rows if c.get("role") == "replace"]
    if not overlays:
        return base  # regression-safe: byte-identical to the old path
    from .overlays import apply_overlay_precedence

    return apply_overlay_precedence(base, overlays)


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


def timeline_audio_sample_rate(db: Database, timeline_row: dict[str, Any]) -> int | None:
    """The audio sample rate to project split-edit boundaries onto samples (invariant #3).

    Picks the first clip asset that declares an ``audio_sample_rate`` (rough cuts are single-asset
    today). ``None`` when no asset carries one — the split is then frame-only and the sample
    projection is simply omitted, never guessed. Public so the accept endpoint can convert an
    accepted frame offset into the canonical ``audio_offset_samples`` it persists on the clip.
    """
    for clip_row in resolve_clip_rows(db, timeline_row):
        asset = repos.get_asset(db, clip_row["asset_id"])
        if asset and asset.get("audio_sample_rate"):
            return int(asset["audio_sample_rate"])
    return None


# Backward-compatible private alias (existing internal call sites).
_audio_sample_rate = timeline_audio_sample_rate


def accepted_offsets_from_clips(
    model: Timeline, audio_sample_rate: int | None
) -> list[AcceptedSplit]:
    """Derive the accepted L/J split offsets from the clip column (the new source of truth).

    Each clip carries a signed ``audio_offset_samples`` head offset (invariant #3: in samples).
    A non-zero offset is the leading-edge shift of the inter-clip cut that BEGINS the clip, so it
    maps to ``AcceptedSplit(seq_cut=clip.src_in_frame, offset=audio_frame - video_frame)``. The
    frame offset is the UI projection of the sample offset, recovered with the same sample<->frame
    math the accept path used to store it (so it round-trips exactly). Returns ``[]`` when no clip
    carries an offset (a pure hard-cut timeline) or when no audio sample rate is known to project
    samples onto frames — the caller then falls back to the legacy OTIO-metadata representation.
    """
    if audio_sample_rate is None:
        return []
    out: list[AcceptedSplit] = []
    for clip in model.ordered():
        samples = clip.audio_offset_samples
        if samples == 0:
            continue
        offset_frames = sample_to_frame(
            samples, audio_sample_rate, model.rate_num, model.rate_den
        )
        if offset_frames != 0:
            out.append(AcceptedSplit(seq_cut=clip.src_in_frame, offset=offset_frames))
    return out


def _resolve_accepted(
    db: Database,
    timeline_row: dict[str, Any],
    model: Timeline,
    accepted: list[AcceptedSplit] | None,
) -> list[AcceptedSplit]:
    """Resolve the accepted L/J splits for a build, honouring column-over-metadata precedence.

    Precedence (the m1 unification):

    1. an explicit ``accepted`` list (a fresh accept call) always wins;
    2. otherwise the per-clip ``audio_offset_samples`` COLUMN is the source of truth;
    3. only when no clip carries an offset do we fall back to the legacy
       ``accepted_offsets_from_otio`` metadata read, so timelines accepted before m1 (whose split
       lives only in the OTIO blob) still export correctly — nothing already accepted is lost.
    """
    if accepted is not None:
        return accepted
    from_clips = accepted_offsets_from_clips(model, _audio_sample_rate(db, timeline_row))
    if from_clips:
        return from_clips
    return accepted_offsets_from_otio(timeline_row.get("otio_json") or "")


def serialize_timeline_otio(
    db: Database,
    timeline_row: dict[str, Any],
    *,
    accepted: list[AcceptedSplit] | None = None,
) -> str:
    """Serialise a timeline to OTIO, carrying any accepted L/J split edits (migration-free).

    The stored ``otio_json`` is a cache regenerated from the clips table on every edit, so accepted
    split offsets must be re-applied at build time. They are sourced (in precedence order) from an
    explicit ``accepted`` list (a fresh accept call), the per-clip ``audio_offset_samples`` COLUMN
    (the m1 source of truth), or — only when no clip carries an offset — the legacy PREVIOUS blob's
    ``metadata["laura"]["accepted_split_offsets"]`` fallback, so a regenerate never clobbers a split
    back to a hard cut. See :func:`_resolve_accepted`.

    With no accepted splits this delegates to the byte-for-byte single-track writer Laura uses
    today — the split representation is purely additive.
    """
    model = build_model(db, timeline_row)
    accepted = _resolve_accepted(db, timeline_row, model, accepted)
    meaningful = [s for s in accepted if not s.is_hard()]
    if not meaningful:
        return timeline_to_otio_string(model)
    return apply_split_cuts(
        model, meaningful, audio_sample_rate=_audio_sample_rate(db, timeline_row)
    )


def export_audio_clips(
    db: Database,
    timeline_row: dict[str, Any],
    model: Timeline,
    *,
    accepted: list[AcceptedSplit] | None = None,
) -> list[AudioClip]:
    """The split-shifted audio clips for an NLE export, or ``[]`` when there is no accepted split.

    This routes the EDL / FCP7-XML / FCPXML exports through 3a's split-aware build: the accepted L/J
    offsets are sourced via :func:`_resolve_accepted` (per-clip ``audio_offset_samples`` COLUMN
    first, legacy ``otio_json`` blob metadata only as a fallback) and applied to the FRESHLY-built
    ``model`` via :func:`laura.api.otio_split.split_audio_clips`. The audio boundaries are
    sample-accurate (invariant #3) when an asset declares an audio sample rate.

    With no accepted (or only hard) splits this returns ``[]`` so the export stays byte-for-byte the
    single-track hard cut Laura emits today — additive and fully backward-compatible.
    """
    accepted = _resolve_accepted(db, timeline_row, model, accepted)
    meaningful = [s for s in accepted if not s.is_hard()]
    if not meaningful:
        return []
    return split_audio_clips(
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
