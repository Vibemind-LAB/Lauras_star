"""Job handler: render a timeline's clips to an MP4 export."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..db import repos
from ..ingest.ffmpeg import FFmpegError
from ..jobs.runner import JobContext, JobHandler
from .mp4 import render_clips_mp4


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
    assert project is not None

    clips: list[tuple[Path, int, int]] = []
    for c in repos.list_timeline_clips(ctx.db, exp["timeline_id"]):
        a = repos.get_asset(ctx.db, c["asset_id"])
        assert a is not None
        clips.append((Path(a["source_path"]), c["src_in_frame"], c["src_out_frame_exclusive"]))

    if not clips:
        repos.set_export_error(ctx.db, export_id, "timeline has no clips")
        raise ValueError("no clips")

    dest = Path(project["workspace_root"]) / "exports" / f"{export_id}.mp4"
    try:
        render_clips_mp4(
            clips, dest,
            rate_num=project["sequence_rate_num"],
            rate_den=project["sequence_rate_den"],
        )
    except FFmpegError as e:
        repos.set_export_error(ctx.db, export_id, str(e)[-500:])
        raise

    repos.set_export_done(
        ctx.db, export_id,
        path=str(dest),
        size_bytes=os.path.getsize(dest),
    )
    return {"export_id": export_id, "path": str(dest)}


def register_render_handlers(registry: dict[str, JobHandler]) -> None:
    """Register all render-stage job handlers into ``registry``."""
    registry["export.render"] = handle_render
