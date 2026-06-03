"""Backend import-status: migration, repo helpers, endpoint phases, retry."""

from __future__ import annotations

import json

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.ingest.handlers import _ProgressWriter
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


def test_progress_writer_throttles_and_writes(tmp_path, monkeypatch) -> None:
    db = _fresh_db(tmp_path)
    job_id = enqueue(
        db, queue="ingest.io", kind="ingest.fetch",
        payload={"asset_id": "a", "source_url": "http://x"}, idempotency_key="fetch:a",
    )
    clock = {"t": 1000.0}
    monkeypatch.setattr("laura.ingest.handlers.time.monotonic", lambda: clock["t"])

    w = _ProgressWriter(db, job_id, min_interval=1.0)
    w(0, 100)        # first call always writes
    w(10, 100)       # same instant -> throttled
    clock["t"] = 1001.5
    w(60, 100)       # >1s later -> writes; speed = 50 bytes / 1.5 s

    prog = json.loads(repos.get_fetch_job(db, "a")["progress_json"])
    assert prog["downloaded"] == 60
    assert prog["total"] == 100
    assert prog["speed_bps"] == pytest.approx(50 / 1.5, rel=0.2)
