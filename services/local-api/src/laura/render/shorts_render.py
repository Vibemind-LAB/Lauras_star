"""Job handler: render one ``shorts_candidates`` row to a vertical 9:16 MP4 with captions.

This is the final functional piece of the auto-shorts cutter. It routes a single chosen
candidate clip ``(source, start_frame, end_frame_exclusive)`` through the existing reel
renderer (:func:`laura.render.mp4.render_clips_mp4`) with ``vertical=True`` (center-crop to
1080×1920) and burned-in karaoke captions (+ optional hook text + loudness normalisation).

It deliberately mirrors :func:`laura.render.handlers.handle_render`:
* the API creates the ``exports`` row first (status ``rendering``) and enqueues this job
  with ``{"export_id": ...}``;
* the ``dest`` path is the same ``<workspace_root>/exports/<export_id>.mp4`` scheme;
* success → :func:`repos.set_export_done`; failure → :func:`repos.set_export_error` + re-raise
  with any partial output removed.

It does NOT reimplement rendering, the ASS builder, or line grouping — those are reused.

TODO(face-aware): ``render_clips_mp4(vertical=True)`` does a static center-crop
(``crop=ih*9/16:ih``). A face-aware per-frame crop controller exists
(``laura.analysis.crop_controller``) but per-frame ffmpeg cropping is out of scope here;
the center-crop is the deterministic baseline for this task.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from ..db import repos
from ..jobs.runner import JobContext, JobHandler
from .captions import build_ass, group_caption_lines
from .captions_source import candidate_caption_words
from .mp4 import render_clips_mp4
from .zoom import ZoomSpec, zoom_spec_from_option

_log = logging.getLogger(__name__)

# 9:16 vertical play resolution for the burned ASS captions (matches render_clips_mp4's
# vertical center-crop+scale to 1080×1920).
_PLAY_W = 1080
_PLAY_H = 1920


def _voiceover_caption_words(
    voiceover_path: str, script: str, total_frames: int, fps: float
) -> list[tuple[str, int, int]]:
    """Caption words for a re-voiced short. Integer frames, end-exclusive.

    Word-accurate from the TTS ``<mp3>.timings.json`` sidecar when present (live finding:
    evenly-spread captions drift audibly from the spoken voice); otherwise the script words
    spread evenly across the video (v1 fallback).
    """
    timings = Path(str(voiceover_path) + ".timings.json")
    if timings.is_file() and fps > 0:
        try:
            data = json.loads(timings.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        timed: list[tuple[str, int, int]] = []
        for row in data.get("words") or []:
            try:
                text = str(row["text"])
                start_f = int(round(float(row["start_s"]) * fps))
                end_f = int(round(float(row["end_s"]) * fps))
            except (KeyError, TypeError, ValueError):
                continue
            if start_f >= total_frames:
                break
            timed.append((text, start_f, max(start_f + 1, min(end_f, total_frames))))
        if timed:
            return timed
    script_words = [w for w in script.split() if w.strip()]
    if not script_words or total_frames <= 0:
        return []
    step = max(1, total_frames // len(script_words))
    return [(w, i * step, min((i + 1) * step, total_frames)) for i, w in enumerate(script_words)]


def _replace_audio(voice_path: Path, dest: Path) -> None:
    """Replace *dest*'s audio track with *voice_path* (video stream copied, no re-encode).

    ``-shortest`` trims to the shorter stream: a voice shorter than the video ends the short
    there; a longer voice is cut at the video's end — v1 semantics for the re-voiced short.
    """
    if not voice_path.is_file():
        raise ValueError(f"voiceover file not found: {voice_path}")
    from ..ingest.ffmpeg import run_ffmpeg

    tmp = dest.with_suffix(".voiced.mp4")
    run_ffmpeg(
        [
            "-i",
            str(dest),
            "-i",
            str(voice_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(tmp),
        ]
    )
    tmp.replace(dest)


def handle_shorts_render(ctx: JobContext) -> dict[str, Any]:
    """Render the candidate referenced by ``ctx.payload['export_id']`` to a 9:16 MP4.

    The export row carries the candidate id and render flags in its ``options``:
    ``candidate_id`` (required), ``captions`` (bool, default True),
    ``hook_text`` (str | None), ``loudnorm`` (bool, default True).

    Returns ``{"export_id", "path", "candidate_id", "frames", "captions"}``.
    Marks the export ``error`` and re-raises when the candidate/asset/project is missing or
    the render fails.
    """
    export_id = ctx.payload["export_id"]
    exp = repos.get_export(ctx.db, export_id)
    if exp is None:
        raise ValueError(f"export not found: {export_id}")

    opts: dict[str, Any] = exp.get("options") or {}

    # Source segments come either from persisted candidates (candidate_ids) or as raw
    # ``segments`` ranges (+ asset_id) — e.g. rough-cut scenes picked by number.
    candidate_ids: list[str] = []
    segment_ranges: list[tuple[int, int]]
    raw_segments = opts.get("segments")
    if raw_segments:
        asset_id = str(opts.get("asset_id") or "")
        if not asset_id:
            repos.set_export_error(ctx.db, export_id, "segments need asset_id")
            raise ValueError("segments need asset_id")
        segment_ranges = [(int(s), int(e)) for (s, e) in raw_segments]
        candidate_id: str | None = None
    else:
        raw_ids = opts.get("candidate_ids") or (
            [opts["candidate_id"]] if opts.get("candidate_id") else []
        )
        candidate_ids = [str(c) for c in raw_ids if c]
        if not candidate_ids:
            repos.set_export_error(ctx.db, export_id, "options missing candidate_id")
            raise ValueError("options missing candidate_id")
        candidates: list[dict[str, Any]] = []
        for cid in candidate_ids:
            row = repos.get_short_candidate(ctx.db, cid)
            if row is None:
                repos.set_export_error(ctx.db, export_id, f"candidate not found: {cid}")
                raise ValueError(f"candidate not found: {cid}")
            candidates.append(row)
        if len({str(c["asset_id"]) for c in candidates}) != 1:
            repos.set_export_error(ctx.db, export_id, "candidates span multiple assets")
            raise ValueError("candidates span multiple assets")
        asset_id = str(candidates[0]["asset_id"])
        candidate_id = candidate_ids[0]
        segment_ranges = [
            (int(c["start_frame"]), int(c["end_frame_exclusive"])) for c in candidates
        ]

    asset = repos.get_asset(ctx.db, asset_id)
    if asset is None:
        repos.set_export_error(ctx.db, export_id, f"asset not found: {asset_id}")
        raise ValueError(f"asset not found: {asset_id}")

    project = repos.get_project(ctx.db, asset["project_id"])
    if project is None:
        repos.set_export_error(ctx.db, export_id, f"project not found: {asset['project_id']}")
        raise ValueError(f"project not found: {asset['project_id']}")

    rate_num = int(project["sequence_rate_num"])
    rate_den = int(project["sequence_rate_den"])

    # Output format: vertical (default) renders onto an out_size canvas (default 1080×1920);
    # vertical=False is the native 16:9 pass-through (X/Twitter preset). Square (LinkedIn) is
    # vertical=True with out_size 1080×1080 — same clamp/fit/blur machinery.
    vertical = bool(opts.get("vertical", True))
    raw_size = opts.get("out_size") or [1080, 1920]
    out_size = (int(raw_size[0]), int(raw_size[1]))

    # zoom_hybrid: per-segment options, index-aligned with the segment list.
    # Missing/invalid entries and missing asset dimensions degrade to plain
    # blur-fill — never fail a render over a zoom hint.
    zoom_specs: list[ZoomSpec | None] | None = None
    zoom_raw = opts.get("zoom")
    if zoom_raw is not None:
        if not isinstance(zoom_raw, list) or len(zoom_raw) != len(segment_ranges):
            repos.set_export_error(ctx.db, export_id, "zoom must align 1:1 with segments")
            raise ValueError("zoom must align 1:1 with segments")
        src_w = asset.get("width")
        src_h = asset.get("height")
        if vertical and src_w and src_h:
            fps = rate_num / (rate_den or 1)
            zoom_specs = [
                zoom_spec_from_option(
                    z if isinstance(z, dict) else None,
                    src_w=int(src_w),
                    src_h=int(src_h),
                    out_w=out_size[0],
                    out_h=out_size[1],
                    segment_seconds=(end - start) / fps,
                )
                for z, (start, end) in zip(zoom_raw, segment_ranges, strict=True)
            ]
            if all(s is None for s in zoom_specs):
                zoom_specs = None
        else:
            _log.warning(
                "zoom requested but unusable (vertical=%s, dims=%s×%s) — blur fallback",
                vertical, src_w, src_h,
            )

    # The short is the ordered concat of the trimmed source segments (end-exclusive, integer
    # frames). One segment == the classic single-clip short.
    clips: list[tuple[Path, int, int]] = [
        (Path(asset["source_path"]), start, end) for (start, end) in segment_ranges
    ]
    total_frames = sum(end - start for (start, end) in segment_ranges)

    # Voiceover mode: a synthesized voice replaces the original audio (post-mux below); the
    # captions then come from the NEW script, its words spread evenly across the video (v1 —
    # word-exact timings from TTS timestamps are a later refinement).
    voiceover_path = opts.get("voiceover_path")
    voiceover_text = opts.get("voiceover_text")

    caption_ass: str | None = None
    if opts.get("captions", True) and voiceover_path and isinstance(voiceover_text, str):
        all_words: list[tuple[str, int, int]] = _voiceover_caption_words(
            str(voiceover_path), voiceover_text, total_frames, rate_num / (rate_den or 1)
        )
        if all_words:
            lines = group_caption_lines(all_words)
            if lines:
                play_w, play_h = out_size if vertical else (1920, 1080)
                caption_ass = build_ass(
                    lines, rate_num=rate_num, rate_den=rate_den, play_w=play_w, play_h=play_h
                )
    # Captions (default on): each segment's words are CLIP-LOCAL to its own trim; offset every
    # segment by the cumulative duration of the segments before it so the burned ASS stays
    # aligned across cuts. Missing run / no words is NOT an error — render without captions.
    elif opts.get("captions", True):
        run = repos.get_latest_transcript_run(ctx.db, asset["id"])
        if run is not None:
            all_words = []
            offset = 0
            for seg_start, seg_end in segment_ranges:
                words = candidate_caption_words(ctx.db, asset["id"], run["id"], seg_start, seg_end)
                all_words.extend((t, s + offset, e + offset) for (t, s, e) in words)
                offset += seg_end - seg_start
            lines = group_caption_lines(all_words)
            if lines:
                play_w, play_h = out_size if vertical else (1920, 1080)
                caption_ass = build_ass(
                    lines,
                    rate_num=rate_num,
                    rate_den=rate_den,
                    play_w=play_w,
                    play_h=play_h,
                )

    # Same dest scheme as handle_render: <workspace_root>/exports/<export_id>.mp4
    dest = Path(project["workspace_root"]) / "exports" / f"{export_id}.mp4"

    hook_text = opts.get("hook_text")
    loudnorm = bool(opts.get("loudnorm", True))
    reel_fit = bool(opts.get("reel_fit", False))
    reel_blur_fill = bool(opts.get("reel_blur_fill", False))
    try:
        render_clips_mp4(
            clips,
            dest,
            rate_num=rate_num,
            rate_den=rate_den,
            vertical=vertical,
            reel_fit=reel_fit,
            reel_blur_fill=reel_blur_fill,
            zoom_specs=zoom_specs,
            out_size=out_size,
            hook_text=hook_text if isinstance(hook_text, str) else None,
            caption_ass=caption_ass,
            loudnorm=loudnorm,
        )
        if voiceover_path:
            _replace_audio(Path(str(voiceover_path)), dest)
        size_bytes = os.path.getsize(dest)
    except Exception as e:  # noqa: BLE001 - persist the failure, drop partial output, re-raise
        repos.set_export_error(ctx.db, export_id, str(e)[-500:])
        if dest.exists():
            dest.unlink(missing_ok=True)
        raise

    repos.set_export_done(ctx.db, export_id, path=str(dest), size_bytes=size_bytes)
    _log.info(
        "shorts.render done export_id=%s candidate_id=%s segments=%d frames=%d captions=%s",
        export_id,
        candidate_id,
        len(segment_ranges),
        total_frames,
        bool(caption_ass),
    )
    return {
        "export_id": export_id,
        "path": str(dest),
        "candidate_id": str(candidate_id) if candidate_id else None,
        "candidate_ids": candidate_ids,
        "segments": len(segment_ranges),
        "frames": total_frames,
        "captions": bool(caption_ass),
    }


def register_shorts_render_handler(registry: dict[str, JobHandler]) -> None:
    """Register the ``shorts.render`` job handler into ``registry``."""
    registry["shorts.render"] = handle_shorts_render
