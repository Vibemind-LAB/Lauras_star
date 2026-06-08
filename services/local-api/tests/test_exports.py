from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase


def _db(tmp_path):
    db = SqliteDatabase(Settings(workspace_root=tmp_path).db_path)
    db.migrate()
    return db


def test_create_list_and_finish_export(tmp_path):
    db = _db(tmp_path)
    e = repos.create_export(db, project_id="p1", timeline_id="t1", format="mp4")
    assert e["status"] == "rendering"
    repos.set_export_done(db, e["id"], path="/x/out.mp4", size_bytes=1234)
    rows = repos.list_exports(db, "p1")
    assert len(rows) == 1 and rows[0]["status"] == "ready" and rows[0]["size_bytes"] == 1234


def test_set_export_error(tmp_path):
    db = _db(tmp_path)
    e = repos.create_export(db, project_id="p1", timeline_id=None, format="mp4")
    repos.set_export_error(db, e["id"], "boom")
    assert repos.get_export(db, e["id"])["status"] == "error"
