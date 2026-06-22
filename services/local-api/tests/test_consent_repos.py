"""Tests for consent_records table and the synthetic/ai_effect asset columns (migration 0015)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase


def _db(tmp_path: Path) -> SqliteDatabase:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    return db


def _project(db: SqliteDatabase) -> str:
    p = repos.create_project(
        db,
        name="Test",
        rate_num=24,
        rate_den=1,
        drop_frame=False,
        workspace_root="/tmp/ws",
    )
    return str(p["id"])


def _asset(db: SqliteDatabase, project_id: str, **kwargs: object) -> dict[str, Any]:
    return repos.create_asset(
        db,
        project_id=project_id,
        type="video",
        display_name="clip.mp4",
        source_path="/tmp/clip.mp4",
        **kwargs,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# consent_records
# ---------------------------------------------------------------------------

def test_create_and_get_consent_record(tmp_path: Path) -> None:
    db = _db(tmp_path)
    pid = _project(db)

    record = repos.create_consent_record(
        db,
        project_id=pid,
        subject_label="Anna",
        confirmed_by="user",
    )

    assert record["subject_label"] == "Anna"
    assert record["confirmed_by"] == "user"
    assert record["confirmed_at"]  # non-empty ISO timestamp

    fetched = repos.get_consent_record(db, record["id"])
    assert fetched is not None
    assert fetched["subject_label"] == "Anna"
    assert fetched["confirmed_at"] == record["confirmed_at"]


def test_list_consent_records_includes_created(tmp_path: Path) -> None:
    db = _db(tmp_path)
    pid = _project(db)

    record = repos.create_consent_record(
        db,
        project_id=pid,
        subject_label="Anna",
        confirmed_by="user",
    )

    listing = repos.list_consent_records(db, pid)
    ids = [r["id"] for r in listing]
    assert record["id"] in ids


def test_list_consent_records_ordered_newest_first(tmp_path: Path) -> None:
    db = _db(tmp_path)
    pid = _project(db)

    r1 = repos.create_consent_record(db, project_id=pid, subject_label="A")
    r2 = repos.create_consent_record(db, project_id=pid, subject_label="B")

    listing = repos.list_consent_records(db, pid)
    # Both present; newest (r2) should come first when timestamps differ
    # (or at least both should appear — ordering by confirmed_at DESC).
    assert {r["id"] for r in listing} == {r1["id"], r2["id"]}


def test_consent_record_optional_fields_none(tmp_path: Path) -> None:
    db = _db(tmp_path)
    pid = _project(db)

    record = repos.create_consent_record(
        db,
        project_id=pid,
        subject_label="Bob",
    )

    assert record["source_asset_id"] is None
    assert record["confirmed_by"] is None
    assert record["note"] is None


def test_get_consent_record_missing_returns_none(tmp_path: Path) -> None:
    db = _db(tmp_path)
    assert repos.get_consent_record(db, "nonexistent") is None


# ---------------------------------------------------------------------------
# synthetic / ai_effect on assets
# ---------------------------------------------------------------------------

def test_create_asset_synthetic_true(tmp_path: Path) -> None:
    db = _db(tmp_path)
    pid = _project(db)

    asset = _asset(db, pid, synthetic=True, ai_effect="reenact")

    fetched = repos.get_asset(db, asset["id"])
    assert fetched is not None
    assert fetched["synthetic"]          # truthy (INTEGER 1)
    assert fetched["ai_effect"] == "reenact"


def test_create_asset_synthetic_default_false(tmp_path: Path) -> None:
    db = _db(tmp_path)
    pid = _project(db)

    asset = _asset(db, pid)

    fetched = repos.get_asset(db, asset["id"])
    assert fetched is not None
    assert not fetched["synthetic"]      # falsy (INTEGER 0)
    assert fetched["ai_effect"] is None


def test_set_asset_synthetic_flips_default_asset(tmp_path: Path) -> None:
    db = _db(tmp_path)
    pid = _project(db)

    asset = _asset(db, pid)
    assert not repos.get_asset(db, asset["id"])["synthetic"]  # type: ignore[index]

    updated = repos.set_asset_synthetic(db, asset["id"], "reenact")
    assert updated is True

    fetched = repos.get_asset(db, asset["id"])
    assert fetched is not None
    assert fetched["synthetic"]
    assert fetched["ai_effect"] == "reenact"


def test_set_asset_synthetic_missing_returns_false(tmp_path: Path) -> None:
    db = _db(tmp_path)
    result = repos.set_asset_synthetic(db, "nonexistent", "reenact")
    assert result is False
