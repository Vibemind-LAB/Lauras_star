"""Persistence contracts for resumable visual-selection drafts."""

from __future__ import annotations

from pathlib import Path

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase


def _db(tmp_path: Path) -> SqliteDatabase:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    return db


def _session(db: SqliteDatabase, tmp_path: Path) -> tuple[str, str]:
    workspace = tmp_path / "ws" / "project"
    workspace.mkdir(parents=True, exist_ok=True)
    project = repos.create_project(
        db,
        name="drafts",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(workspace),
    )
    asset = repos.create_asset(
        db,
        project_id=str(project["id"]),
        type="video",
        display_name="source.mp4",
        source_path=str(workspace / "source.mp4"),
    )
    repos.create_production_session(
        db,
        session_id="session-1",
        asset_id=str(asset["id"]),
        created_utc="2026-08-17T08:00:00+00:00",
    )
    return str(asset["id"]), "session-1"


def _selections(candidate_id: str, *, included: bool = True) -> list[dict[str, object]]:
    return [
        {
            "rough_cut_order": 0,
            "candidate_id": candidate_id,
            "included": included,
            "requested_duration_s": 5,
        }
    ]


def test_visual_selection_draft_survives_database_reopen_and_updates_session(
    tmp_path: Path,
) -> None:
    """Catches drafts being tied to one backend process instead of SQLite."""
    db_a = _db(tmp_path)
    _, session_id = _session(db_a, tmp_path)

    first = repos.save_visual_selection_draft(
        db_a,
        session_id=session_id,
        proposal_hash="a" * 64,
        source_fingerprint="b" * 64,
        selections=_selections("candidate-a"),
        expected_revision=None,
        updated_utc="2026-08-17T08:01:00+00:00",
    )

    db_b = SqliteDatabase(db_a.db_path)
    db_b.migrate()
    assert repos.get_visual_selection_draft(db_b, session_id) == first
    assert first == {
        "session_id": session_id,
        "proposal_hash": "a" * 64,
        "source_fingerprint": "b" * 64,
        "selections": _selections("candidate-a"),
        "revision": 1,
        "updated_utc": "2026-08-17T08:01:00+00:00",
    }
    session = repos.get_production_session(db_b, session_id)
    assert session is not None
    assert session["updated_utc"] == "2026-08-17T08:01:00+00:00"


def test_visual_selection_draft_compare_and_swap_rejects_stale_writer(
    tmp_path: Path,
) -> None:
    """Catches an older Electron window silently overwriting a newer draft."""
    db_a = _db(tmp_path)
    _, session_id = _session(db_a, tmp_path)
    db_b = SqliteDatabase(db_a.db_path)
    db_b.migrate()

    repos.save_visual_selection_draft(
        db_a,
        session_id=session_id,
        proposal_hash="a" * 64,
        source_fingerprint="b" * 64,
        selections=_selections("candidate-a"),
        expected_revision=None,
        updated_utc="2026-08-17T08:01:00+00:00",
    )
    second = repos.save_visual_selection_draft(
        db_a,
        session_id=session_id,
        proposal_hash="a" * 64,
        source_fingerprint="b" * 64,
        selections=_selections("candidate-b", included=False),
        expected_revision=1,
        updated_utc="2026-08-17T08:02:00+00:00",
    )

    with pytest.raises(repos.DraftRevisionConflict) as conflict:
        repos.save_visual_selection_draft(
            db_b,
            session_id=session_id,
            proposal_hash="a" * 64,
            source_fingerprint="b" * 64,
            selections=_selections("candidate-stale"),
            expected_revision=1,
            updated_utc="2026-08-17T08:03:00+00:00",
        )

    assert second["revision"] == 2
    assert conflict.value.current == second
    assert repos.get_visual_selection_draft(db_b, session_id) == second
    session = repos.get_production_session(db_b, session_id)
    assert session is not None
    assert session["updated_utc"] == "2026-08-17T08:02:00+00:00"


def test_visual_selection_draft_delete_and_session_cascade_are_idempotent(
    tmp_path: Path,
) -> None:
    """Catches confirmed or deleted sessions leaving a resumable ghost draft."""
    db = _db(tmp_path)
    asset_id, session_id = _session(db, tmp_path)
    repos.save_visual_selection_draft(
        db,
        session_id=session_id,
        proposal_hash="a" * 64,
        source_fingerprint="b" * 64,
        selections=_selections("candidate-a"),
        expected_revision=None,
        updated_utc="2026-08-17T08:01:00+00:00",
    )

    repos.delete_visual_selection_draft(db, session_id)
    repos.delete_visual_selection_draft(db, session_id)
    assert repos.get_visual_selection_draft(db, session_id) is None

    repos.save_visual_selection_draft(
        db,
        session_id=session_id,
        proposal_hash="a" * 64,
        source_fingerprint="b" * 64,
        selections=_selections("candidate-a"),
        expected_revision=None,
        updated_utc="2026-08-17T08:02:00+00:00",
    )
    with db.transaction() as conn:
        conn.execute("DELETE FROM media_assets WHERE id=?", (asset_id,))
    assert repos.get_visual_selection_draft(db, session_id) is None
