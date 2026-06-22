"""get_active_consent_id picks the newest non-revoked consent for the project."""

from __future__ import annotations

from pathlib import Path

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase


def _db(tmp_path: Path) -> SqliteDatabase:
    db = SqliteDatabase(Settings(workspace_root=tmp_path, start_runner=False).db_path)
    db.migrate()
    return db


def _project(db: SqliteDatabase, tmp_path: Path) -> str:
    p = repos.create_project(
        db,
        name="P",
        workspace_root=str(tmp_path / "ws"),
        rate_num=25,
        rate_den=1,
        drop_frame=False,
    )
    return str(p["id"])


def test_none_when_no_consent(tmp_path: Path) -> None:
    db = _db(tmp_path)
    assert repos.get_active_consent_id(db, _project(db, tmp_path)) is None


def test_returns_newest_non_revoked(tmp_path: Path) -> None:
    db = _db(tmp_path)
    pid = _project(db, tmp_path)
    c1 = repos.create_consent_record(db, project_id=pid, subject_label="Me")
    c2 = repos.create_consent_record(db, project_id=pid, subject_label="Me again")
    assert repos.get_active_consent_id(db, pid) == c2["id"]
    repos.revoke_consent_record(db, c2["id"])
    assert repos.get_active_consent_id(db, pid) == c1["id"]


def test_all_revoked_is_none(tmp_path: Path) -> None:
    db = _db(tmp_path)
    pid = _project(db, tmp_path)
    c1 = repos.create_consent_record(db, project_id=pid, subject_label="Me")
    repos.revoke_consent_record(db, c1["id"])
    assert repos.get_active_consent_id(db, pid) is None
