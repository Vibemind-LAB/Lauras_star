"""Job handler: render a timeline's clips to an MP4 export."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..db import repos
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
    if project is None:
        repos.set_export_error(ctx.db, export_id, "project not found")
        raise ValueError("project not found")

    clips: list[tuple[Path, int, int]] = []
    for c in repos.list_timeline_clips(ctx.db, exp["timeline_id"]):
        a = repos.get_asset(ctx.db, c["asset_id"])
        if a is None:
            repos.set_export_error(ctx.db, export_id, f"asset not found: {c['asset_id']}")
            raise ValueError(f"asset not found: {c['asset_id']}")
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
