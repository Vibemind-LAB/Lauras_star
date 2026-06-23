"""Task 9: POST /timelines/{id}/undo, POST /timelines/{id}/redo, GET /timelines/{id}/history."""

from __future__ import annotations

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase


def _seed(settings: Settings) -> tuple[SqliteDatabase, str]:
    """Open the same DB file the `client` fixture uses and seed a rough-cut timeline.

    Returns (db, timeline_id).  Mirrors the seeded_rough_cut fixture in conftest.py but
    against settings.db_path so the TestClient and this helper share the same SQLite file.
    """
    db = SqliteDatabase(settings.db_path)
    project = repos.create_project(
        db,
        name="history-test",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(settings.workspace_root / "project"),
    )
    timeline = repos.create_timeline(
        db,
        project_id=project["id"],
        name="rough",
        kind="rough_cut",
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="src",
        source_path=str(settings.workspace_root / "source.mp4"),
    )
    repos.add_timeline_clip(
        db,
        timeline_id=timeline["id"],
        asset_id=asset["id"],
        src_in_frame=0,
        src_out_frame_exclusive=30,
        seq_in_frame=0,
        seq_out_frame_exclusive=30,
        lane=0,
        role="base",
    )
    repos.replace_scenes(db, project["id"], timeline["id"], [(0, 30)])
    return db, timeline["id"]


def test_undo_redo_history_endpoints(client: TestClient, settings: Settings) -> None:
    db, tl = _seed(settings)

    # Make an undoable edit through the API so a checkpoint is created.
    # cut-at-frame on a midpoint (frame 15, inside the single [0..30) clip) creates a checkpoint.
    r0 = client.post(f"/timelines/{tl}/cut-at-frame", json={"at_seq_frame": 15})
    assert r0.status_code in (200, 422)  # any edit that goes through the checkpoint wrapper

    # If the edit was a no-op or the history is still empty, push a checkpoint directly.
    if client.get(f"/timelines/{tl}/history").json()["can_undo"] is False:
        repos.push_undo_checkpoint(db, tl, "Edit")

    # History should now report can_undo=True.
    hist = client.get(f"/timelines/{tl}/history").json()
    assert hist["can_undo"] is True, f"expected can_undo after checkpoint; got {hist}"

    # Undo should return 200 with {clips, scenes}.
    r_undo = client.post(f"/timelines/{tl}/undo")
    assert r_undo.status_code == 200, r_undo.text
    body = r_undo.json()
    assert "clips" in body, f"undo response missing 'clips': {body}"
    assert "scenes" in body, f"undo response missing 'scenes': {body}"

    # After undo, can_redo must be True.
    hist2 = client.get(f"/timelines/{tl}/history").json()
    assert hist2["can_redo"] is True, f"expected can_redo after undo; got {hist2}"

    # Redo should succeed.
    r_redo = client.post(f"/timelines/{tl}/redo")
    assert r_redo.status_code == 200, r_redo.text
    assert "clips" in r_redo.json()
    assert "scenes" in r_redo.json()


def test_undo_empty_is_409(client: TestClient, settings: Settings) -> None:
    _db, tl = _seed(settings)
    # Fresh timeline — no checkpoint on the undo stack.
    r = client.post(f"/timelines/{tl}/undo")
    assert r.status_code == 409, r.text


def test_redo_empty_is_409(client: TestClient, settings: Settings) -> None:
    _db, tl = _seed(settings)
    # Fresh timeline — no checkpoint on the redo stack either.
    r = client.post(f"/timelines/{tl}/redo")
    assert r.status_code == 409, r.text


def test_history_404_for_unknown_timeline(client: TestClient, settings: Settings) -> None:
    _seed(settings)  # ensure DB is migrated
    assert client.get("/timelines/no-such-id/history").status_code == 404
    assert client.post("/timelines/no-such-id/undo").status_code == 404
    assert client.post("/timelines/no-such-id/redo").status_code == 404
