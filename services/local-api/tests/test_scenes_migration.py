from __future__ import annotations

from pathlib import Path

from laura.config import Settings
from laura.db.database import SqliteDatabase


def test_scenes_migration_creates_table(tmp_path: Path) -> None:
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    with db.connection() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(scenes)").fetchall()}
    assert {
        "id", "project_id", "source_timeline_id", "name",
        "order_index", "seq_in_frame", "seq_out_frame_exclusive", "created_at",
    } <= cols
