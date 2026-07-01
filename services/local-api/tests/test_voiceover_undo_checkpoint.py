"""Undo checkpoint for voiceover creation (#2 — VO-Undo-Lücke).

Requesting a voiceover must push a pre-VO snapshot onto the undo stack so the
edit is reversible, exactly like the other synchronous editorial mutations.
The clip itself is added asynchronously by the ``ai.voiceover`` job; the
checkpoint captures the *pre-request* state so undo returns there (and cancels
the in-flight job via the existing history machinery).
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app

_TOKEN = "test-token"
_H = {"X-Laura-Token": _TOKEN}


def _app(tmp_path: Path) -> tuple[TestClient, SqliteDatabase]:
    settings = Settings(workspace_root=tmp_path / "ws", token=_TOKEN, start_runner=False)
    app = create_app(settings)
    db: SqliteDatabase = app.state.db
    return TestClient(app), db


def _seed_rough_cut(db: SqliteDatabase) -> str:
    """Project + rough_cut timeline with one lane-0 clip. Returns timeline_id."""
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a", source_path="/tmp/a.mp4"
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.add_timeline_clip(
        db,
        timeline_id=tl["id"],
        asset_id=asset["id"],
        src_in_frame=0,
        src_out_frame_exclusive=60,
        seq_in_frame=0,
        seq_out_frame_exclusive=60,
        lane=0,
    )
    return str(tl["id"])


def test_create_voiceover_pushes_undo_checkpoint(tmp_path: Path) -> None:
    client, db = _app(tmp_path)
    tl_id = _seed_rough_cut(db)

    # Precondition: nothing to undo yet.
    before = client.get(f"/timelines/{tl_id}/history", headers=_H).json()
    assert before["can_undo"] is False

    # Act: request a voiceover over [0, 30).
    r = client.post(
        f"/timelines/{tl_id}/voiceover",
        json={"text": "hallo welt", "seq_in_frame": 0, "seq_out_frame_exclusive": 30},
        headers=_H,
    )
    assert r.status_code == 202, r.text

    # Assert: the VO request is now on the undo stack, labelled.
    after = client.get(f"/timelines/{tl_id}/history", headers=_H).json()
    assert after["can_undo"] is True
    assert after["undo_label"] == "Voiceover erstellt"
