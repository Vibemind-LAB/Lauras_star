from __future__ import annotations

from pathlib import Path

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase


def _db(tmp_path: Path) -> SqliteDatabase:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    return db


def test_set_and_clear_scene_music(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repos.replace_scenes(db, "p1", "tl1", [(0, 30)])
    sid = repos.list_scenes(db, "tl1")[0]["id"]
    repos.set_scene_music(db, sid, "asset-9", 150)
    s = repos.get_scene(db, sid)
    assert s is not None
    assert s["music_asset_id"] == "asset-9" and s["music_gain_percent"] == 150
    repos.clear_scene_music(db, sid)
    s = repos.get_scene(db, sid)
    assert s is not None
    assert s["music_asset_id"] is None and s["music_gain_percent"] == 100


def test_get_scene_by_timeline(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repos.replace_scenes(db, "p1", "tl1", [(0, 30)])
    sid = repos.list_scenes(db, "tl1")[0]["id"]
    repos.set_scene_timeline(db, sid, "scene-tl-7")
    found = repos.get_scene_by_timeline(db, "scene-tl-7")
    assert found is not None
    assert found["id"] == sid
    assert repos.get_scene_by_timeline(db, "nope") is None
