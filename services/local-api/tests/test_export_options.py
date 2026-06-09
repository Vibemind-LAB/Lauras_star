"""Round-trip tests for the exports.options JSON column (migration 0013)."""

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase


def _db(tmp_path):
    db = SqliteDatabase(Settings(workspace_root=tmp_path).db_path)
    db.migrate()
    return db


def _project_and_timeline(db):
    project = repos.create_project(
        db,
        name="Test",
        rate_num=25,
        rate_den=1,
        drop_frame=False,
        workspace_root="/tmp",
    )
    timeline = repos.create_timeline(db, project_id=project["id"], name="Cut", kind="rough_cut")
    return project, timeline


def test_create_export_with_options_round_trips(tmp_path):
    db = _db(tmp_path)
    project, timeline = _project_and_timeline(db)

    opts = {"vertical": True, "hook_text": "H"}
    export = repos.create_export(
        db,
        project_id=project["id"],
        timeline_id=timeline["id"],
        format="mp4",
        options=opts,
    )
    assert export["options"] == opts

    fetched = repos.get_export(db, export["id"])
    assert fetched is not None
    assert fetched["options"] == opts


def test_create_export_without_options_returns_empty_dict(tmp_path):
    db = _db(tmp_path)
    project, timeline = _project_and_timeline(db)

    export = repos.create_export(
        db,
        project_id=project["id"],
        timeline_id=timeline["id"],
        format="mp4",
    )
    assert export["options"] == {}

    fetched = repos.get_export(db, export["id"])
    assert fetched is not None
    assert fetched["options"] == {}


def test_list_exports_includes_options(tmp_path):
    db = _db(tmp_path)
    project, timeline = _project_and_timeline(db)

    opts = {"reel": "A", "vertical": False}
    repos.create_export(
        db,
        project_id=project["id"],
        timeline_id=timeline["id"],
        format="mp4",
        options=opts,
    )
    repos.create_export(
        db,
        project_id=project["id"],
        timeline_id=timeline["id"],
        format="mp4",
    )

    rows = repos.list_exports(db, project["id"])
    assert len(rows) == 2
    # Newest first; first created without options → last in list
    options_values = {r["options"] == opts for r in rows}
    assert True in options_values
    assert any(r["options"] == {} for r in rows)
