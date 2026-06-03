"""Backend import-status: migration, repo helpers, endpoint phases, retry."""

from __future__ import annotations

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.jobs import enqueue


def _fresh_db(tmp_path) -> SqliteDatabase:
    db = SqliteDatabase(Settings(workspace_root=tmp_path).db_path)
    db.migrate()
    return db


def test_set_and_get_fetch_job_progress(tmp_path) -> None:
    db = _fresh_db(tmp_path)
    job_id = enqueue(
        db, queue="ingest.io", kind="ingest.fetch",
        payload={"asset_id": "asset-1", "source_url": "http://x/y.mp4"},
        idempotency_key="fetch:asset-1",
    )
    assert repos.get_fetch_job(db, "asset-1")["id"] == job_id
    repos.set_job_progress(db, job_id, '{"downloaded":10,"total":100,"speed_bps":5}')
    again = repos.get_fetch_job(db, "asset-1")
    assert again["progress_json"] == '{"downloaded":10,"total":100,"speed_bps":5}'


def test_get_fetch_job_none_when_absent(tmp_path) -> None:
    db = _fresh_db(tmp_path)
    assert repos.get_fetch_job(db, "nope") is None
