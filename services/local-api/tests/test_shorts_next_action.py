"""Tests for GET /shorts/{short_id}/next-action — pure read-model, no side effects.

TDD: tests are written before implementation. They fail until the endpoint + resolver exist.

State machine under test (first applicable):
  1. No proxy/waveform in asset_files          → blocked PROXY_PENDING
  2. Analysis run queued/running               → blocked ANALYSIS_RUNNING
  3. No analysis run at all                    → tool=analysis_run
  4. Analysis succeeded, no rough_cut timeline → tool=roughcut_from_shots
  5. rough_cut (or sequence) has clips but no
     succeeded render export                   → tool=render_reel
  6. A succeeded render export exists          → done
  7. Unknown short_id                          → 404
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(tmp_path: Path) -> tuple[TestClient, SqliteDatabase]:
    settings = Settings(workspace_root=tmp_path, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    client = TestClient(create_app(settings))
    client.__enter__()
    return client, db


def _create_project_and_asset(
    client: TestClient, db: SqliteDatabase
) -> tuple[dict[str, Any], dict[str, Any]]:
    project = client.post(
        "/projects", json={"name": "test", "sequence_rate_num": 30, "sequence_rate_den": 1}
    ).json()
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="clip.mov",
        source_path="/media/clip.mov",
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
    # Add at least one shot so from-shots can work later
    repos.insert_shots(
        db, asset_id=asset_id, run_id=run["id"],
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


def _row_counts(db: SqliteDatabase) -> dict[str, int]:
    """Snapshot row counts for no-writes assertion."""
    tables = [
        "projects", "media_assets", "asset_files", "analysis_runs",
        "timelines", "timeline_clips", "exports", "jobs",
    ]
    counts: dict[str, int] = {}
    with db.connection() as conn:
        for tbl in tables:
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {tbl}").fetchone()
            counts[tbl] = int(row["n"])
    return counts


# ---------------------------------------------------------------------------
# State 1: No proxy yet → PROXY_PENDING
# ---------------------------------------------------------------------------

def test_no_proxy_returns_blocked(tmp_path: Path) -> None:
    client, db = _make_client(tmp_path)
    try:
        _, asset = _create_project_and_asset(client, db)
        resp = client.get(f"/shorts/{asset['id']}/next-action")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["tool"] is None
        assert body["label_key"] == "next_action.preparing"
        assert "PROXY_PENDING" in body["blocked_by"]
        assert body["short_id"] == asset["id"]
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# State 2a: Proxy exists, analysis run is queued/running → ANALYSIS_RUNNING
# ---------------------------------------------------------------------------

def test_analysis_running_returns_blocked(tmp_path: Path) -> None:
    client, db = _make_client(tmp_path)
    try:
        _, asset = _create_project_and_asset(client, db)
        _add_proxy_file(db, asset["id"])
        # Create a queued run (not yet started)
        repos.create_analysis_run(
            db, asset_id=asset["id"], pipeline_version="1", config={"stages": {}}
        )
        resp = client.get(f"/shorts/{asset['id']}/next-action")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["tool"] is None
        assert body["label_key"] == "next_action.analyzing"
        assert "ANALYSIS_RUNNING" in body["blocked_by"]
    finally:
        client.__exit__(None, None, None)


def test_analysis_running_status_returns_blocked(tmp_path: Path) -> None:
    client, db = _make_client(tmp_path)
    try:
        _, asset = _create_project_and_asset(client, db)
        _add_proxy_file(db, asset["id"])
        run = repos.create_analysis_run(
            db, asset_id=asset["id"], pipeline_version="1", config={"stages": {}}
        )
        repos.start_analysis_run(db, run["id"])  # status -> "running"
        resp = client.get(f"/shorts/{asset['id']}/next-action")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["tool"] is None
        assert "ANALYSIS_RUNNING" in body["blocked_by"]
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# State 3: Proxy exists, no run at all → suggest analysis_run
# ---------------------------------------------------------------------------

def test_no_analysis_suggests_analysis_run(tmp_path: Path) -> None:
    client, db = _make_client(tmp_path)
    try:
        _, asset = _create_project_and_asset(client, db)
        _add_proxy_file(db, asset["id"])
        resp = client.get(f"/shorts/{asset['id']}/next-action")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["tool"] == "analysis_run"
        assert body["args"] == {"asset_id": asset["id"]}
        assert body["label_key"] == "next_action.analyze"
        assert body["blocked_by"] == []
    finally:
        client.__exit__(None, None, None)


# Also covers failed analysis (failed run → still suggest re-running analysis)
def test_failed_analysis_suggests_analysis_run(tmp_path: Path) -> None:
    client, db = _make_client(tmp_path)
    try:
        _, asset = _create_project_and_asset(client, db)
        _add_proxy_file(db, asset["id"])
        run = repos.create_analysis_run(
            db, asset_id=asset["id"], pipeline_version="1", config={"stages": {}}
        )
        repos.start_analysis_run(db, run["id"])
        repos.finish_analysis_run(db, run["id"], status="failed", diagnostics={})
        resp = client.get(f"/shorts/{asset['id']}/next-action")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["tool"] == "analysis_run"
        assert body["label_key"] == "next_action.analyze"
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# State 4: Analysis succeeded, no rough_cut timeline → suggest roughcut_from_shots
# ---------------------------------------------------------------------------

def test_analysis_done_no_cut_suggests_roughcut(tmp_path: Path) -> None:
    client, db = _make_client(tmp_path)
    try:
        project, asset = _create_project_and_asset(client, db)
        _add_proxy_file(db, asset["id"])
        _add_succeeded_analysis(db, asset["id"])
        resp = client.get(f"/shorts/{asset['id']}/next-action")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["tool"] == "roughcut_from_shots"
        assert body["args"] == {"asset_id": asset["id"]}
        assert body["label_key"] == "next_action.cut"
        assert body["blocked_by"] == []
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# State 5: rough_cut exists with clips, no export → suggest render_reel
# ---------------------------------------------------------------------------

def test_rough_cut_exists_suggests_render_reel(tmp_path: Path) -> None:
    client, db = _make_client(tmp_path)
    try:
        project, asset = _create_project_and_asset(client, db)
        _add_proxy_file(db, asset["id"])
        _add_succeeded_analysis(db, asset["id"])
        tl = _add_rough_cut_with_clips(db, project["id"], asset["id"])
        resp = client.get(f"/shorts/{asset['id']}/next-action")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["tool"] == "render_reel"
        assert body["args"] == {"timeline_id": tl["id"]}
        assert body["label_key"] == "next_action.build_reel"
        assert body["blocked_by"] == []
    finally:
        client.__exit__(None, None, None)


def test_sequence_timeline_preferred_over_rough_cut(tmp_path: Path) -> None:
    """If a kind=sequence timeline exists for the project it is preferred over rough_cut."""
    client, db = _make_client(tmp_path)
    try:
        project, asset = _create_project_and_asset(client, db)
        _add_proxy_file(db, asset["id"])
        _add_succeeded_analysis(db, asset["id"])
        # Also create a rough_cut (will be ignored)
        _add_rough_cut_with_clips(db, project["id"], asset["id"])
        # Create a sequence timeline for the project
        seq_tl = repos.create_timeline(
            db, project_id=project["id"], name="Sequenz", kind="sequence"
        )
        repos.add_timeline_clip(
            db,
            timeline_id=seq_tl["id"],
            asset_id=asset["id"],
            src_in_frame=0,
            src_out_frame_exclusive=100,
            seq_in_frame=0,
            seq_out_frame_exclusive=100,
        )
        resp = client.get(f"/shorts/{asset['id']}/next-action")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["tool"] == "render_reel"
        assert body["args"]["timeline_id"] == seq_tl["id"]
        assert body["label_key"] == "next_action.build_reel"
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# State 6: Export succeeded → done
# ---------------------------------------------------------------------------

def test_export_done_returns_done(tmp_path: Path) -> None:
    client, db = _make_client(tmp_path)
    try:
        project, asset = _create_project_and_asset(client, db)
        _add_proxy_file(db, asset["id"])
        _add_succeeded_analysis(db, asset["id"])
        tl = _add_rough_cut_with_clips(db, project["id"], asset["id"])
        exp = _add_succeeded_export(db, project["id"], tl["id"], tmp_path)
        resp = client.get(f"/shorts/{asset['id']}/next-action")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["tool"] is None
        assert body["label_key"] == "next_action.done"
        assert body["args"].get("export_id") == exp["id"]
        assert body["blocked_by"] == []
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# State 7: Unknown short_id → 404
# ---------------------------------------------------------------------------

def test_unknown_short_id_returns_404(tmp_path: Path) -> None:
    client, db = _make_client(tmp_path)
    try:
        resp = client.get("/shorts/nonexistent-id/next-action")
        assert resp.status_code == 404, resp.text
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Parity: endpoint body == resolver output for same state
# ---------------------------------------------------------------------------

def test_parity_endpoint_equals_resolver(tmp_path: Path) -> None:
    """The HTTP endpoint must return the exact same dict as the pure resolver function."""
    from laura.api.shorts import resolve_next_action

    client, db = _make_client(tmp_path)
    try:
        project, asset = _create_project_and_asset(client, db)
        _add_proxy_file(db, asset["id"])
        _add_succeeded_analysis(db, asset["id"])

        # Call the resolver directly
        resolver_result = resolve_next_action(db, asset["id"])
        assert resolver_result is not None

        # Call the endpoint
        resp = client.get(f"/shorts/{asset['id']}/next-action")
        assert resp.status_code == 200, resp.text
        endpoint_body = resp.json()

        # Both must agree on every field
        assert endpoint_body["tool"] == resolver_result.tool
        assert endpoint_body["args"] == resolver_result.args
        assert endpoint_body["label_key"] == resolver_result.label_key
        assert endpoint_body["reason"] == resolver_result.reason
        assert endpoint_body["blocked_by"] == resolver_result.blocked_by
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# No-writes: resolver must not mutate the DB
# ---------------------------------------------------------------------------

def test_resolver_performs_no_writes(tmp_path: Path) -> None:
    """Calling resolve_next_action must leave all row counts unchanged."""
    from laura.api.shorts import resolve_next_action

    client, db = _make_client(tmp_path)
    try:
        project, asset = _create_project_and_asset(client, db)
        _add_proxy_file(db, asset["id"])
        _add_succeeded_analysis(db, asset["id"])

        before = _row_counts(db)
        resolve_next_action(db, asset["id"])
        after = _row_counts(db)

        assert before == after, f"Resolver mutated DB: {before} -> {after}"
    finally:
        client.__exit__(None, None, None)


def test_endpoint_performs_no_writes(tmp_path: Path) -> None:
    """GET /shorts/{id}/next-action must leave all row counts unchanged."""
    client, db = _make_client(tmp_path)
    try:
        project, asset = _create_project_and_asset(client, db)
        _add_proxy_file(db, asset["id"])

        before = _row_counts(db)
        client.get(f"/shorts/{asset['id']}/next-action")
        after = _row_counts(db)

        assert before == after, f"Endpoint mutated DB: {before} -> {after}"
    finally:
        client.__exit__(None, None, None)
