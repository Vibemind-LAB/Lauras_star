"""Tests for POST /shorts/batch-plan — pure read-model, no side effects.

TDD: tests are written before the implementation. They FAIL until api/batch.py
and the router registration in main.py exist.

Test matrix (per the brief):
  1. 3-short batch in different states → correct per-short next_action + correct order.
  2. Per-short hash determinism: same (state, id) → same hash; different states → different.
  3. batch_hash order-sensitivity: same shorts re-ordered → same leaf hashes but different
     batch_hash.
  4. Unknown short_id → found=False, action None; OTHERS still resolve (no abort).
  5. NO-WRITES assertion: row counts unchanged before/after plan_batch.
  6. Empty short_ids → 422.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app

# ---------------------------------------------------------------------------
# Helpers (mirror the pattern from test_shorts_next_action.py)
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
        "timelines", "timeline_clips", "exports", "jobs", "short_runs", "asset_policies",
    ]
    counts: dict[str, int] = {}
    with db.connection() as conn:
        for tbl in tables:
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {tbl}").fetchone()
            counts[tbl] = int(row["n"])
    return counts


# ---------------------------------------------------------------------------
# 1. 3-state batch: each short resolves to the correct next_action
# ---------------------------------------------------------------------------

def test_three_state_batch(tmp_path: Path) -> None:
    """Batch over 3 shorts in different states returns correct per-short actions in input order."""
    client, db = _make_client(tmp_path)
    try:
        # Short A: proxy not ready → tool=None, PROXY_PENDING
        project_a, asset_a = _create_project_and_asset(client, db, "a.mov")

        # Short B: analysis done, no rough cut → tool=roughcut_from_shots
        project_b, asset_b = _create_project_and_asset(client, db, "b.mov")
        _add_proxy_file(db, asset_b["id"])
        _add_succeeded_analysis(db, asset_b["id"])

        # Short C: export done → tool=None, done
        project_c, asset_c = _create_project_and_asset(client, db, "c.mov")
        _add_proxy_file(db, asset_c["id"])
        _add_succeeded_analysis(db, asset_c["id"])
        tl_c = _add_rough_cut_with_clips(db, project_c["id"], asset_c["id"])
        _add_succeeded_export(db, project_c["id"], tl_c["id"], tmp_path)

        short_ids = [asset_a["id"], asset_b["id"], asset_c["id"]]
        resp = client.post("/shorts/batch-plan", json={"short_ids": short_ids})
        assert resp.status_code == 200, resp.text
        body = resp.json()

        plans = body["plans"]
        assert len(plans) == 3

        # Order must match input order
        assert plans[0]["short_id"] == asset_a["id"]
        assert plans[1]["short_id"] == asset_b["id"]
        assert plans[2]["short_id"] == asset_c["id"]

        # Short A: blocked PROXY_PENDING
        assert plans[0]["found"] is True
        assert plans[0]["action"]["tool"] is None
        assert "PROXY_PENDING" in plans[0]["action"]["blocked_by"]

        # Short B: roughcut_from_shots
        assert plans[1]["found"] is True
        assert plans[1]["action"]["tool"] == "roughcut_from_shots"
        assert plans[1]["action"]["args"] == {"asset_id": asset_b["id"]}

        # Short C: done
        assert plans[2]["found"] is True
        assert plans[2]["action"]["tool"] is None
        assert plans[2]["action"]["label_key"] == "next_action.done"

        # batch_hash must be present
        assert isinstance(body["batch_hash"], str)
        assert len(body["batch_hash"]) == 64  # sha256 hexdigest

    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 2. Per-short hash determinism
# ---------------------------------------------------------------------------

def test_per_short_hash_determinism(tmp_path: Path) -> None:
    """Same (short_id, state) → same hash on repeated calls; different states → different hashes."""
    client, db = _make_client(tmp_path)
    try:
        # Two shorts in different states
        project_a, asset_a = _create_project_and_asset(client, db, "det_a.mov")
        # asset_a: no proxy → PROXY_PENDING state

        project_b, asset_b = _create_project_and_asset(client, db, "det_b.mov")
        _add_proxy_file(db, asset_b["id"])
        _add_succeeded_analysis(db, asset_b["id"])
        # asset_b: roughcut_from_shots state

        def call() -> list[dict[str, Any]]:
            resp = client.post(
                "/shorts/batch-plan",
                json={"short_ids": [asset_a["id"], asset_b["id"]]},
            )
            assert resp.status_code == 200
            return resp.json()["plans"]

        plans1 = call()
        plans2 = call()

        # Determinism: same inputs produce same hashes
        assert plans1[0]["hash"] == plans2[0]["hash"]
        assert plans1[1]["hash"] == plans2[1]["hash"]

        # Different states → different per-short hashes
        assert plans1[0]["hash"] != plans1[1]["hash"]

    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 3. batch_hash order-sensitivity
# ---------------------------------------------------------------------------

def test_batch_hash_order_sensitivity(tmp_path: Path) -> None:
    """Reordering inputs changes batch_hash but NOT the individual per-short hashes."""
    client, db = _make_client(tmp_path)
    try:
        project_a, asset_a = _create_project_and_asset(client, db, "ord_a.mov")
        project_b, asset_b = _create_project_and_asset(client, db, "ord_b.mov")
        _add_proxy_file(db, asset_b["id"])

        resp_ab = client.post(
            "/shorts/batch-plan",
            json={"short_ids": [asset_a["id"], asset_b["id"]]},
        )
        resp_ba = client.post(
            "/shorts/batch-plan",
            json={"short_ids": [asset_b["id"], asset_a["id"]]},
        )
        assert resp_ab.status_code == 200
        assert resp_ba.status_code == 200

        body_ab = resp_ab.json()
        body_ba = resp_ba.json()

        # Different order → different batch_hash
        assert body_ab["batch_hash"] != body_ba["batch_hash"]

        # Per-short hashes for a given short_id are unchanged regardless of position
        hash_a_from_ab = next(p["hash"] for p in body_ab["plans"] if p["short_id"] == asset_a["id"])
        hash_a_from_ba = next(p["hash"] for p in body_ba["plans"] if p["short_id"] == asset_a["id"])
        assert hash_a_from_ab == hash_a_from_ba

        hash_b_from_ab = next(p["hash"] for p in body_ab["plans"] if p["short_id"] == asset_b["id"])
        hash_b_from_ba = next(p["hash"] for p in body_ba["plans"] if p["short_id"] == asset_b["id"])
        assert hash_b_from_ab == hash_b_from_ba

    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 4. Unknown short_id → found=False, others resolve
# ---------------------------------------------------------------------------

def test_unknown_short_id_does_not_abort_batch(tmp_path: Path) -> None:
    """An unknown short_id yields found=False with None action; other shorts still resolve."""
    client, db = _make_client(tmp_path)
    try:
        project_a, asset_a = _create_project_and_asset(client, db, "known.mov")
        _add_proxy_file(db, asset_a["id"])
        _add_succeeded_analysis(db, asset_a["id"])

        short_ids = ["nonexistent-id-xyz", asset_a["id"]]
        resp = client.post("/shorts/batch-plan", json={"short_ids": short_ids})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        plans = body["plans"]
        assert len(plans) == 2

        # First entry: unknown
        unknown_plan = plans[0]
        assert unknown_plan["short_id"] == "nonexistent-id-xyz"
        assert unknown_plan["found"] is False
        assert unknown_plan["action"] is None
        assert isinstance(unknown_plan["hash"], str) and len(unknown_plan["hash"]) == 64

        # Second entry: still resolved correctly
        known_plan = plans[1]
        assert known_plan["short_id"] == asset_a["id"]
        assert known_plan["found"] is True
        assert known_plan["action"]["tool"] == "roughcut_from_shots"

    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 5. NO-WRITES: plan_batch must not mutate the database
# ---------------------------------------------------------------------------

def test_plan_batch_performs_no_writes(tmp_path: Path) -> None:
    """Calling plan_batch (directly + via endpoint) must leave all row counts unchanged."""
    from laura.api.batch import plan_batch

    client, db = _make_client(tmp_path)
    try:
        project, asset = _create_project_and_asset(client, db)
        _add_proxy_file(db, asset["id"])
        _add_succeeded_analysis(db, asset["id"])

        before = _row_counts(db)

        # Direct resolver call
        plan_batch(db, [asset["id"]])

        # HTTP endpoint call
        client.post("/shorts/batch-plan", json={"short_ids": [asset["id"]]})

        after = _row_counts(db)
        assert before == after, f"plan_batch mutated DB: {before} -> {after}"

    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 6. Empty short_ids → 422
# ---------------------------------------------------------------------------

def test_empty_short_ids_returns_422(tmp_path: Path) -> None:
    """POST /shorts/batch-plan with empty short_ids must return 422."""
    client, db = _make_client(tmp_path)
    try:
        resp = client.post("/shorts/batch-plan", json={"short_ids": []})
        assert resp.status_code == 422, resp.text
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Extra: batch_hash is sha256 of canonical_json of ordered leaf hashes
# ---------------------------------------------------------------------------

def test_batch_hash_structure(tmp_path: Path) -> None:
    """Verify batch_hash == sha256(canonical_json([per-short hashes in order]))."""
    from laura.ledger.recipe import canonical_json

    client, db = _make_client(tmp_path)
    try:
        project, asset = _create_project_and_asset(client, db)
        _add_proxy_file(db, asset["id"])

        resp = client.post("/shorts/batch-plan", json={"short_ids": [asset["id"]]})
        assert resp.status_code == 200
        body = resp.json()

        leaf_hashes = [p["hash"] for p in body["plans"]]
        expected_batch_hash = hashlib.sha256(
            canonical_json(leaf_hashes).encode("utf-8")
        ).hexdigest()
        assert body["batch_hash"] == expected_batch_hash

    finally:
        client.__exit__(None, None, None)
