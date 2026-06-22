"""Tests for POST /shorts/batch-status — conveyor rollup, pure read, no side effects.

TDD: tests written before the implementation.  They FAIL until api/batch.py and
api/models.py are updated.

Test matrix (per the brief):
  1. Multi-stage rollup: manifest spanning all 7 stage keys → by_stage counts each
     correctly; total == len; all 7 stage keys present (zeros where none).
  2. needs_human: short with asset_policy mode="human" is counted; non-human /
     no-policy shorts are not.
  3. Unknown short_id → counted under not_found; does NOT abort the rollup.
  4. NO-WRITES assertion: row counts unchanged before/after batch_status call.
  5. Empty short_ids → 422.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app
from laura.policy import set_asset_policy

# ---------------------------------------------------------------------------
# Helpers (mirror the pattern from test_batch_plan.py)
# ---------------------------------------------------------------------------


def _make_client(tmp_path: Path) -> tuple[TestClient, SqliteDatabase]:
    settings = Settings(workspace_root=tmp_path, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    client = TestClient(create_app(settings))
    client.__enter__()
    return client, db


def _create_project_and_asset(
    client: TestClient, db: SqliteDatabase, name: str = "clip.mov"
) -> tuple[dict[str, Any], dict[str, Any]]:
    project = client.post(
        "/projects", json={"name": "test", "sequence_rate_num": 30, "sequence_rate_den": 1}
    ).json()
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name=name,
        source_path=f"/media/{name}",
    )
    return project, asset


def _add_proxy_file(db: SqliteDatabase, asset_id: str) -> None:
    repos.add_asset_file(
        db, asset_id=asset_id, kind="proxy", path="/workspace/proxy.mp4", is_proxy=True
    )


def _add_succeeded_analysis(db: SqliteDatabase, asset_id: str) -> dict[str, Any]:
    run = repos.create_analysis_run(
        db, asset_id=asset_id, pipeline_version="1", config={"stages": {}}
    )
    repos.start_analysis_run(db, run["id"])
    repos.finish_analysis_run(db, run["id"], status="succeeded", diagnostics={})
    repos.insert_shots(
        db,
        asset_id=asset_id,
        run_id=run["id"],
        shots=[{"src_in_frame": 0, "src_out_frame_exclusive": 100, "method": "test"}],
    )
    return repos.get_analysis_run(db, run["id"])  # type: ignore[return-value]


def _add_rough_cut_with_clips(
    db: SqliteDatabase, project_id: str, asset_id: str
) -> dict[str, Any]:
    tl = repos.create_timeline(
        db, project_id=project_id, name="RC", kind="rough_cut", created_from=asset_id
    )
    repos.add_timeline_clip(
        db,
        timeline_id=tl["id"],
        asset_id=asset_id,
        src_in_frame=0,
        src_out_frame_exclusive=100,
        seq_in_frame=0,
        seq_out_frame_exclusive=100,
    )
    return repos.get_timeline(db, tl["id"])  # type: ignore[return-value]


def _add_succeeded_export(
    db: SqliteDatabase, project_id: str, timeline_id: str, tmp_path: Path
) -> dict[str, Any]:
    exp = repos.create_export(db, project_id=project_id, timeline_id=timeline_id, format="mp4")
    out_path = tmp_path / "reel.mp4"
    out_path.write_bytes(b"fake")
    repos.set_export_done(db, exp["id"], path=str(out_path), size_bytes=4)
    return repos.get_export(db, exp["id"])  # type: ignore[return-value]


def _add_running_analysis(db: SqliteDatabase, asset_id: str) -> dict[str, Any]:
    run = repos.create_analysis_run(
        db, asset_id=asset_id, pipeline_version="1", config={"stages": {}}
    )
    repos.start_analysis_run(db, run["id"])
    return repos.get_analysis_run(db, run["id"])  # type: ignore[return-value]


def _row_counts(db: SqliteDatabase) -> dict[str, int]:
    """Snapshot row counts for no-writes assertion."""
    tables = [
        "projects",
        "media_assets",
        "asset_files",
        "analysis_runs",
        "timelines",
        "timeline_clips",
        "exports",
        "jobs",
    ]
    counts: dict[str, int] = {}
    with db.connection() as conn:
        for tbl in tables:
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {tbl}").fetchone()
            counts[tbl] = int(row["n"])
    return counts


# ---------------------------------------------------------------------------
# 1. Multi-stage rollup: all 7 stage keys; correct counts; total == len
# ---------------------------------------------------------------------------


def test_multi_stage_rollup(tmp_path: Path) -> None:
    """A manifest spanning several stages → by_stage counts each correctly; all 7 keys present."""
    client, db = _make_client(tmp_path)
    try:
        # Stage: preparing (no proxy)
        _, asset_preparing = _create_project_and_asset(client, db, "preparing.mov")

        # Stage: analyzing (analysis run queued/running)
        _, asset_analyzing = _create_project_and_asset(client, db, "analyzing.mov")
        _add_proxy_file(db, asset_analyzing["id"])
        _add_running_analysis(db, asset_analyzing["id"])

        # Stage: analyze (proxy ready, no analysis run)
        _, asset_analyze = _create_project_and_asset(client, db, "analyze.mov")
        _add_proxy_file(db, asset_analyze["id"])

        # Stage: cut (analysis done, no rough cut clips)
        _, asset_cut = _create_project_and_asset(client, db, "cut.mov")
        _add_proxy_file(db, asset_cut["id"])
        _add_succeeded_analysis(db, asset_cut["id"])

        # Stage: build (rough cut with clips, no export)
        project_build, asset_build = _create_project_and_asset(client, db, "build.mov")
        _add_proxy_file(db, asset_build["id"])
        _add_succeeded_analysis(db, asset_build["id"])
        _add_rough_cut_with_clips(db, project_build["id"], asset_build["id"])

        # Stage: done (export succeeded)
        project_done, asset_done = _create_project_and_asset(client, db, "done.mov")
        _add_proxy_file(db, asset_done["id"])
        _add_succeeded_analysis(db, asset_done["id"])
        tl_done = _add_rough_cut_with_clips(db, project_done["id"], asset_done["id"])
        _add_succeeded_export(db, project_done["id"], tl_done["id"], tmp_path)

        # not_found: use a nonexistent id to get a 7th stage
        ghost_id = "nonexistent-xyz"

        short_ids = [
            asset_preparing["id"],
            asset_analyzing["id"],
            asset_analyze["id"],
            asset_cut["id"],
            asset_build["id"],
            asset_done["id"],
            ghost_id,
        ]
        resp = client.post("/shorts/batch-status", json={"short_ids": short_ids})
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["total"] == len(short_ids)

        by_stage = body["by_stage"]
        # All 7 stage keys must be present
        expected_keys = {"preparing", "analyzing", "analyze", "cut", "build", "done", "not_found"}
        assert set(by_stage.keys()) == expected_keys, f"by_stage keys mismatch: {by_stage.keys()}"

        assert by_stage["preparing"] == 1
        assert by_stage["analyzing"] == 1
        assert by_stage["analyze"] == 1
        assert by_stage["cut"] == 1
        assert by_stage["build"] == 1
        assert by_stage["done"] == 1
        assert by_stage["not_found"] == 1

    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 2. needs_human: human-policy short is counted; others are not
# ---------------------------------------------------------------------------


def test_needs_human_count(tmp_path: Path) -> None:
    """Short with policy=human is counted; non-human / no-policy shorts are excluded."""
    client, db = _make_client(tmp_path)
    try:
        # Short with human policy
        _, asset_human = _create_project_and_asset(client, db, "human.mov")
        set_asset_policy(db, asset_human["id"], policy="human", source="row")

        # Short with auto policy
        _, asset_auto = _create_project_and_asset(client, db, "auto.mov")
        set_asset_policy(db, asset_auto["id"], policy="auto", source="row")

        # Short with no policy at all
        _, asset_none = _create_project_and_asset(client, db, "none.mov")

        short_ids = [asset_human["id"], asset_auto["id"], asset_none["id"]]
        resp = client.post("/shorts/batch-status", json={"short_ids": short_ids})
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["total"] == 3
        assert body["needs_human"] == 1, f"expected 1, got {body['needs_human']}"

    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 3. Unknown short_id → counted under not_found; no abort
# ---------------------------------------------------------------------------


def test_unknown_short_id_counted_as_not_found(tmp_path: Path) -> None:
    """Unknown short_id is bucketed as not_found; known shorts still resolve."""
    client, db = _make_client(tmp_path)
    try:
        _, asset_a = _create_project_and_asset(client, db, "known.mov")
        _add_proxy_file(db, asset_a["id"])
        _add_succeeded_analysis(db, asset_a["id"])

        short_ids = ["nonexistent-id-abc", asset_a["id"]]
        resp = client.post("/shorts/batch-status", json={"short_ids": short_ids})
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["total"] == 2
        assert body["by_stage"]["not_found"] == 1
        # The known short should have resolved to "cut" (analysis done, no rough cut)
        assert body["by_stage"]["cut"] == 1

    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 4. NO-WRITES: batch_status must not mutate the database
# ---------------------------------------------------------------------------


def test_batch_status_performs_no_writes(tmp_path: Path) -> None:
    """Calling batch_status (directly + via endpoint) leaves all row counts unchanged."""
    from laura.api.batch import batch_status

    client, db = _make_client(tmp_path)
    try:
        _, asset = _create_project_and_asset(client, db)
        _add_proxy_file(db, asset["id"])
        _add_succeeded_analysis(db, asset["id"])

        before = _row_counts(db)

        # Direct function call
        batch_status(db, [asset["id"]])

        # HTTP endpoint call
        client.post("/shorts/batch-status", json={"short_ids": [asset["id"]]})

        after = _row_counts(db)
        assert before == after, f"batch_status mutated DB: {before} -> {after}"

    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 5. Empty short_ids → 422
# ---------------------------------------------------------------------------


def test_empty_short_ids_returns_422(tmp_path: Path) -> None:
    """POST /shorts/batch-status with empty short_ids must return 422."""
    client, db = _make_client(tmp_path)
    try:
        resp = client.post("/shorts/batch-status", json={"short_ids": []})
        assert resp.status_code == 422, resp.text
    finally:
        client.__exit__(None, None, None)
