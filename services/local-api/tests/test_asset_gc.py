"""Redo-safe garbage collection of orphaned synthetic assets (#2).

A synthetic ``media_asset`` (``media_assets.synthetic=1``) is deleted ONLY when referenced by
NO live clip (video or audio) AND NO undo/redo history snapshot. Deleting an asset still
reachable via a redo snapshot would break redo (FK ``ON DELETE CASCADE`` on re-insert), so the
GC is deliberately conservative: any asset whose id appears in a ``timeline_history`` payload is
kept. On deletion the on-disk file is unlinked too.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.editing.asset_gc import gc_orphaned_synthetic_assets


def _mkdb(tmp_path: Path) -> SqliteDatabase:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    return db


def _project(db: SqliteDatabase) -> dict[str, Any]:
    return repos.create_project(
        db, name="P", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/ws"
    )


def _synthetic_audio(db: SqliteDatabase, project_id: str, path: Path) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00")
    return repos.create_asset(
        db,
        project_id=project_id,
        type="audio",
        display_name="Voiceover",
        source_path=str(path),
        synthetic=True,
        ai_effect="voiceover",
    )


def test_gc_keeps_asset_referenced_by_live_audio_clip(tmp_path: Path) -> None:
    db = _mkdb(tmp_path)
    project = _project(db)
    asset = _synthetic_audio(db, project["id"], tmp_path / "synthetic" / "vo.wav")
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.add_timeline_audio_clip(
        db, timeline_id=tl["id"], asset_id=asset["id"], seq_in_frame=0, seq_out_frame_exclusive=30
    )
    assert gc_orphaned_synthetic_assets(db, project_id=project["id"]) == []
    assert repos.get_asset(db, asset["id"]) is not None


def test_gc_keeps_synthetic_video_referenced_by_live_clip(tmp_path: Path) -> None:
    db = _mkdb(tmp_path)
    project = _project(db)
    vid = tmp_path / "synthetic" / "reenact.mp4"
    vid.parent.mkdir(parents=True, exist_ok=True)
    vid.write_bytes(b"\x00")
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="Reenact",
        source_path=str(vid), synthetic=True, ai_effect="reenact",
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.add_timeline_clip(
        db, timeline_id=tl["id"], asset_id=asset["id"],
        src_in_frame=0, src_out_frame_exclusive=30,
        seq_in_frame=0, seq_out_frame_exclusive=30,
    )
    assert gc_orphaned_synthetic_assets(db, project_id=project["id"]) == []
    assert repos.get_asset(db, asset["id"]) is not None


def test_gc_keeps_asset_referenced_only_by_history_snapshot(tmp_path: Path) -> None:
    db = _mkdb(tmp_path)
    project = _project(db)
    asset = _synthetic_audio(db, project["id"], tmp_path / "synthetic" / "vo.wav")
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    # No live clip; the asset lives only in an undo snapshot payload → redo could restore it.
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO timeline_history (id, timeline_id, seq_no, stack, label, payload_json, "
            "created_at) VALUES (?,?,?,?,?,?,?)",
            (
                "hist-1", tl["id"], 1, "undo", "Wörter gelöscht",
                json.dumps({"audio_clips": [{"asset_id": asset["id"]}]}), "2026-01-01T00:00:00Z",
            ),
        )
    assert gc_orphaned_synthetic_assets(db, project_id=project["id"]) == []
    assert repos.get_asset(db, asset["id"]) is not None


def test_gc_deletes_fully_orphaned_synthetic_asset_and_file(tmp_path: Path) -> None:
    db = _mkdb(tmp_path)
    project = _project(db)
    wav = tmp_path / "synthetic" / "vo.wav"
    asset = _synthetic_audio(db, project["id"], wav)
    assert wav.exists()
    # No live clip, no history reference → fully orphaned.
    assert gc_orphaned_synthetic_assets(db, project_id=project["id"]) == [asset["id"]]
    assert repos.get_asset(db, asset["id"]) is None
    assert not wav.exists()


def test_gc_ignores_non_synthetic_asset(tmp_path: Path) -> None:
    db = _mkdb(tmp_path)
    project = _project(db)
    src = tmp_path / "imported.mp4"
    src.write_bytes(b"\x00")
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="import", source_path=str(src)
    )
    assert gc_orphaned_synthetic_assets(db, project_id=project["id"]) == []
    assert repos.get_asset(db, asset["id"]) is not None
    assert src.exists()


def test_timeline_checkpoint_gcs_orphaned_synthetic_asset(tmp_path: Path) -> None:
    """A checkpointed edit runs the orphan GC as a best-effort post-step."""
    from laura.editing.history import timeline_checkpoint

    db = _mkdb(tmp_path)
    project = _project(db)
    wav = tmp_path / "synthetic" / "vo.wav"
    asset = _synthetic_audio(db, project["id"], wav)
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    with timeline_checkpoint(db, tl["id"], "Edit"):
        pass  # the edit does nothing to the (already orphaned) asset
    assert repos.get_asset(db, asset["id"]) is None
    assert not wav.exists()


def test_timeline_checkpoint_keeps_live_synthetic_asset(tmp_path: Path) -> None:
    """A synthetic asset still referenced by a live clip survives a checkpointed edit."""
    from laura.editing.history import timeline_checkpoint

    db = _mkdb(tmp_path)
    project = _project(db)
    wav = tmp_path / "synthetic" / "vo.wav"
    asset = _synthetic_audio(db, project["id"], wav)
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.add_timeline_audio_clip(
        db, timeline_id=tl["id"], asset_id=asset["id"], seq_in_frame=0, seq_out_frame_exclusive=30
    )
    with timeline_checkpoint(db, tl["id"], "Edit"):
        pass
    assert repos.get_asset(db, asset["id"]) is not None
