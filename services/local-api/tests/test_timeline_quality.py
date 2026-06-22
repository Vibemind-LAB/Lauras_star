"""P1-T2a — timeline_quality migration (0026) + repos + GET endpoint.

TDD test suite: written first, should fail before implementation, green after.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from laura.analysis.eval_cut import CutEvalReport
from laura.analysis.eval_quality import RoughCutQuality
from laura.api import timelines as timelines_api
from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.main import create_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stub_report(exactness: float, n: int) -> CutEvalReport:
    return CutEvalReport(
        n_boundaries=n, mean_abs_offset=0.0, pct_exact=exactness, pct_within1=exactness,
        pct_within2=exactness, n_imprecise=0, exactness_score=exactness, worst=(), per_boundary=(),
    )


def _setup(tmp_path: Path) -> tuple[TestClient, SqliteDatabase, dict[str, Any], dict[str, Any]]:
    settings = Settings(workspace_root=tmp_path, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    client = TestClient(create_app(settings))
    client.__enter__()
    project = client.post(
        "/projects", json={"name": "p", "sequence_rate_num": 30, "sequence_rate_den": 1}
    ).json()
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a.mov", source_path="a.mov"
    )
    return client, db, project, asset


def _add_run_with_shots(db: SqliteDatabase, asset_id: str) -> str:
    run = repos.create_analysis_run(
        db, asset_id=asset_id, pipeline_version="1", config={"stages": {}}
    )
    repos.insert_shots(
        db, asset_id=asset_id, run_id=run["id"],
        shots=[
            {"src_in_frame": 0, "src_out_frame_exclusive": 50, "method": "test"},
            {"src_in_frame": 50, "src_out_frame_exclusive": 120, "method": "test"},
        ],
    )
    return str(run["id"])


# ---------------------------------------------------------------------------
# Migration tests
# ---------------------------------------------------------------------------

def test_migration_adds_timeline_quality_table(db: Database) -> None:
    with db.connection() as conn:
        cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(timeline_quality)").fetchall()
        }
    assert {"timeline_id", "status", "overall", "visual_exactness", "editorial_cleanliness",
             "n_cuts", "n_split_cuts", "created_at"} <= cols


# ---------------------------------------------------------------------------
# Repo unit tests
# ---------------------------------------------------------------------------

def test_set_and_get_quality_computed(db: Database) -> None:
    project = repos.create_project(
        db, name="P", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/ws"
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    tl_id = str(tl["id"])

    repos.set_timeline_quality(
        db, tl_id,
        status="computed",
        overall=0.8,
        visual_exactness=1.0,
        editorial_cleanliness=0.5,
        n_cuts=2,
        n_split_cuts=1,
    )
    row = repos.get_timeline_quality(db, tl_id)
    assert row is not None
    assert row["status"] == "computed"
    assert abs(row["overall"] - 0.8) < 1e-9
    assert abs(row["visual_exactness"] - 1.0) < 1e-9
    assert abs(row["editorial_cleanliness"] - 0.5) < 1e-9
    assert row["n_cuts"] == 2
    assert row["n_split_cuts"] == 1
    assert row["created_at"]


def test_set_quality_no_video(db: Database) -> None:
    project = repos.create_project(
        db, name="P", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/ws"
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    tl_id = str(tl["id"])

    repos.set_timeline_quality(db, tl_id, status="no_video")
    row = repos.get_timeline_quality(db, tl_id)
    assert row is not None
    assert row["status"] == "no_video"
    assert row["overall"] is None
    assert row["visual_exactness"] is None


def test_get_quality_none_when_not_set(db: Database) -> None:
    project = repos.create_project(
        db, name="P", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/ws"
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    assert repos.get_timeline_quality(db, str(tl["id"])) is None


def test_upsert_replaces_previous_row(db: Database) -> None:
    """Recompute must replace the old row — no duplicates (upsert)."""
    project = repos.create_project(
        db, name="P", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/ws"
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    tl_id = str(tl["id"])

    repos.set_timeline_quality(db, tl_id, status="no_video")
    repos.set_timeline_quality(
        db, tl_id, status="computed", overall=0.9, visual_exactness=0.9,
        editorial_cleanliness=0.9, n_cuts=3, n_split_cuts=0,
    )
    with db.connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM timeline_quality WHERE timeline_id=?", (tl_id,)
        ).fetchone()["c"]
    assert count == 1
    row = repos.get_timeline_quality(db, tl_id)
    assert row is not None and row["status"] == "computed"
    assert abs(row["overall"] - 0.9) < 1e-9


def test_fk_cascade_on_timeline_delete(db: Database) -> None:
    project = repos.create_project(
        db, name="P", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/ws"
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    tl_id = str(tl["id"])
    repos.set_timeline_quality(db, tl_id, status="no_video")
    with db.transaction() as conn:
        conn.execute("DELETE FROM timelines WHERE id=?", (tl_id,))
    assert repos.get_timeline_quality(db, tl_id) is None


# ---------------------------------------------------------------------------
# API: GET /timelines/{id}/quality
# ---------------------------------------------------------------------------

def test_get_quality_unknown_timeline_404(tmp_path: Path) -> None:
    client, _db, _project, _asset = _setup(tmp_path)
    try:
        r = client.get("/timelines/no-such-id/quality")
        assert r.status_code == 404
    finally:
        client.__exit__(None, None, None)


def test_get_quality_pending_when_no_row(tmp_path: Path) -> None:
    """Timeline exists but quality not yet computed → 200 status='pending'."""
    client, db, project, _asset = _setup(tmp_path)
    try:
        tl = client.post(
            f"/projects/{project['id']}/timelines",
            json={"name": "RC", "kind": "rough_cut"},
        ).json()
        r = client.get(f"/timelines/{tl['id']}/quality")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "pending"
        assert body["overall"] is None
        assert body["timeline_id"] == tl["id"]
    finally:
        client.__exit__(None, None, None)


def test_from_shots_persists_computed_quality(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """from-shots with computable quality persists status='computed' + scores; GET returns them."""
    client, db, project, asset = _setup(tmp_path)
    try:
        run = repos.create_analysis_run(
            db, asset_id=asset["id"], pipeline_version="q", config={"stages": {}}
        )
        repos.insert_shots(
            db, asset_id=asset["id"], run_id=run["id"],
            shots=[
                {"src_in_frame": 0, "src_out_frame_exclusive": 50, "method": "t"},
                {"src_in_frame": 50, "src_out_frame_exclusive": 120, "method": "t"},
            ],
        )
        repos.insert_segment_with_words(
            db, asset_id=asset["id"], run_id=run["id"], speaker_id=None,
            segment={"start_sample": 0, "end_sample": 1, "start_frame": 20,
                     "end_frame": 75, "text": "alpha omega"},
            words=[
                {"idx": 0, "start_sample": 0, "end_sample": 1,
                 "start_frame": 20, "end_frame": 40, "text": "alpha"},
                {"idx": 1, "start_sample": 1, "end_sample": 2,
                 "start_frame": 60, "end_frame": 75, "text": "omega"},
            ],
        )
        monkeypatch.setattr(timelines_api, "_resolve_video_path", lambda *a, **k: "fake.mp4")
        monkeypatch.setattr(timelines_api, "_detect_asset_silence", lambda *a, **k: [])
        monkeypatch.setattr(timelines_api, "_align_rows_editorial", lambda *a, **k: None)
        monkeypatch.setattr(timelines_api, "_plan_split_cuts", lambda *a, **k: [])
        monkeypatch.setattr(
            timelines_api,
            "evaluate_rough_cut",
            lambda *a, **k: RoughCutQuality(
                n_cuts=1, visual_exactness=1.0, editorial_clean=0.5, overall=0.8,
                visual=_stub_report(1.0, 1), editorial={"pct_clean": 0.5},
            ),
        )
        resp = client.post(
            f"/projects/{project['id']}/timelines/from-shots",
            json={"asset_id": asset["id"]},
        )
        assert resp.status_code == 201, resp.text
        tl_id = resp.json()["timeline"]["id"]

        r = client.get(f"/timelines/{tl_id}/quality")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "computed"
        assert body["timeline_id"] == tl_id
        assert body["overall"] == pytest.approx(0.8)
        assert body["visual_exactness"] == pytest.approx(1.0)
        assert body["editorial_cleanliness"] == pytest.approx(0.5)
        assert body["n_cuts"] == 1
        assert body["n_split_cuts"] == 0
        assert body["created_at"] is not None
    finally:
        client.__exit__(None, None, None)


def test_from_shots_persists_no_video_when_no_readable_video(tmp_path: Path) -> None:
    """from-shots without a readable video persists status='no_video'.

    GET returns no_video + null scores.
    """
    client, db, project, asset = _setup(tmp_path)
    try:
        _add_run_with_shots(db, asset["id"])
        resp = client.post(
            f"/projects/{project['id']}/timelines/from-shots",
            json={"asset_id": asset["id"]},
        )
        assert resp.status_code == 201, resp.text
        # Inline quality field is None (existing behaviour)
        assert resp.json()["quality"] is None

        tl_id = resp.json()["timeline"]["id"]
        r = client.get(f"/timelines/{tl_id}/quality")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "no_video"
        assert body["overall"] is None
        assert body["visual_exactness"] is None
        assert body["editorial_cleanliness"] is None
        assert body["n_cuts"] is None
        assert body["n_split_cuts"] is None
    finally:
        client.__exit__(None, None, None)
