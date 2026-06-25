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

import logging
import os
from pathlib import Path
from typing import Any

from ..db import repos
from ..jobs.runner import JobContext, JobHandler
from .captions import build_ass, group_caption_lines
from .captions_source import candidate_caption_words
from .mp4 import render_clips_mp4

_log = logging.getLogger(__name__)

# 9:16 vertical play resolution for the burned ASS captions (matches render_clips_mp4's
# vertical center-crop+scale to 1080×1920).
_PLAY_W = 1080
_PLAY_H = 1920


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
    candidate_id = opts.get("candidate_id")
    if not candidate_id:
        repos.set_export_error(ctx.db, export_id, "options missing candidate_id")
        raise ValueError("options missing candidate_id")

    candidate = repos.get_short_candidate(ctx.db, str(candidate_id))
    if candidate is None:
        repos.set_export_error(ctx.db, export_id, f"candidate not found: {candidate_id}")
        raise ValueError(f"candidate not found: {candidate_id}")

    asset = repos.get_asset(ctx.db, candidate["asset_id"])
    if asset is None:
        repos.set_export_error(ctx.db, export_id, f"asset not found: {candidate['asset_id']}")
        raise ValueError(f"asset not found: {candidate['asset_id']}")

    project = repos.get_project(ctx.db, asset["project_id"])
    if project is None:
        repos.set_export_error(ctx.db, export_id, f"project not found: {asset['project_id']}")
        raise ValueError(f"project not found: {asset['project_id']}")

    rate_num = int(project["sequence_rate_num"])
    rate_den = int(project["sequence_rate_den"])
    start_frame = int(candidate["start_frame"])
    end_frame = int(candidate["end_frame_exclusive"])

    # The whole short is exactly one trimmed source clip (end-exclusive, integer frames).
    clips: list[tuple[Path, int, int]] = [
        (Path(asset["source_path"]), start_frame, end_frame)
    ]

    # Captions (default on): map the candidate's words to CLIP-LOCAL frames so the burned ASS
    # lines up with the trimmed clip (frame 0 == start_frame). Missing run / no words is NOT an
    # error — we render the clip without captions.
    caption_ass: str | None = None
    if opts.get("captions", True):
        run = repos.get_latest_analysis_run(ctx.db, asset["id"])
        if run is not None and run.get("status") == "succeeded":
            words = candidate_caption_words(
                ctx.db, asset["id"], run["id"], start_frame, end_frame
            )
            lines = group_caption_lines(words)
            if lines:
                caption_ass = build_ass(
                    lines,
                    rate_num=rate_num,
                    rate_den=rate_den,
                    play_w=_PLAY_W,
                    play_h=_PLAY_H,
                )

    # Same dest scheme as handle_render: <workspace_root>/exports/<export_id>.mp4
    dest = Path(project["workspace_root"]) / "exports" / f"{export_id}.mp4"

    hook_text = opts.get("hook_text")
    loudnorm = bool(opts.get("loudnorm", True))
    try:
        render_clips_mp4(
            clips,
            dest,
            rate_num=rate_num,
            rate_den=rate_den,
            vertical=True,
            hook_text=hook_text if isinstance(hook_text, str) else None,
            caption_ass=caption_ass,
            loudnorm=loudnorm,
        )
        size_bytes = os.path.getsize(dest)
    except Exception as e:  # noqa: BLE001 - persist the failure, drop partial output, re-raise
        repos.set_export_error(ctx.db, export_id, str(e)[-500:])
        if dest.exists():
            dest.unlink(missing_ok=True)
        raise

    repos.set_export_done(ctx.db, export_id, path=str(dest), size_bytes=size_bytes)
    _log.info(
        "shorts.render done export_id=%s candidate_id=%s frames=%d captions=%s",
        export_id,
        candidate_id,
        end_frame - start_frame,
        bool(caption_ass),
    )
    return {
        "export_id": export_id,
        "path": str(dest),
        "candidate_id": str(candidate_id),
        "frames": end_frame - start_frame,
        "captions": bool(caption_ass),
    }


def register_shorts_render_handler(registry: dict[str, JobHandler]) -> None:
    """Register the ``shorts.render`` job handler into ``registry``."""
    registry["shorts.render"] = handle_shorts_render
