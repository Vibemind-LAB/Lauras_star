from __future__ import annotations

from pathlib import Path

from laura.db.database import SqliteDatabase


def test_timeline_history_table_and_schema_version(tmp_path: Path) -> None:
    db = SqliteDatabase(tmp_path / "t.db")
    db.migrate()
    with db.connection() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(timeline_history)").fetchall()}
        ver = conn.execute("SELECT MAX(version) AS v FROM schema_meta").fetchone()["v"]
    assert cols == {"id", "timeline_id", "seq_no", "stack", "label", "payload_json", "created_at"}
    assert ver >= 29
