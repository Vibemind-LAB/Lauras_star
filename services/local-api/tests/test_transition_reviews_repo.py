"""Plan B / Task B4 — transition_reviews migration (0024) + repos."""

from __future__ import annotations

from laura.db import repos
from laura.db.database import Database


def _seed_timeline(db: Database) -> str:
    project = repos.create_project(
        db, name="P", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/ws"
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    return str(tl["id"])


def _upsert(
    db: Database,
    tl_id: str,
    *,
    seq: int = 100,
    smoothness: float = 0.2,
    label: str = "jump_cut",
    digest: str = "m1",
) -> None:
    repos.upsert_transition_review(
        db,
        timeline_id=tl_id,
        asset_a="A",
        asset_b="A",
        src_out_a=100,
        src_in_b=100,
        boundary_seq_frame=seq,
        boundary_signature="sig",
        smoothness=smoothness,
        label=label,
        reason="r",
        suggested_fix_json='{"kind":"transition"}',
        model_id="stub",
        model_digest=digest,
    )


def test_migration_adds_table(db: Database) -> None:
    with db.connection() as conn:
        cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(transition_reviews)").fetchall()
        }
    assert {"timeline_id", "asset_a", "src_out_a", "model_digest", "suggested_fix_json"} <= cols


def test_upsert_and_get_cached(db: Database) -> None:
    tl_id = _seed_timeline(db)
    _upsert(db, tl_id)
    row = repos.get_cached_review(
        db,
        timeline_id=tl_id,
        asset_a="A",
        asset_b="A",
        src_out_a=100,
        src_in_b=100,
        model_digest="m1",
    )
    assert row is not None and row["label"] == "jump_cut"
    assert (
        repos.get_cached_review(
            db,
            timeline_id=tl_id,
            asset_a="A",
            asset_b="A",
            src_out_a=100,
            src_in_b=100,
            model_digest="other",
        )
        is None
    )


def test_cache_key_ignores_seq_frame(db: Database) -> None:
    # Same semantic identity + digest, different seq position (upstream edit) -> one row, updated.
    tl_id = _seed_timeline(db)
    _upsert(db, tl_id, seq=100, smoothness=0.2)
    _upsert(db, tl_id, seq=500, smoothness=0.7)
    rows = repos.list_transition_reviews(db, tl_id)
    assert len(rows) == 1
    assert rows[0]["boundary_seq_frame"] == 500
    assert abs(rows[0]["smoothness"] - 0.7) < 1e-9


def test_different_digest_is_a_separate_row(db: Database) -> None:
    tl_id = _seed_timeline(db)
    _upsert(db, tl_id, digest="m1")
    _upsert(db, tl_id, digest="m2")
    assert len(repos.list_transition_reviews(db, tl_id)) == 2


def test_fk_cascade_on_timeline_delete(db: Database) -> None:
    tl_id = _seed_timeline(db)
    _upsert(db, tl_id)
    with db.transaction() as conn:
        conn.execute("DELETE FROM timelines WHERE id=?", (tl_id,))
    assert repos.list_transition_reviews(db, tl_id) == []
