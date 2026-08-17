"""Deleting a production removes what it produced and nothing it was made from.

Seeding mirrors tests/test_api_scene_selection.py (project + asset + session + board under
``board_root_for``); the run directory here is filled by hand with the shapes the real board
writes — a render_report naming an export, a voice artifact naming its track, an archived
version naming an EARLIER export — because the point of the test is which of those the cleanup
finds, not how the pipeline produced them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from laura.api.short_creator import delete_production_session
from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.jobs.runner import enqueue
from laura.short_creator.board_models import BoardMeta
from laura.short_creator.production_orchestrator import board_root_for
from laura.short_creator.session_cleanup import collect_session_artifacts

_NOW = "2026-08-18T00:00:00Z"


def _db(tmp_path: Path) -> Database:
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db: Database = SqliteDatabase(settings.db_path)
    db.migrate()
    return db


def _seed(db: Database, tmp_path: Path, *, session_id: str = "sess-1") -> tuple[str, str, Path]:
    """Project + asset + session + a run directory holding a board. Returns
    ``(asset_id, project_id, workspace_root)``."""
    workspace_root = tmp_path / "ws" / "proj"
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(workspace_root),
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a",
        source_path=str(tmp_path / "input.mp4"),
    )
    repos.create_production_session(
        db, session_id=session_id, asset_id=asset["id"], created_utc=_NOW,
    )
    root = board_root_for(db, str(asset["id"]), session_id)
    root.mkdir(parents=True, exist_ok=True)
    (root / "versions").mkdir(exist_ok=True)
    (root / "meta.json").write_text(
        BoardMeta(
            session_id=session_id, asset_id=str(asset["id"]), created_utc=_NOW,
            task="t", target_seconds=30.0,
        ).model_dump_json(),
        encoding="utf-8",
    )
    return str(asset["id"]), str(project["id"]), workspace_root


def _write_run_artifacts(db: Database, asset_id: str, session_id: str, workspace_root: Path,
                         *, export_ids: list[str], voice_name: str) -> dict[str, Path]:
    """A board that names one export per version plus a voice track, with the files present."""
    root = board_root_for(db, asset_id, session_id)
    (root / "render_report.json").write_text(
        json.dumps({"export_id": export_ids[-1], "video_s": 30.0, "width": 1080, "height": 1920}),
        encoding="utf-8",
    )
    for i, export_id in enumerate(export_ids[:-1], start=1):
        (root / "versions" / f"render_report.v{i}.json").write_text(
            json.dumps({"export_id": export_id}), encoding="utf-8"
        )
    voice_dir = workspace_root / "voiceovers"
    voice_dir.mkdir(parents=True, exist_ok=True)
    mp3 = voice_dir / f"{voice_name}.mp3"
    timings = voice_dir / f"{voice_name}.mp3.timings.json"
    mp3.write_bytes(b"audio")
    timings.write_text("{}", encoding="utf-8")
    (root / "voice.json").write_text(
        json.dumps({"mp3_path": str(mp3), "timings_path": str(timings), "voice_s": 20.0}),
        encoding="utf-8",
    )
    sheet_dir = root.parent / "contact_sheets"
    sheet_dir.mkdir(parents=True, exist_ok=True)
    (sheet_dir / "sheet.png").write_bytes(b"png")
    return {"mp3": mp3, "timings": timings, "run_dir": root.parent}


def _seed_export(db: Database, project_id: str, workspace_root: Path, export_id: str) -> Path:
    """An exports row whose file exists on disk."""
    exports_dir = workspace_root / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    path = exports_dir / f"{export_id}.mp4"
    path.write_bytes(b"video")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO exports (id, project_id, timeline_id, format, status, path, "
            "size_bytes, created_at) VALUES (?, ?, NULL, 'mp4', 'ready', ?, 5, ?)",
            (export_id, project_id, str(path), _NOW),
        )
    return path


def test_delete_removes_run_dir_exports_and_voice_but_keeps_the_input(tmp_path: Path) -> None:
    db = _db(tmp_path)
    asset_id, project_id, workspace_root = _seed(db, tmp_path)
    paths = _write_run_artifacts(
        db, asset_id, "sess-1", workspace_root, export_ids=["exp-old", "exp-new"],
        voice_name="voice-1",
    )
    old_file = _seed_export(db, project_id, workspace_root, "exp-old")
    new_file = _seed_export(db, project_id, workspace_root, "exp-new")

    out = delete_production_session(db, "sess-1")

    assert sorted(out["exports_deleted"]) == ["exp-new", "exp-old"]
    assert out["board_removed"] is True
    assert not paths["run_dir"].exists()
    assert not old_file.exists() and not new_file.exists()
    assert not paths["mp3"].exists() and not paths["timings"].exists()
    assert repos.get_export(db, "exp-new") is None
    assert repos.get_production_session(db, "sess-1") is None
    # The input side is untouched: the asset, its project and its row all survive.
    assert repos.get_asset(db, asset_id) is not None
    assert repos.get_project(db, project_id) is not None


def test_delete_leaves_the_shared_per_line_voice_cache_alone(tmp_path: Path) -> None:
    # voiceovers/lines/ is keyed by content hash and shared across sessions — deleting one
    # production must not cost every other production its cache.
    db = _db(tmp_path)
    asset_id, project_id, workspace_root = _seed(db, tmp_path)
    _write_run_artifacts(
        db, asset_id, "sess-1", workspace_root, export_ids=["exp-1"], voice_name="voice-1"
    )
    _seed_export(db, project_id, workspace_root, "exp-1")
    cache = workspace_root / "voiceovers" / "lines"
    cache.mkdir(parents=True, exist_ok=True)
    cached = cache / "abc123.mp3"
    cached.write_bytes(b"line")

    delete_production_session(db, "sess-1")

    assert cached.exists()


def test_delete_refuses_while_the_session_is_running(tmp_path: Path) -> None:
    db = _db(tmp_path)
    asset_id, project_id, workspace_root = _seed(db, tmp_path)
    _write_run_artifacts(
        db, asset_id, "sess-1", workspace_root, export_ids=["exp-1"], voice_name="voice-1"
    )
    _seed_export(db, project_id, workspace_root, "exp-1")
    job_id = enqueue(db, queue="production", kind="production.run", payload={}, max_attempts=1)
    repos.set_production_session_job(db, "sess-1", job_id)

    with pytest.raises(HTTPException) as exc:
        delete_production_session(db, "sess-1")
    assert exc.value.status_code == 409
    assert repos.get_production_session(db, "sess-1") is not None
    assert repos.get_export(db, "exp-1") is not None  # nothing half-deleted


def test_delete_404s_on_an_unknown_session(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed(db, tmp_path)

    with pytest.raises(HTTPException) as exc:
        delete_production_session(db, "nope")
    assert exc.value.status_code == 404


def test_delete_works_on_a_board_too_broken_to_parse(tmp_path: Path) -> None:
    # The sessions most worth deleting are the half-written ones (a killed run leaves truncated
    # JSON behind). Cleanup still removes the directory and the session row.
    db = _db(tmp_path)
    asset_id, _project_id, workspace_root = _seed(db, tmp_path)
    root = board_root_for(db, asset_id, "sess-1")
    (root / "render_report.json").write_text('{"export_id": "exp-tru', encoding="utf-8")

    out = delete_production_session(db, "sess-1")

    assert out["board_removed"] is True
    assert out["exports_deleted"] == []
    assert repos.get_production_session(db, "sess-1") is None
    assert not root.parent.exists()


def test_delete_never_unlinks_a_path_outside_the_workspace(tmp_path: Path) -> None:
    # A board is a file the agent writes; a stray absolute path in it must not turn a cleanup
    # into an arbitrary delete.
    db = _db(tmp_path)
    asset_id, _project_id, workspace_root = _seed(db, tmp_path)
    outsider = tmp_path / "precious.mp3"
    outsider.write_bytes(b"not ours")
    root = board_root_for(db, asset_id, "sess-1")
    (root / "voice.json").write_text(
        json.dumps({"mp3_path": str(outsider), "voice_s": 1.0}), encoding="utf-8"
    )

    out = delete_production_session(db, "sess-1")

    assert outsider.exists()
    assert out["files_deleted"] == []


def test_collect_walks_versions_and_nested_shapes(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "board" / "versions").mkdir(parents=True)
    (run_dir / "board" / "render_report.json").write_text(
        json.dumps({"export_id": "exp-2", "checks": [{"name": "voice_fits"}]}), encoding="utf-8"
    )
    (run_dir / "board" / "versions" / "render_report.v1.json").write_text(
        json.dumps({"export_id": "exp-1"}), encoding="utf-8"
    )
    (run_dir / "board" / "voice.json").write_text(
        json.dumps({"mp3_path": "/ws/voiceovers/v.mp3", "segments": [{"mp3_path": "/ws/l.mp3"}]}),
        encoding="utf-8",
    )

    found = collect_session_artifacts(run_dir)

    assert found.export_ids == {"exp-1", "exp-2"}
    assert Path("/ws/voiceovers/v.mp3") in found.media_paths
    assert Path("/ws/l.mp3") in found.media_paths  # nested per-line segments count too


def test_collect_on_a_missing_directory_is_empty(tmp_path: Path) -> None:
    found = collect_session_artifacts(tmp_path / "gone")
    assert found.export_ids == set() and found.media_paths == set()
