"""``generate.video`` job handler (Axis 2, Slice 1).

Produces a clip via an injectable :class:`~laura.generate.backend.VideoGenerateBackend` and
registers it as a synthetic media asset (``ai_effect="generate_video"``) through the standard
asset path, so it joins the project's media pool and can be placed/edited like any import. v1 does
NOT auto-place the clip on a timeline — placement is a product decision left to the existing ops.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..db import repos
from ..jobs.runner import JobContext, JobHandler
from ..util import new_id
from .backend import VideoGenerateBackend, resolve_video_generate_backend


def handle_video_generate(
    ctx: JobContext, *, backend: VideoGenerateBackend | None = None
) -> dict[str, Any]:
    """Generate a clip for the payload's ``prompt`` and register it as a synthetic asset.

    Payload: ``{project_id, prompt, duration_frames}``. Returns
    ``{"ok": True, "asset_id": ..., "out_path": ...}`` or ``{"ok": False, "error": ...}``.
    """
    payload = ctx.payload
    project_id = str(payload["project_id"])
    prompt = str(payload["prompt"])
    duration_frames = int(payload["duration_frames"])

    project = repos.get_project(ctx.db, project_id)
    if project is None:
        return {"ok": False, "error": "project not found", "project_id": project_id}

    active: VideoGenerateBackend = backend or resolve_video_generate_backend()
    out_path = Path(str(project["workspace_root"])) / "generated" / f"{new_id()}.mp4"
    active.generate(
        prompt=prompt,
        out_path=out_path,
        duration_frames=duration_frames,
        fps_num=int(project["sequence_rate_num"]),
        fps_den=int(project["sequence_rate_den"]),
    )

    asset = repos.create_asset(
        ctx.db,
        project_id=project_id,
        type="video",
        display_name=f"Generated: {prompt[:40]}",
        source_path=str(out_path),
        synthetic=True,
        ai_effect="generate_video",
    )
    return {"ok": True, "asset_id": str(asset["id"]), "out_path": str(out_path)}


def register_generate_handlers(registry: dict[str, JobHandler]) -> None:
    """Register the ``generate.video`` handler on the job registry."""
    registry["generate.video"] = handle_video_generate
