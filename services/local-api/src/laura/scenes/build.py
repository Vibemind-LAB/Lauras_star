"""Shared rough-cut + scene build helpers.

The same logic is needed in two places: the ``scenes:generate`` HTTP endpoint and the
post-analysis auto-build hook (smart handling — land an asset edit-ready with zero clicks).
Keeping it here avoids duplicating the clip-building / word-assignment / grouping logic.

All operations are idempotent and safe to call repeatedly (and on re-analysis):
``populate_rough_cut_from_shots`` is a no-op once the timeline has clips, and
``autobuild_asset_edit_ready`` never re-groups a timeline that already has scenes (so user
scene edits survive a re-analysis)."""

from __future__ import annotations

from typing import Any

from ..db import repos
from ..db.database import Database
from .grouping import group_into_scenes


def default_gap_frames(asset: dict[str, Any]) -> int:
    """Default inter-clip silence gap (~1.5 s) in frames for scene grouping."""
    return round(1.5 * (asset["rate_num"] or 30) / (asset["rate_den"] or 1))


def _asset_words(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    for seg in transcript:
        spk = seg.get("speaker_id")
        for w in seg["words"]:
            words.append(
                {"start_frame": w["start_frame"], "end_frame": w["end_frame"], "speaker": spk}
            )
    words.sort(key=lambda w: w["start_frame"])
    return words


def _assign_words(
    clips: list[dict[str, Any]], words: list[dict[str, Any]]
) -> list[list[dict[str, Any]]]:
    out: list[list[dict[str, Any]]] = []
    for c in clips:
        lo, hi = c["src_in_frame"], c["src_out_frame_exclusive"]
        out.append([w for w in words if w["start_frame"] < hi and w["end_frame"] > lo])
    return out


def populate_rough_cut_from_shots(
    db: Database, timeline_id: str, asset_id: str, run_id: str
) -> list[dict[str, Any]]:
    """Fill an EMPTY timeline with gapless clips from the asset's kept shots; no-op if the
    timeline already has clips. Returns the timeline's clips (possibly empty)."""
    clips = repos.list_timeline_clips(db, timeline_id)
    if clips:
        return clips
    offset = 0
    for shot in repos.list_shots(db, asset_id, run_id):
        if not shot.get("keep", True):
            continue
        length = shot["src_out_frame_exclusive"] - shot["src_in_frame"]
        repos.add_timeline_clip(
            db,
            timeline_id=timeline_id,
            asset_id=asset_id,
            src_in_frame=shot["src_in_frame"],
            src_out_frame_exclusive=shot["src_out_frame_exclusive"],
            seq_in_frame=offset,
            seq_out_frame_exclusive=offset + length,
        )
        offset += length
    return repos.list_timeline_clips(db, timeline_id)


def group_timeline_scenes(
    db: Database,
    *,
    project_id: str,
    timeline_id: str,
    asset: dict[str, Any],
    run_id: str,
    clips: list[dict[str, Any]],
    gap_frames: int | None = None,
) -> None:
    """Group clips into scenes (words from transcript) and replace_scenes."""
    words = _asset_words(repos.get_transcript(db, asset["id"], run_id))
    words_by_clip = _assign_words(clips, words)
    gap = default_gap_frames(asset) if gap_frames is None else gap_frames
    ranges = group_into_scenes(clips, words_by_clip, gap_frames=gap)
    repos.replace_scenes(db, project_id, timeline_id, ranges)


def autobuild_asset_edit_ready(
    db: Database, *, project_id: str, asset_id: str, run_id: str
) -> int:
    """Idempotently make an asset edit-ready: ensure its rough-cut timeline exists, fill it
    from kept shots, group into scenes. Returns the scene count.

    - If there are no kept shots (no clips) -> return 0, build nothing.
    - If scenes ALREADY exist for the timeline -> do NOT re-group (preserve user edits);
      still ensure clips are populated; return the existing scene count.
    - Otherwise group and return the new scene count.
    Safe to call repeatedly and on re-analysis."""
    timeline = repos.get_or_create_asset_rough_cut(db, project_id, asset_id)
    timeline_id = str(timeline["id"])
    existing = repos.list_scenes(db, timeline_id)
    clips = populate_rough_cut_from_shots(db, timeline_id, asset_id, run_id)
    if not clips:
        return 0
    if existing:
        return len(existing)
    asset = repos.get_asset(db, asset_id)
    assert asset is not None
    group_timeline_scenes(
        db,
        project_id=project_id,
        timeline_id=timeline_id,
        asset=asset,
        run_id=run_id,
        clips=clips,
    )
    return len(repos.list_scenes(db, timeline_id))
