"""Tests: handle_render forces disclosure overlay when timeline has synthetic content.

Fix 1 — plain MP4 export must carry the EU AI Act disclosure when synthetic content
(lane-1 role='replace' clips, replace_original/mute_original audio, or synthetic assets)
is present and the caller did not already supply disclosure_text.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.jobs import JobRunner, default_registry, enqueue
from laura.render import handlers as render_handlers
from laura.render.mp4 import _DEFAULT_DISCLOSURE


def _build_db(tmp_path: Path) -> tuple[Any, Any, Any, Any]:
    """Minimal project + asset + timeline + one lane-0 clip."""
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


def _run_render_job(
    db: Any,
    project: Any,
    tl: Any,
    exp: Any,
    captured: dict[str, Any],
    monkeypatch: Any,
) -> None:
    def fake_render(*args: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(render_handlers, "render_clips_mp4", fake_render)

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


def test_no_synthetic_content_no_disclosure(monkeypatch: Any, tmp_path: Path) -> None:
    """Plain timeline with no synthetic clips: disclosure_text stays None."""
    captured: dict[str, Any] = {}
    db, project, tl, _ = _build_db(tmp_path)
    exp = repos.create_export(
        db, project_id=project["id"], timeline_id=tl["id"], format="mp4", options=None,
    )
    _run_render_job(db, project, tl, exp, captured, monkeypatch)
    assert captured.get("disclosure_text") is None


def test_replace_role_clip_forces_default_disclosure(monkeypatch: Any, tmp_path: Path) -> None:
    """Lane-1 role='replace' clip triggers automatic _DEFAULT_DISCLOSURE."""
    captured: dict[str, Any] = {}
    db, project, tl, asset = _build_db(tmp_path)

    # Add a lane-1 replace overlay clip
    repos.add_timeline_clip(
        db, timeline_id=tl["id"], asset_id=asset["id"],
        src_in_frame=0, src_out_frame_exclusive=30,
        seq_in_frame=0, seq_out_frame_exclusive=30,
        lane=1, role="replace",
    )

    exp = repos.create_export(
        db, project_id=project["id"], timeline_id=tl["id"], format="mp4", options=None,
    )
    _run_render_job(db, project, tl, exp, captured, monkeypatch)
    assert captured.get("disclosure_text") == _DEFAULT_DISCLOSURE


def test_replace_original_audio_forces_default_disclosure(monkeypatch: Any, tmp_path: Path) -> None:
    """Audio clip with mix_mode='replace_original' triggers automatic _DEFAULT_DISCLOSURE."""
    captured: dict[str, Any] = {}
    db, project, tl, asset = _build_db(tmp_path)

    audio_asset = repos.create_asset(
        db, project_id=project["id"], type="audio",
        display_name="vo.wav", source_path=str(tmp_path / "vo.wav"),
    )
    repos.add_timeline_audio_clip(
        db, timeline_id=tl["id"], asset_id=audio_asset["id"],
        seq_in_frame=0, seq_out_frame_exclusive=30,
        mix_mode="replace_original",
    )

    exp = repos.create_export(
        db, project_id=project["id"], timeline_id=tl["id"], format="mp4", options=None,
    )
    _run_render_job(db, project, tl, exp, captured, monkeypatch)
    assert captured.get("disclosure_text") == _DEFAULT_DISCLOSURE


def test_caller_provided_disclosure_is_respected(monkeypatch: Any, tmp_path: Path) -> None:
    """If the caller explicitly set disclosure_text (even to a custom value), it passes through."""
    captured: dict[str, Any] = {}
    db, project, tl, asset = _build_db(tmp_path)

    # Add a replace overlay — synthetic content present
    repos.add_timeline_clip(
        db, timeline_id=tl["id"], asset_id=asset["id"],
        src_in_frame=0, src_out_frame_exclusive=30,
        seq_in_frame=0, seq_out_frame_exclusive=30,
        lane=1, role="replace",
    )

    exp = repos.create_export(
        db, project_id=project["id"], timeline_id=tl["id"], format="mp4",
        options={"disclosure_text": "Custom"},
    )
    _run_render_job(db, project, tl, exp, captured, monkeypatch)
    # Custom value passes through as-is (not overridden to _DEFAULT_DISCLOSURE)
    assert captured.get("disclosure_text") == "Custom"
