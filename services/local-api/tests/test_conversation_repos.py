"""Conversation repos: the chat-first persistence layer (spec 2026-08-03).

seq must be gapless per conversation (the thread is rebuilt from it after restarts),
delete must cascade, and content_json round-trips as a dict.
"""
from __future__ import annotations

from pathlib import Path

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase


def _db(tmp_path: Path) -> Database:
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db: Database = SqliteDatabase(settings.db_path)
    db.migrate()
    return db


def test_create_list_get_and_title(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repos.create_conversation(db, conversation_id="c1", created_utc="2026-08-03T10:00:00Z")
    repos.create_conversation(db, conversation_id="c2", created_utc="2026-08-03T11:00:00Z")
    repos.set_conversation_title(db, "c1", "Bau mir einen Short")
    repos.touch_conversation(db, "c1", "2026-08-03T12:00:00Z")

    rows = repos.list_conversations(db)
    assert [r["id"] for r in rows] == ["c1", "c2"], "newest updated first"
    c1 = repos.get_conversation(db, "c1")
    assert c1 is not None and c1["title"] == "Bau mir einen Short"
    assert repos.get_conversation(db, "missing") is None


def test_messages_seq_is_gapless_and_content_round_trips(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repos.create_conversation(db, conversation_id="c1", created_utc="2026-08-03T10:00:00Z")
    s1 = repos.append_conversation_message(
        db, message_id="m1", conversation_id="c1", role="user", kind="text",
        content={"text": "hallo"}, created_utc="2026-08-03T10:00:01Z",
    )
    s2 = repos.append_conversation_message(
        db, message_id="m2", conversation_id="c1", role="assistant", kind="action",
        content={"tool": "start_short", "args": {}, "refs": {"job_id": "j1"}, "outcome": "running"},
        created_utc="2026-08-03T10:00:02Z",
    )
    assert (s1, s2) == (1, 2)
    msgs = repos.list_conversation_messages(db, "c1")
    assert [m["seq"] for m in msgs] == [1, 2]
    assert msgs[0]["content"] == {"text": "hallo"}
    assert msgs[1]["content"]["refs"]["job_id"] == "j1"


def test_update_message_content_and_get(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repos.create_conversation(db, conversation_id="c1", created_utc="2026-08-03T10:00:00Z")
    repos.append_conversation_message(
        db, message_id="m1", conversation_id="c1", role="assistant", kind="approval_request",
        content={"action_type": "import_urls", "payload": {"urls": ["u"]}, "status": "pending",
                 "decided_at": None, "result": None},
        created_utc="2026-08-03T10:00:01Z",
    )
    msg = repos.get_conversation_message(db, "m1")
    assert msg is not None and msg["content"]["status"] == "pending"
    repos.update_conversation_message_content(
        db, "m1", {**msg["content"], "status": "approved"}
    )
    updated = repos.get_conversation_message(db, "m1")
    assert updated is not None and updated["content"]["status"] == "approved"


def test_delete_cascades_messages(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repos.create_conversation(db, conversation_id="c1", created_utc="2026-08-03T10:00:00Z")
    repos.append_conversation_message(
        db, message_id="m1", conversation_id="c1", role="user", kind="text",
        content={"text": "x"}, created_utc="2026-08-03T10:00:01Z",
    )
    repos.delete_conversation(db, "c1")
    assert repos.get_conversation(db, "c1") is None
    assert repos.get_conversation_message(db, "m1") is None
