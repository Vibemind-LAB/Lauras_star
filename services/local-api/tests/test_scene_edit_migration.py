from __future__ import annotations
from pathlib import Path
from laura.config import Settings
from laura.db.database import SqliteDatabase


def test_scene_edit_migration_adds_columns(tmp_path: Path) -> None:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    with db.connection() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(scenes)").fetchall()}
    assert {"scene_timeline_id", "music_asset_id", "music_gain_percent"} <= cols
