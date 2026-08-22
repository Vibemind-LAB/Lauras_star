"""Unit test: timeline_checkpoint pushes a pre-edit snapshot onto the undo stack.

Also: the checkpoint write path (repos.push_undo_checkpoint) must survive concurrent
writers instead of dying "database is locked" (Task 8 hardening, pattern: ae01228's
chat-append fix).
"""

from __future__ import annotations

import json
import threading
import time

from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.editing.history import timeline_checkpoint


def test_checkpoint_pushes_pre_edit_snapshot(
    seeded_rough_cut: tuple[Database, str, str],
) -> None:
    db, tl, _ = seeded_rough_cut
    before = repos.capture_timeline_snapshot(db, tl)
    with timeline_checkpoint(db, tl, "Wörter gelöscht"):
        repos.replace_timeline_clips(db, tl, [])  # the "edit"
    st = repos.get_history_state(db, tl)
    assert st["can_undo"] and st["undo_label"] == "Wörter gelöscht"
    with db.connection() as conn:
        payload = conn.execute(
            "SELECT payload_json FROM timeline_history "
            "WHERE timeline_id=? ORDER BY seq_no DESC LIMIT 1",
            (tl,),
        ).fetchone()["payload_json"]
    assert json.loads(payload)["clips"] == before["clips"]  # the PRE-edit snapshot


def test_checkpoint_survives_a_concurrent_writers_commit(
    seeded_rough_cut: tuple[Database, str, str],
) -> None:
    """A concurrently committing writer must not kill push_undo_checkpoint.

    Mirrors the ae01228 regression test for chat append: push_row (called inside
    push_undo_checkpoint) reads MAX(seq_no) before its INSERT. A deferred BEGIN takes a
    read snapshot at that SELECT; if another connection commits before the INSERT
    upgrades to a write, SQLite fails immediately with SQLITE_BUSY_SNAPSHOT, bypassing
    busy_timeout entirely. The checkpoint must instead wait out the writer and succeed.
    """
    db, tl, _ = seeded_rough_cut
    assert isinstance(db, SqliteDatabase)  # the writer thread needs a raw .connect()

    lock_held = threading.Event()

    def hold_write_lock_then_commit() -> None:
        conn = db.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE timelines SET name=? WHERE id=?", ("held", tl))
            lock_held.set()
            # Long enough for the checkpoint's read to take its snapshot and start
            # waiting on the write lock — well under the 5s busy_timeout.
            time.sleep(0.5)
            conn.execute("COMMIT")
        finally:
            conn.close()

    writer = threading.Thread(target=hold_write_lock_then_commit)
    writer.start()
    try:
        assert lock_held.wait(5.0), "writer thread never acquired the lock"
        with timeline_checkpoint(db, tl, "Concurrent Edit"):
            repos.replace_timeline_clips(db, tl, [])
    finally:
        writer.join()

    st = repos.get_history_state(db, tl)
    assert st["can_undo"] and st["undo_label"] == "Concurrent Edit"


def test_concurrent_checkpoint_writers_no_database_locked(
    seeded_rough_cut: tuple[Database, str, str],
) -> None:
    """N threads hammering timeline_checkpoint on the same timeline concurrently must
    never raise "database is locked" — they may serialize (via busy_timeout) but must
    all eventually succeed."""
    db, tl, _ = seeded_rough_cut
    n_threads = 16
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        try:
            with timeline_checkpoint(db, tl, f"Edit {i}"):
                repos.replace_timeline_clips(db, tl, [])
        except BaseException as exc:  # noqa: BLE001 - capture every failure for the assert
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30.0)

    locked_errors = [e for e in errors if "database is locked" in str(e)]
    assert locked_errors == [], f"database is locked under concurrent checkpoints: {errors}"
    assert errors == [], f"unexpected errors under concurrent checkpoints: {errors}"

    # All 16 checkpoints landed (no writer starved permanently).
    with db.connection() as conn:
        cnt = conn.execute(
            "SELECT COUNT(*) AS c FROM timeline_history WHERE timeline_id=? AND stack='undo'",
            (tl,),
        ).fetchone()["c"]
    assert cnt == n_threads
