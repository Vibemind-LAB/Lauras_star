"""Tests: handle_render forwards reel options (vertical, hook_text, disclosure_text)
to render_clips_mp4. Uses monkeypatching — no ffmpeg required.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.jobs import JobRunner, default_registry, enqueue
from laura.render import handlers as render_handlers


def _build_db(tmp_path: Path) -> tuple[Any, Any, Any, Any]:
    """Create a minimal project + asset + timeline + one clip; return (db, project, tl, asset)."""
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
    return db, project, tl, asset


def _run_render_job(db: Any, project: Any, tl: Any, exp: Any) -> None:
    """Enqueue and drain a single export.render job."""
    registry = default_registry()
    render_handlers.register_render_handlers(registry)
    runner = JobRunner(db, registry)
    enqueue(
        db, queue="export", kind="export.render",
        payload={"export_id": exp["id"]},
        idempotency_key=f"render:{exp['id']}",
    )
    for _ in range(20):
        if not runner.run_once():
            break


def test_reel_options_forwarded_to_render(monkeypatch: Any, tmp_path: Path) -> None:
    """Options vertical/hook_text/disclosure_text reach render_clips_mp4."""
    captured: dict[str, Any] = {}

    def fake_render(*args: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(render_handlers, "render_clips_mp4", fake_render)

    db, project, tl, _ = _build_db(tmp_path)
    exp = repos.create_export(
        db,
        project_id=project["id"],
        timeline_id=tl["id"],
        format="mp4",
        options={"vertical": True, "hook_text": "Hook", "disclosure_text": "KI"},
    )

    _run_render_job(db, project, tl, exp)

    assert captured.get("vertical") is True
    assert captured.get("hook_text") == "Hook"
    assert captured.get("disclosure_text") == "KI"


def test_no_options_uses_defaults(monkeypatch: Any, tmp_path: Path) -> None:
    """When export has no options, render_clips_mp4 gets the off defaults."""
    captured: dict[str, Any] = {}

    def fake_render(*args: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(render_handlers, "render_clips_mp4", fake_render)

    db, project, tl, _ = _build_db(tmp_path)
    exp = repos.create_export(
        db,
        project_id=project["id"],
        timeline_id=tl["id"],
        format="mp4",
        options=None,
    )

    _run_render_job(db, project, tl, exp)

    assert captured.get("vertical") is False
    assert captured.get("hook_text") is None
    assert captured.get("disclosure_text") is None
