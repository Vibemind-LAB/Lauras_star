"""P7-T3 — short_runs ledger wiring: mint at render-reel enqueue, update on finish.

TDD suite (written before implementation — expected to fail until wiring is in place):

1. render-reel enqueue mints a short_run with status=queued; export options carry short_run_id.
2. record_short_run_result (unit): success path updates status + persists trace.
3. record_short_run_result (unit): failure path updates status + persists trace.
4. record_short_run_result (unit): absent short_run_id → no-op (no crash).
5. record_short_run_result (unit): nonexistent run_id → no crash (update_run returns None).
6. render-reel on timeline with no created_from → still mints (input_sha256=None).
7. mint failure does not break enqueue (monkeypatched store raises).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

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
    # Set sha256 directly so input_sha256 is non-None in the mint
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


# ---------------------------------------------------------------------------
# 1. render-reel enqueue mints a short_run (queued) and stores short_run_id in options
# ---------------------------------------------------------------------------


def test_render_reel_mints_queued_short_run(tmp_path: Path) -> None:
    """POST /timelines/{id}/render-reel must mint a short_run with status=queued."""
    client, db = _make_client_db(tmp_path)
    pid = _project(client)
    asset_id, tl_id = _asset_timeline(client, db, pid)

    resp = client.post(f"/timelines/{tl_id}/render-reel", json={"hook_text": "Go!"})
    assert resp.status_code == 202, resp.text

    export_id = resp.json()["export_id"]
    exp = repos.get_export(db, export_id)
    assert exp is not None

    short_run_id = exp["options"].get("short_run_id")
    assert short_run_id is not None, "options must carry short_run_id after mint"

    store = get_ledger_store(db)
    run = store.get_run(short_run_id)
    assert run is not None, f"short_run {short_run_id} not found in ledger"
    assert run["status"] == "queued"


def test_render_reel_short_run_id_not_in_recipe(tmp_path: Path) -> None:
    """The recipe used to compute short_id must NOT include short_run_id itself
    (circular id). Verify: mint two reels with identical options → same short_id."""
    client, db = _make_client_db(tmp_path)
    pid = _project(client)
    _, tl_id = _asset_timeline(client, db, pid)

    r1 = client.post(f"/timelines/{tl_id}/render-reel", json={"hook_text": "X"})
    r2 = client.post(f"/timelines/{tl_id}/render-reel", json={"hook_text": "X"})
    assert r1.status_code == r2.status_code == 202

    store = get_ledger_store(db)
    id1 = repos.get_export(db, r1.json()["export_id"])["options"]["short_run_id"]
    id2 = repos.get_export(db, r2.json()["export_id"])["options"]["short_run_id"]

    run1 = store.get_run(id1)
    run2 = store.get_run(id2)
    assert run1 is not None and run2 is not None
    # Same inputs → same short_id (content-addressed)
    assert run1["short_id"] == run2["short_id"]
    # But two distinct run rows (two enqueue calls)
    assert run1["id"] != run2["id"]


# ---------------------------------------------------------------------------
# 2 & 3. record_short_run_result — success and failure paths
# ---------------------------------------------------------------------------


def test_record_short_run_result_success(tmp_path: Path) -> None:
    """record_short_run_result with status=succeeded updates the run."""
    from laura.render.handlers import record_short_run_result

    settings = Settings(workspace_root=tmp_path, token=None, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()

    store = get_ledger_store(db)
    run = store.record_run(short_id="sid-1", pipeline_version="2")
    run_id = run["id"]

    options: dict[str, object] = {"short_run_id": run_id, "vertical": True}
    trace = {"export_id": "exp-abc", "output": "/tmp/out.mp4"}
    record_short_run_result(db, options, status="succeeded", trace=trace)

    updated = store.get_run(run_id)
    assert updated is not None
    assert updated["status"] == "succeeded"
    assert updated["trace_json"] is not None
    decoded = json.loads(updated["trace_json"])
    assert decoded["export_id"] == "exp-abc"
    assert decoded["output"] == "/tmp/out.mp4"


def test_record_short_run_result_failed(tmp_path: Path) -> None:
    """record_short_run_result with status=failed stores error in trace."""
    from laura.render.handlers import record_short_run_result

    settings = Settings(workspace_root=tmp_path, token=None, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()

    store = get_ledger_store(db)
    run = store.record_run(short_id="sid-2", pipeline_version="2")
    run_id = run["id"]

    options: dict[str, object] = {"short_run_id": run_id}
    record_short_run_result(db, options, status="failed", trace={"error": "ffmpeg died"})

    updated = store.get_run(run_id)
    assert updated is not None
    assert updated["status"] == "failed"
    assert updated["trace_json"] is not None
    assert "ffmpeg died" in json.loads(updated["trace_json"])["error"]


# ---------------------------------------------------------------------------
# 4. record_short_run_result — absent short_run_id is a no-op (no crash)
# ---------------------------------------------------------------------------


def test_record_short_run_result_absent_id_noop(tmp_path: Path) -> None:
    """If options has no short_run_id, record_short_run_result must return silently."""
    from laura.render.handlers import record_short_run_result

    settings = Settings(workspace_root=tmp_path, token=None, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()

    # Must not raise
    record_short_run_result(db, {}, status="succeeded", trace=None)
    record_short_run_result(db, {"hook_text": "H"}, status="failed", trace={"error": "x"})


# ---------------------------------------------------------------------------
# 5. record_short_run_result — nonexistent run_id → no crash
# ---------------------------------------------------------------------------


def test_record_short_run_result_nonexistent_run(tmp_path: Path) -> None:
    """update_run for an unknown id returns None; must not raise."""
    from laura.render.handlers import record_short_run_result

    settings = Settings(workspace_root=tmp_path, token=None, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()

    options: dict[str, object] = {"short_run_id": "does-not-exist"}
    # Must not raise even though the run_id is unknown
    record_short_run_result(db, options, status="succeeded", trace=None)


# ---------------------------------------------------------------------------
# 6. render-reel on timeline with no created_from → still mints (input_sha256=None)
# ---------------------------------------------------------------------------


def test_render_reel_no_created_from_mints_with_null_sha(tmp_path: Path) -> None:
    """Timeline without created_from: mint still succeeds with input_sha256=None."""
    client, db = _make_client_db(tmp_path)
    pid = _project(client)
    # No created_from on this timeline
    tl = repos.create_timeline(db, project_id=pid, name="cut", kind="rough_cut")

    resp = client.post(f"/timelines/{tl['id']}/render-reel", json={})
    assert resp.status_code == 202, resp.text

    exp = repos.get_export(db, resp.json()["export_id"])
    assert exp is not None
    short_run_id = exp["options"].get("short_run_id")
    assert short_run_id is not None, "must still mint when created_from is absent"

    store = get_ledger_store(db)
    run = store.get_run(short_run_id)
    assert run is not None
    assert run["input_sha256"] is None


# ---------------------------------------------------------------------------
# 7. mint failure does NOT break enqueue
# ---------------------------------------------------------------------------


def test_render_reel_mint_failure_does_not_break_enqueue(tmp_path: Path) -> None:
    """If mint_short_run raises, the render job must still be enqueued."""
    client, db = _make_client_db(tmp_path)
    pid = _project(client)
    _, tl_id = _asset_timeline(client, db, pid)

    with patch("laura.api.reels.mint_short_run", side_effect=RuntimeError("ledger down")):
        resp = client.post(f"/timelines/{tl_id}/render-reel", json={})

    # Enqueue must succeed even though mint raised
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert "export_id" in body
    assert "job_id" in body

    # No short_run_id in options (mint failed, gracefully skipped)
    exp = repos.get_export(db, body["export_id"])
    assert exp is not None
    assert "short_run_id" not in exp["options"]


# ---------------------------------------------------------------------------
# C1 regression — quality state must NOT contaminate the recipe hash
# ---------------------------------------------------------------------------

def test_same_short_id_across_quality_states(tmp_path: Path) -> None:
    """Identical render params must yield the same short_id regardless of quality_status.

    Regression for C1: quality_status/quality_verified are runtime observations and
    must be excluded from the recipe hash.
    """
    client, db = _make_client_db(tmp_path)
    pid = _project(client)
    _, tl_id = _asset_timeline(client, db, pid)

    # First call: no timeline_quality row → quality_status='pending', quality_verified=False
    r1 = client.post(f"/timelines/{tl_id}/render-reel", json={"hook_text": "Hello"})
    assert r1.status_code == 202, r1.text

    # Insert a computed quality row for the timeline
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO timeline_quality (timeline_id, status, overall, created_at) "
            "VALUES (?, 'computed', 0.95, datetime('now'))",
            (tl_id,),
        )

    # Second call: quality_status='computed', quality_verified=True
    r2 = client.post(f"/timelines/{tl_id}/render-reel", json={"hook_text": "Hello"})
    assert r2.status_code == 202, r2.text

    store = get_ledger_store(db)
    id1 = repos.get_export(db, r1.json()["export_id"])["options"].get("short_run_id")
    id2 = repos.get_export(db, r2.json()["export_id"])["options"].get("short_run_id")
    assert id1 is not None and id2 is not None

    run1 = store.get_run(id1)
    run2 = store.get_run(id2)
    assert run1 is not None and run2 is not None

    # CRITICAL: same user params + same asset → same short_id regardless of quality state
    assert run1["short_id"] == run2["short_id"], (
        f"short_id changed across quality states: {run1['short_id']} vs {run2['short_id']}"
    )
    assert run1["recipe_hash"] == run2["recipe_hash"], (
        "recipe_hash changed across quality states"
    )
    # But two distinct run rows
    assert run1["id"] != run2["id"]


def test_recipe_from_trace_excludes_quality_keys(tmp_path: Path) -> None:
    """recipe_from_trace must reconstruct a recipe that matches stored recipe_hash.

    Regression for C1: if quality keys contaminate the recipe, verified=False even
    for legitimate runs.
    """
    from laura.api.batch import recipe_from_trace
    from laura.ledger import mint_short_run
    from laura.ledger.recipe import RECIPE_EXCLUDED_KEYS, compute_recipe_hash

    settings = Settings(workspace_root=tmp_path, token=None, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()

    # Simulate what render_reel does: build recipe excluding quality keys
    options: dict[str, object] = {
        "vertical": False,
        "hook_text": "Test",
        "quality_status": "computed",
        "quality_verified": True,
    }
    recipe = {k: v for k, v in options.items() if k not in RECIPE_EXCLUDED_KEYS}

    # Mint using the clean recipe
    store = get_ledger_store(db)
    run = mint_short_run(store, recipe=recipe, input_sha256=None)
    run_id = run["id"]

    # Store the export with ALL options (including quality keys + short_run_id)
    options_with_run_id = dict(options)
    options_with_run_id["short_run_id"] = run_id
    import json as _json
    exp = repos.create_export(
        db, project_id="p1", timeline_id="tl1", format="mp4", options=options_with_run_id
    )
    store.update_run(run_id, status="succeeded", trace_json=_json.dumps({"export_id": exp["id"]}))

    result = recipe_from_trace(db, run_id)

    assert result["available"] is True
    # CRITICAL: verified must be True — recipe reconstruction must exclude quality keys
    assert result["verified"] is True, (
        f"verified=False: computed={compute_recipe_hash(result['recipe'] or {})!r} "
        f"stored={result['recipe_hash']!r}"
    )
    # Recipe must not contain excluded keys
    for key in RECIPE_EXCLUDED_KEYS:
        assert key not in (result["recipe"] or {}), f"recipe contains excluded key: {key}"
