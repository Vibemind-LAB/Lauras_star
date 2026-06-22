"""P7-T4 — recipe_from_trace: reconstruct + verify a short_run's recipe from its export.

TDD suite (written before implementation):

1. succeeded short_run → recipe reconstructed, verified=True, available=True.
2. queued short_run (no trace) → recipe=None, available=False, no crash.
3. short_run whose export was deleted → available=False.
4. unknown run_id → 404.
5. tampered recipe_hash → verified=False while recipe still returned.
6. NO-WRITES assertion: recipe_from_trace must not mutate the database.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.ledger import get_ledger_store
from laura.main import create_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client_db(tmp_path: Path) -> tuple[TestClient, Database]:
    settings = Settings(workspace_root=tmp_path, token=None, start_runner=False)
    app = create_app(settings)
    return TestClient(app), app.state.db


def _project(client: TestClient) -> str:
    resp = client.post(
        "/projects", json={"name": "p", "sequence_rate_num": 30, "sequence_rate_den": 1}
    )
    assert resp.status_code in (200, 201)
    return str(resp.json()["id"])


def _asset_timeline(client: TestClient, db: Database, pid: str) -> tuple[str, str]:
    """Create an asset (with sha256) + a rough_cut timeline linked via created_from=asset_id."""
    asset = repos.create_asset(
        db,
        project_id=pid,
        type="video",
        display_name="clip.mp4",
        source_path="/tmp/clip.mp4",
    )
    with db.transaction() as conn:
        conn.execute(
            "UPDATE media_assets SET sha256=? WHERE id=?",
            ("deadbeef" * 8, asset["id"]),
        )
    tl = repos.create_timeline(
        db,
        project_id=pid,
        name="Rough Cut",
        kind="rough_cut",
        created_from=asset["id"],
    )
    return asset["id"], tl["id"]


def _mint_succeeded_run_with_export(
    db: Database, tmp_path: Path, *, recipe_options: dict[str, Any] | None = None
) -> tuple[str, str]:
    """Mint a short_run, create a matching export, update run to succeeded with trace.

    Returns (run_id, export_id).
    """
    store = get_ledger_store(db)
    run = store.record_run(short_id="short-abc", pipeline_version="test-1")
    run_id = run["id"]

    options: dict[str, Any] = recipe_options or {
        "vertical": True,
        "captions": False,
        "hook_text": "Hello!",
    }
    # The export carries the recipe options + short_run_id (as P7-T3 does)
    export_options: dict[str, Any] = dict(options)
    export_options["short_run_id"] = run_id

    # Use repos.create_export to store it
    exp = repos.create_export(
        db,
        project_id="proj-1",
        timeline_id="tl-1",
        format="mp4",
        options=export_options,
    )
    export_id = exp["id"]

    # Write a fake output file so path is realistic
    out = tmp_path / "reel.mp4"
    out.write_bytes(b"fake")

    # Update run to succeeded with trace
    store.update_run(
        run_id,
        status="succeeded",
        trace_json=json.dumps({"export_id": export_id, "output": str(out)}),
    )

    return run_id, export_id


def _row_counts(db: Database) -> dict[str, int]:
    """Snapshot row counts for no-writes assertion."""
    tables = [
        "projects", "media_assets", "asset_files", "analysis_runs",
        "timelines", "timeline_clips", "exports", "jobs", "short_runs", "asset_policies",
    ]
    counts: dict[str, int] = {}
    with db.connection() as conn:
        for tbl in tables:
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {tbl}").fetchone()
            counts[tbl] = int(row["n"])
    return counts


# ---------------------------------------------------------------------------
# 1. succeeded short_run → recipe reconstructed, verified=True, available=True
# ---------------------------------------------------------------------------


def test_recipe_from_trace_succeeded(tmp_path: Path) -> None:
    """Succeeded short_run: recipe reconstructed from export options, verified=True."""
    from laura.api.batch import recipe_from_trace
    from laura.ledger.recipe import compute_recipe_hash

    settings = Settings(workspace_root=tmp_path, token=None, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()

    recipe_options: dict[str, Any] = {
        "vertical": True,
        "captions": False,
        "hook_text": "Greetings",
        "caption_preset": "reels",
    }
    run_id, export_id = _mint_succeeded_run_with_export(db, tmp_path, recipe_options=recipe_options)

    # Also update the run with the correct recipe_hash for the options (minus short_run_id)
    expected_hash = compute_recipe_hash(recipe_options)
    with db.transaction() as conn:
        conn.execute(
            "UPDATE short_runs SET recipe_hash=? WHERE id=?",
            (expected_hash, run_id),
        )

    result = recipe_from_trace(db, run_id)

    assert result["available"] is True
    assert result["verified"] is True
    assert result["status"] == "succeeded"
    assert result["recipe"] is not None
    assert "short_run_id" not in result["recipe"], "recipe must not contain short_run_id"
    # Recipe should match original options
    assert result["recipe"] == recipe_options
    assert result["recipe_hash"] == expected_hash


def test_recipe_from_trace_succeeded_via_http(tmp_path: Path) -> None:
    """GET /short-runs/{run_id}/recipe returns 200 with correct body for succeeded run."""
    from laura.ledger.recipe import compute_recipe_hash

    client, db = _make_client_db(tmp_path)
    recipe_options: dict[str, Any] = {"vertical": True, "captions": False}
    run_id, _ = _mint_succeeded_run_with_export(db, tmp_path, recipe_options=recipe_options)

    expected_hash = compute_recipe_hash(recipe_options)
    with db.transaction() as conn:
        conn.execute(
            "UPDATE short_runs SET recipe_hash=? WHERE id=?",
            (expected_hash, run_id),
        )

    resp = client.get(f"/short-runs/{run_id}/recipe")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is True
    assert body["verified"] is True
    assert body["recipe"] is not None
    assert "short_run_id" not in body["recipe"]
    assert body["recipe_hash"] == expected_hash


# ---------------------------------------------------------------------------
# 2. queued short_run (no trace) → recipe=None, available=False, no crash
# ---------------------------------------------------------------------------


def test_recipe_from_trace_queued(tmp_path: Path) -> None:
    """Queued short_run has no trace → available=False, recipe=None, no crash."""
    from laura.api.batch import recipe_from_trace

    settings = Settings(workspace_root=tmp_path, token=None, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()

    store = get_ledger_store(db)
    run = store.record_run(short_id="short-q", pipeline_version="test-1")
    run_id = run["id"]

    result = recipe_from_trace(db, run_id)

    assert result["available"] is False
    assert result["verified"] is False
    assert result["recipe"] is None
    assert result["status"] == "queued"


def test_recipe_from_trace_queued_via_http(tmp_path: Path) -> None:
    """GET /short-runs/{run_id}/recipe for a queued run returns 200 with available=False."""
    client, db = _make_client_db(tmp_path)
    store = get_ledger_store(db)
    run = store.record_run(short_id="short-q2", pipeline_version="test-1")

    resp = client.get(f"/short-runs/{run['id']}/recipe")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is False
    assert body["recipe"] is None


# ---------------------------------------------------------------------------
# 3. short_run whose export was deleted → available=False
# ---------------------------------------------------------------------------


def test_recipe_from_trace_deleted_export(tmp_path: Path) -> None:
    """Export deleted after run succeeded → available=False."""
    from laura.api.batch import recipe_from_trace

    settings = Settings(workspace_root=tmp_path, token=None, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()

    run_id, export_id = _mint_succeeded_run_with_export(db, tmp_path)

    # Delete the export row
    with db.transaction() as conn:
        conn.execute("DELETE FROM exports WHERE id=?", (export_id,))

    result = recipe_from_trace(db, run_id)

    assert result["available"] is False
    assert result["verified"] is False
    assert result["recipe"] is None


def test_recipe_from_trace_deleted_export_via_http(tmp_path: Path) -> None:
    """GET /short-runs/{run_id}/recipe when export deleted → 200, available=False."""
    client, db = _make_client_db(tmp_path)
    run_id, export_id = _mint_succeeded_run_with_export(db, tmp_path)

    with db.transaction() as conn:
        conn.execute("DELETE FROM exports WHERE id=?", (export_id,))

    resp = client.get(f"/short-runs/{run_id}/recipe")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is False
    assert body["recipe"] is None


# ---------------------------------------------------------------------------
# 4. unknown run_id → 404
# ---------------------------------------------------------------------------


def test_recipe_from_trace_unknown_run_id(tmp_path: Path) -> None:
    """Unknown run_id → HTTP 404."""
    client, db = _make_client_db(tmp_path)
    resp = client.get("/short-runs/does-not-exist/recipe")
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# 5. tampered recipe_hash → verified=False, recipe still returned
# ---------------------------------------------------------------------------


def test_recipe_from_trace_tampered_hash(tmp_path: Path) -> None:
    """Tampered recipe_hash stored in short_run → verified=False but recipe returned."""
    from laura.api.batch import recipe_from_trace

    settings = Settings(workspace_root=tmp_path, token=None, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()

    run_id, _ = _mint_succeeded_run_with_export(db, tmp_path)

    # Tamper the stored recipe_hash
    tampered = "a" * 64  # wrong hash
    with db.transaction() as conn:
        conn.execute(
            "UPDATE short_runs SET recipe_hash=? WHERE id=?",
            (tampered, run_id),
        )

    result = recipe_from_trace(db, run_id)

    # Recipe is still available but verification fails
    assert result["available"] is True
    assert result["verified"] is False
    assert result["recipe"] is not None
    assert result["recipe_hash"] == tampered


def test_recipe_from_trace_tampered_hash_via_http(tmp_path: Path) -> None:
    """GET returns 200 with verified=False when hash is tampered."""
    client, db = _make_client_db(tmp_path)
    run_id, _ = _mint_succeeded_run_with_export(db, tmp_path)

    tampered = "b" * 64
    with db.transaction() as conn:
        conn.execute(
            "UPDATE short_runs SET recipe_hash=? WHERE id=?",
            (tampered, run_id),
        )

    resp = client.get(f"/short-runs/{run_id}/recipe")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is True
    assert body["verified"] is False
    assert body["recipe"] is not None


# ---------------------------------------------------------------------------
# 6. NO-WRITES assertion
# ---------------------------------------------------------------------------


def test_recipe_from_trace_no_writes(tmp_path: Path) -> None:
    """recipe_from_trace (direct + HTTP) must not mutate any table."""
    from laura.api.batch import recipe_from_trace

    client, db = _make_client_db(tmp_path)
    run_id, _ = _mint_succeeded_run_with_export(db, tmp_path)

    before = _row_counts(db)

    # Direct call
    recipe_from_trace(db, run_id)

    # HTTP call
    client.get(f"/short-runs/{run_id}/recipe")

    after = _row_counts(db)
    assert before == after, f"recipe_from_trace mutated DB: {before} -> {after}"
