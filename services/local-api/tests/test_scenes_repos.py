from __future__ import annotations

from pathlib import Path

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase


def _db(tmp_path: Path) -> SqliteDatabase:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    return db


def test_replace_scenes_auto_names_and_orders(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repos.replace_scenes(db, "p1", "tl1", [(0, 30), (30, 90)])
    scenes = repos.list_scenes(db, "tl1")
    rows = [(s["name"], s["order_index"], s["seq_in_frame"], s["seq_out_frame_exclusive"])
            for s in scenes]
    assert rows == [
        ("Szene 1", 0, 0, 30),
        ("Szene 2", 1, 30, 90),
    ]


def test_replace_scenes_is_idempotent(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repos.replace_scenes(db, "p1", "tl1", [(0, 30)])
    repos.replace_scenes(db, "p1", "tl1", [(0, 60), (60, 90)])
    assert len(repos.list_scenes(db, "tl1")) == 2


def test_update_scene_name(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repos.replace_scenes(db, "p1", "tl1", [(0, 30)])
    sid = repos.list_scenes(db, "tl1")[0]["id"]
    repos.update_scene_name(db, sid, "Intro")
    scene = repos.get_scene(db, sid)
    assert scene is not None
    assert scene["name"] == "Intro"
