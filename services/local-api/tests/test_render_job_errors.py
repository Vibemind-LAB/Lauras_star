"""Regression: a non-ffmpeg failure during render marks the export ``error``, not stuck
``rendering``. Monkeypatches the renderer so no ffmpeg binary is required.

Job-safety invariant: the handler must persist failure on *any* exception (not only
``FFmpegError``); otherwise a deleted project, an ``OSError`` from mkdir, etc. would leave
the export row permanently in ``rendering``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.jobs import JobRunner, default_registry, enqueue
from laura.render import handlers as render_handlers


def test_render_job_marks_error_on_non_ffmpeg_failure(
    monkeypatch: Any, tmp_path: Path
) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(render_handlers, "render_clips_mp4", boom)

    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()

    proot = settings.workspace_root / "project"
    proot.mkdir(parents=True, exist_ok=True)
    project = repos.create_project(
        db, name="t", rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(proot),
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video",
        display_name="a.mp4", source_path=str(tmp_path / "a.mp4"),
    )
    tl = repos.create_timeline(
        db, project_id=project["id"], name="cut", kind="rough_cut",
    )
    repos.add_timeline_clip(
        db, timeline_id=tl["id"], asset_id=asset["id"],
        src_in_frame=0, src_out_frame_exclusive=30,
        seq_in_frame=0, seq_out_frame_exclusive=30,
    )
    exp = repos.create_export(
        db, project_id=project["id"], timeline_id=tl["id"], format="mp4",
    )

    registry = default_registry()
    render_handlers.register_render_handlers(registry)
    runner = JobRunner(db, registry)
    enqueue(
        db, queue="export", kind="export.render",
        payload={"export_id": exp["id"]}, idempotency_key=f"render:{exp['id']}",
    )
    for _ in range(20):  # bounded drain; retries are capped by the runner anyway
        if not runner.run_once():
            break

    row = repos.get_export(db, exp["id"])
    assert row is not None
    assert row["status"] == "error", row
    assert "disk gone" in (row["error"] or "")
