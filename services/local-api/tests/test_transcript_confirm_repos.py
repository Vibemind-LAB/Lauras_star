"""Transcript confirmation gate repos (Transkript-Gates Task 1).

The confirmation stamp records user approval of an asset's transcript,
blocking downstream gates until explicitly confirmed.
"""

from pathlib import Path

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase


def _db(tmp_path: Path) -> SqliteDatabase:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    return db


def test_set_transcript_confirmed_at(tmp_path: Path) -> None:
    """Setting transcript_confirmed_at updates the media_assets row."""
    db = _db(tmp_path)
    project = repos.create_project(
        db,
        name="p",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root="/tmp/p",
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="a",
        source_path="/tmp/a.mp4",
    )
    asset_id = str(asset["id"])

    # Initially NULL
    row = repos.get_asset(db, asset_id)
    assert row is not None
    assert row.get("transcript_confirmed_at") is None

    # Set to a timestamp
    confirmed_utc = "2026-08-04T12:00:00+00:00"
    repos.set_transcript_confirmed_at(db, asset_id, confirmed_utc)

    # Verify it was set
    row = repos.get_asset(db, asset_id)
    assert row is not None
    assert row["transcript_confirmed_at"] == confirmed_utc


def test_set_transcript_confirmed_at_replaces(tmp_path: Path) -> None:
    """Calling set_transcript_confirmed_at twice replaces the previous value."""
    db = _db(tmp_path)
    project = repos.create_project(
        db,
        name="p",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root="/tmp/p",
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="a",
        source_path="/tmp/a.mp4",
    )
    asset_id = str(asset["id"])

    utc1 = "2026-08-04T12:00:00+00:00"
    repos.set_transcript_confirmed_at(db, asset_id, utc1)
    row = repos.get_asset(db, asset_id)
    assert row is not None
    assert row["transcript_confirmed_at"] == utc1

    utc2 = "2026-08-04T13:00:00+00:00"
    repos.set_transcript_confirmed_at(db, asset_id, utc2)
    row = repos.get_asset(db, asset_id)
    assert row is not None
    assert row["transcript_confirmed_at"] == utc2


def test_clear_transcript_confirmed_at(tmp_path: Path) -> None:
    """Clearing transcript_confirmed_at (passing None) resets it to NULL."""
    db = _db(tmp_path)
    project = repos.create_project(
        db,
        name="p",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root="/tmp/p",
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="a",
        source_path="/tmp/a.mp4",
    )
    asset_id = str(asset["id"])

    # Set it
    utc = "2026-08-04T12:00:00+00:00"
    repos.set_transcript_confirmed_at(db, asset_id, utc)
    row = repos.get_asset(db, asset_id)
    assert row is not None
    assert row["transcript_confirmed_at"] == utc

    # Clear it
    repos.set_transcript_confirmed_at(db, asset_id, None)
    row = repos.get_asset(db, asset_id)
    assert row is not None
    assert row.get("transcript_confirmed_at") is None
