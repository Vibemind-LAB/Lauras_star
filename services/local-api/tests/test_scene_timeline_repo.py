from __future__ import annotations

from pathlib import Path

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase


def test_set_scene_timeline_links(tmp_path: Path) -> None:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    repos.replace_scenes(db, "p1", "tl1", [(0, 30)])
    sid = repos.list_scenes(db, "tl1")[0]["id"]
    repos.set_scene_timeline(db, sid, "scene-tl-9")
    scene = repos.get_scene(db, sid)
    assert scene is not None
    assert scene["scene_timeline_id"] == "scene-tl-9"
