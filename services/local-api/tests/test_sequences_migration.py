from __future__ import annotations
from pathlib import Path
from laura.config import Settings
from laura.db.database import SqliteDatabase


def test_sequence_items_table(tmp_path: Path) -> None:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    with db.connection() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(sequence_items)").fetchall()}
    assert {"id", "sequence_timeline_id", "scene_id", "order_index", "created_at"} <= cols
