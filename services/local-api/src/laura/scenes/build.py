"""Shared rough-cut + scene build helpers.

The same logic is needed in two places: the ``scenes:generate`` HTTP endpoint and the
post-analysis auto-build hook (smart handling — land an asset edit-ready with zero clicks).
Keeping it here avoids duplicating the clip-building / word-assignment / grouping logic.

All operations are idempotent and safe to call repeatedly (and on re-analysis):
``populate_rough_cut_from_shots`` is a no-op once the timeline has clips, and
``autobuild_asset_edit_ready`` never re-groups a timeline that already has scenes (so user
scene edits survive a re-analysis)."""

from __future__ import annotations

import os
from typing import Any

from ..analysis import cutplace
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


def _env_flag(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def _env_ms_to_frames(name: str, default_ms: int, asset: dict[str, Any]) -> int:
    """Read a millisecond env value and project it onto integer frames at the asset rate."""
    try:
        ms = max(0, int(os.environ.get(name, str(default_ms))))
    except ValueError:
        ms = default_ms
    rate_num = asset["rate_num"] or 30
    rate_den = asset["rate_den"] or 1
    return round(ms / 1000 * rate_num / rate_den)


def speech_keep_ranges(
    src_in: int, src_out: int, words: list[dict[str, Any]], *, pad: int, min_gap: int
) -> list[tuple[int, int]]:
    """Source sub-ranges of ``[src_in, src_out)`` to KEEP: speech (each word padded by ``pad``
    frames) with internal no-speech gaps of ``>= min_gap`` frames removed.

    All frames are integers and ranges stay end-exclusive (invariant #2). **Safety:** a clip with
    no words is returned unchanged — non-speech content (B-roll, music, silent intervals) is never
    trimmed. Leading/trailing dead-air is only trimmed when it is itself ``>= min_gap`` (otherwise
    the kept range extends to the clip edge, so we never shave a fraction off the head/tail)."""
    if not words:
        return [(src_in, src_out)]
    intervals: list[list[int]] = []
    for w in words:
        a = max(src_in, int(w["start_frame"]) - pad)
        b = min(src_out, int(w["end_frame"]) + pad)
        if b > a:
            intervals.append([a, b])
    if not intervals:
        return [(src_in, src_out)]
    intervals.sort()
    merged: list[list[int]] = [intervals[0]]
    for a, b in intervals[1:]:
        if a - merged[-1][1] < min_gap:  # gap too small to be worth cutting -> merge
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    if merged[0][0] - src_in < min_gap:  # short lead-in: keep it (no fractional head shave)
        merged[0][0] = src_in
    if src_out - merged[-1][1] < min_gap:  # short tail: keep it
        merged[-1][1] = src_out
    return [(a, b) for a, b in merged]


def tighten_rough_cut(
    db: Database,
    *,
    timeline_id: str,
    asset: dict[str, Any],
    run_id: str,
    clips: list[dict[str, Any]],
    pad: int,
    min_gap: int,
) -> tuple[list[dict[str, Any]], int]:
    """Remove dead-air from a freshly-populated rough cut by keeping only padded speech ranges of
    each clip (gaps ``>= min_gap`` dropped), re-sequenced gaplessly. Returns ``(clips, removed)``
    where ``removed`` is the number of source frames trimmed. A no-op (returns the input clips,
    ``removed=0``) when there is no transcript or nothing exceeds ``min_gap`` — so it never
    degrades a cut that has no dead-air, and never touches non-speech footage."""
    words = _asset_words(repos.get_transcript(db, asset["id"], run_id))
    words_by_clip = _assign_words(clips, words)
    rows: list[dict[str, Any]] = []
    offset = 0
    removed = 0
    for clip, clip_words in zip(clips, words_by_clip, strict=True):
        full = clip["src_out_frame_exclusive"] - clip["src_in_frame"]
        kept = 0
        for a, b in speech_keep_ranges(
            clip["src_in_frame"],
            clip["src_out_frame_exclusive"],
            clip_words,
            pad=pad,
            min_gap=min_gap,
        ):
            length = b - a
            rows.append(
                {
                    "asset_id": clip["asset_id"],
                    "src_in_frame": a,
                    "src_out_frame_exclusive": b,
                    "seq_in_frame": offset,
                    "seq_out_frame_exclusive": offset + length,
                }
            )
            offset += length
            kept += length
        removed += full - kept
    if removed <= 0:  # nothing trimmed -> leave the populated clips untouched
        return clips, 0
    repos.replace_timeline_clips(db, timeline_id, rows)
    return repos.list_timeline_clips(db, timeline_id), removed


def populate_rough_cut_from_shots(
    db: Database, timeline_id: str, asset_id: str, run_id: str
) -> list[dict[str, Any]]:
    """Fill an EMPTY timeline with gapless clips from the asset's kept shots; no-op if the
    timeline already has clips. Returns the timeline's clips (possibly empty)."""
    clips = repos.list_timeline_clips(db, timeline_id)
    if clips:
        return clips
    rows: list[dict[str, Any]] = []
    offset = 0
    for shot in repos.list_shots(db, asset_id, run_id):
        if not shot.get("keep", True):
            continue
        length = shot["src_out_frame_exclusive"] - shot["src_in_frame"]
        rows.append(
            {
                "src_in_frame": shot["src_in_frame"],
                "src_out_frame_exclusive": shot["src_out_frame_exclusive"],
                "seq_in_frame": offset,
                "seq_out_frame_exclusive": offset + length,
            }
        )
        offset += length
    # Unified editorial placement: refine the raw shot cuts onto transcript/audio seams using the
    # SAME joint_place logic the from-shots endpoint runs, so the zero-click import lands a clean,
    # frame-accurate cut instead of the bare shot boundary. A pure refinement — a no-op without a
    # transcript / readable video, so non-speech footage keeps its raw cut. Opt out with
    # LAURA_EDITORIAL_AUTOCUT=0.
    if len(rows) >= 2 and cutplace.editorial_autocut_enabled():
        asset = repos.get_asset(db, asset_id)
        if asset is not None:
            cutplace.apply_editorial_placement(db, asset=asset, run_id=run_id, rows=rows)
    for row in rows:
        repos.add_timeline_clip(
            db,
            timeline_id=timeline_id,
            asset_id=asset_id,
            src_in_frame=row["src_in_frame"],
            src_out_frame_exclusive=row["src_out_frame_exclusive"],
            seq_in_frame=row["seq_in_frame"],
            seq_out_frame_exclusive=row["seq_out_frame_exclusive"],
        )
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


def autobuild_asset_edit_ready(db: Database, *, project_id: str, asset_id: str, run_id: str) -> int:
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
    # Smart handling: tighten the auto-cut by dropping dead-air inside speech clips so the user
    # lands on a pacier cut with zero clicks. Transcript-gated + a no-op without dead-air, so
    # non-speech footage (B-roll, music, silent clips) is never touched. Opt out with
    # LAURA_AUTO_TIGHTEN=0. Only the auto path tightens; the explicit scenes:generate endpoint
    # keeps the full cut.
    if _env_flag("LAURA_AUTO_TIGHTEN", default=True):
        clips, _removed = tighten_rough_cut(
            db,
            timeline_id=timeline_id,
            asset=asset,
            run_id=run_id,
            clips=clips,
            pad=_env_ms_to_frames("LAURA_TIGHTEN_PAD_MS", 300, asset),
            min_gap=_env_ms_to_frames("LAURA_TIGHTEN_MIN_GAP_MS", 900, asset),
        )
    group_timeline_scenes(
        db,
        project_id=project_id,
        timeline_id=timeline_id,
        asset=asset,
        run_id=run_id,
        clips=clips,
    )
    return len(repos.list_scenes(db, timeline_id))
