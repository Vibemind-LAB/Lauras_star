"""Job handler: render a timeline's clips to an MP4 export."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..db import repos
from ..editing.otio_sync import resolve_clip_rows
from ..jobs.runner import JobContext, JobHandler
from ..sequences.music import sequence_music_tracks
from .audio import AudioOverlay
from .captions import build_ass, group_caption_lines
from .captions_source import timeline_caption_words
from .mp4 import VideoTransition, render_clips_mp4
from .sync import assert_or_fix_media_sync

_CAPTION_PRESETS: dict[str, tuple[int, int]] = {
    "reels": (1080, 1920),
    "tiktok": (1080, 1920),
    "shorts": (1080, 1920),
    "wide": (1920, 1080),
}


def cap_clips_to_frames(
    clips: list[tuple[Path, int, int]], budget_frames: int
) -> list[tuple[Path, int, int]]:
    """Keep only the first ``budget_frames`` output frames of a ``(path, src_in, src_out)`` clip
    list (deterministic tail-trim for the reel duration cap): full clips until the budget, the
    boundary clip trimmed to fit, the rest dropped. Integer frames, end-exclusive. No-op when
    ``budget_frames <= 0`` or the clips already fit within the budget."""
    if budget_frames <= 0:
        return clips
    out: list[tuple[Path, int, int]] = []
    used = 0
    for path, fin, fout in clips:
        length = fout - fin
        if used + length <= budget_frames:
            out.append((path, fin, fout))
            used += length
            if used == budget_frames:
                break
        else:
            remaining = budget_frames - used
            if remaining > 0:
                out.append((path, fin, fin + remaining))
            break
    return out


def snap_budget_to_word_boundary(
    budget_frames: int, total_frames: int, word_seq_ends: list[int]
) -> int:
    """Snap a reel duration-cap budget *down* to the latest transcript word boundary at or below
    ``budget_frames``, so a capped reel ends on a complete word instead of mid-word.

    ``word_seq_ends`` are end-exclusive *sequence* frames (e.g. the third element of each
    ``timeline_caption_words`` token). Prefix-preserving: the result is only ever <= the budget,
    so the kept clips still start at seq 0 and burned captions / music stay aligned.

    Returns ``budget_frames`` unchanged whenever a snap would be a no-op: a non-positive budget,
    a cut that already fits (``total_frames <= budget_frames``), or no word boundary lying in
    ``(0, budget_frames]`` (e.g. B-roll with no transcript) — so the no-transcript path is identical
    to the plain ``cap_clips_to_frames`` tail-trim."""
    if budget_frames <= 0 or total_frames <= budget_frames:
        return budget_frames
    candidates = [w for w in word_seq_ends if 0 < w <= budget_frames]
    if not candidates:
        return budget_frames
    return max(candidates)


def _option_str(opts: dict[str, object], key: str, default: str, allowed: set[str]) -> str:
    value = opts.get(key)
    if not isinstance(value, str):
        return default
    return value if value in allowed else default


def _option_int(opts: dict[str, object], key: str, default: int, *, lo: int, hi: int) -> int:
    value = opts.get(key)
    if not isinstance(value, int):
        return default
    return max(lo, min(hi, value))


def _timeline_audio_overlays(db: Any, timeline_id: str) -> list[AudioOverlay]:
    overlays: list[AudioOverlay] = []
    for clip in repos.list_timeline_audio_clips(db, timeline_id):
        asset = repos.get_asset(db, clip["asset_id"])
        if asset is None:
            raise ValueError(f"audio asset not found: {clip['asset_id']}")
        overlays.append(
            AudioOverlay(
                path=Path(asset["source_path"]),
                seq_in_frame=int(clip["seq_in_frame"]),
                seq_out_frame_exclusive=int(clip["seq_out_frame_exclusive"]),
                asset_in_frame=int(clip["asset_in_frame"]),
                gain_percent=int(clip["gain_percent"]),
                fade_in_frames=int(clip["fade_in_frames"]),
                fade_out_frames=int(clip["fade_out_frames"]),
                mix_mode=str(clip.get("mix_mode") or "mix"),
                ducking_percent=int(clip.get("ducking_percent") or 100),
            )
        )
    return overlays


def _scene_length(db: Any, scene: dict[str, Any]) -> int:
    scene_timeline_id = scene.get("scene_timeline_id")
    if not scene_timeline_id:
        return 0
    clips = repos.list_timeline_clips(db, str(scene_timeline_id))
    return max((int(c["seq_out_frame_exclusive"]) for c in clips), default=0)


def _sequence_video_transitions(db: Any, sequence_timeline_id: str) -> list[VideoTransition]:
    transitions: list[VideoTransition] = []
    boundary = 0
    items = repos.list_sequence_items(db, sequence_timeline_id)
    for index, item in enumerate(items):
        scene = repos.get_scene(db, item["scene_id"])
        if scene is None:
            continue
        boundary += _scene_length(db, scene)
        if index >= len(items) - 1:
            continue
        kind = str(item.get("transition_after_kind") or "hard")
        duration_frames = int(item.get("transition_after_frames") or 0)
        if kind == "hard" or duration_frames <= 0:
            continue
        transitions.append(
            VideoTransition(
                kind=kind,
                boundary_frame=boundary,
                duration_frames=duration_frames,
            )
        )
    return transitions


def _clip_video_transitions(db: Any, timeline_id: str) -> list[VideoTransition]:
    """VideoTransitions for a rough_cut/scene timeline, from clip-level transition fields.

    Mirrors :func:`_sequence_video_transitions` but walks lane-0 clips: the boundary frame is a
    clip's ``seq_out_frame_exclusive`` (== the next clip's seq-in), and the transition that plays
    after it comes from ``transition_after_kind/frames``. The final clip has no following clip, so
    a transition set on it is ignored."""
    transitions: list[VideoTransition] = []
    clips = [c for c in repos.list_timeline_clips(db, timeline_id) if int(c.get("lane") or 0) == 0]
    clips.sort(key=lambda c: int(c["seq_in_frame"]))
    for index, clip in enumerate(clips):
        if index >= len(clips) - 1:
            continue
        kind = str(clip.get("transition_after_kind") or "hard")
        duration_frames = int(clip.get("transition_after_frames") or 0)
        if kind == "hard" or duration_frames <= 0:
            continue
        transitions.append(
            VideoTransition(
                kind=kind,
                boundary_frame=int(clip["seq_out_frame_exclusive"]),
                duration_frames=duration_frames,
            )
        )
    return transitions


def handle_render(ctx: JobContext) -> dict[str, Any]:
    """Claim an export row, resolve its timeline clips, run ffmpeg, mark done."""
    export_id = ctx.payload["export_id"]
    exp = repos.get_export(ctx.db, export_id)
    if exp is None:
        raise ValueError(f"export not found: {export_id}")

    tl = repos.get_timeline(ctx.db, exp["timeline_id"])
    if tl is None:
        repos.set_export_error(ctx.db, export_id, "timeline not found")
        raise ValueError("timeline not found")

    project = repos.get_project(ctx.db, exp["project_id"])
    if project is None:
        repos.set_export_error(ctx.db, export_id, "project not found")
        raise ValueError("project not found")

    clip_rows = resolve_clip_rows(ctx.db, tl)
    clips: list[tuple[Path, int, int]] = []
    for c in clip_rows:
        a = repos.get_asset(ctx.db, c["asset_id"])
        if a is None:
            repos.set_export_error(ctx.db, export_id, f"asset not found: {c['asset_id']}")
            raise ValueError(f"asset not found: {c['asset_id']}")
        clips.append((Path(a["source_path"]), c["src_in_frame"], c["src_out_frame_exclusive"]))

    # Reel duration cap: keep only the first N seconds (deterministic tail-trim). Captions, music
    # and transitions reference seq positions from the start, so trimming the tail leaves the kept
    # portion aligned. No-op when unset or the cut is already within budget.
    max_s = (exp.get("options") or {}).get("max_duration_seconds")
    if isinstance(max_s, int) and max_s > 0:
        budget = round(max_s * project["sequence_rate_num"] / project["sequence_rate_den"])
        total = sum(fout - fin for _src, fin, fout in clips)
        # Smart end (default): snap the cut down to the latest transcript word boundary so the reel
        # finishes on a complete word, not mid-word. Still prefix-preserving (kept clips start at
        # seq 0), so alignment is untouched. Only consulted when a cut is actually needed; opt out
        # with reel_exact_duration=true to hit the exact frame budget.
        if total > budget and not (exp.get("options") or {}).get("reel_exact_duration"):
            word_ends = [w[2] for w in timeline_caption_words(ctx.db, exp["timeline_id"])]
            budget = snap_budget_to_word_boundary(budget, total, word_ends)
        clips = cap_clips_to_frames(clips, budget)

    if not clips:
        repos.set_export_error(ctx.db, export_id, "timeline has no clips")
        raise ValueError("no clips")

    # Build per-track music list:
    # - sequence timeline  → walk every scene; each with music contributes one track
    #   at its place in the assembled sequence.
    # - scene timeline     → at most one track covering the whole scene.
    # - other kinds        → no music.
    music_tracks: list[tuple[Path, int, int, int]] = []
    if tl["kind"] == "sequence":
        music_tracks = sequence_music_tracks(ctx.db, exp["timeline_id"])
    else:
        scene = repos.get_scene_by_timeline(ctx.db, exp["timeline_id"])
        if scene is not None and scene.get("music_asset_id"):
            masset = repos.get_asset(ctx.db, scene["music_asset_id"])
            if masset is not None:
                # Scene occupies [0, total_frames) — total = max seq_out across clip rows.
                total_frames = max(
                    (c["seq_out_frame_exclusive"] for c in clip_rows),
                    default=0,
                )
                music_tracks = [
                    (
                        Path(masset["source_path"]),
                        0,
                        total_frames,
                        int(scene["music_gain_percent"]),
                    )
                ]

    audio_overlays = _timeline_audio_overlays(ctx.db, exp["timeline_id"])
    video_transitions = (
        _sequence_video_transitions(ctx.db, exp["timeline_id"])
        if tl["kind"] == "sequence"
        else _clip_video_transitions(ctx.db, exp["timeline_id"])
    )

    opts: dict[str, object] = exp.get("options") or {}

    caption_ass: str | None = None
    if opts.get("captions"):
        words = timeline_caption_words(ctx.db, exp["timeline_id"])
        lines = group_caption_lines(words)
        if lines:
            preset = _option_str(
                opts, "caption_preset", "reels", {"reels", "tiktok", "shorts", "wide"}
            )
            play_w, play_h = _CAPTION_PRESETS[preset]
            caption_ass = build_ass(
                lines,
                rate_num=project["sequence_rate_num"],
                rate_den=project["sequence_rate_den"],
                play_w=play_w,
                play_h=play_h,
                fontsize=_option_int(opts, "caption_fontsize", 72, lo=24, hi=160),
                margin_v=_option_int(opts, "caption_safe_margin", 250, lo=0, hi=800),
                mode=_option_str(opts, "caption_mode", "karaoke", {"karaoke", "normal"}),
                position=_option_str(
                    opts, "caption_position", "bottom", {"top", "middle", "bottom"}
                ),
            )

    dest = Path(project["workspace_root"]) / "exports" / f"{export_id}.mp4"
    try:
        render_clips_mp4(
            clips,
            dest,
            rate_num=project["sequence_rate_num"],
            rate_den=project["sequence_rate_den"],
            music_tracks=music_tracks if music_tracks else None,
            audio_overlays=audio_overlays if audio_overlays else None,
            vertical=bool(opts.get("vertical", False)),
            hook_text=opts.get("hook_text"),  # type: ignore[arg-type]
            disclosure_text=opts.get("disclosure_text"),  # type: ignore[arg-type]
            caption_ass=caption_ass,
            video_transitions=video_transitions if video_transitions else None,
        )
        assert_or_fix_media_sync(
            dest,
            expected_frames=sum(fout - fin for _src, fin, fout in clips),
            rate_num=project["sequence_rate_num"],
            rate_den=project["sequence_rate_den"],
            require_video=True,
            fix=True,
        )
        size_bytes = os.path.getsize(dest)
    except Exception as e:  # noqa: BLE001 - persist the failure, drop partial output, re-raise
        repos.set_export_error(ctx.db, export_id, str(e)[-500:])
        if dest.exists():
            dest.unlink(missing_ok=True)
        raise

    repos.set_export_done(ctx.db, export_id, path=str(dest), size_bytes=size_bytes)
    return {"export_id": export_id, "path": str(dest)}


def register_render_handlers(registry: dict[str, JobHandler]) -> None:
    """Register all render-stage job handlers into ``registry``."""
    registry["export.render"] = handle_render
