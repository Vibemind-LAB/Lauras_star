"""POST /projects/{project_id}/auto-short — topic in, scouted v2 production session out.

Task 3 of the auto-short arc (spec 2026-07-21-auto-short-design.md §3): wires Task 1's
``discovery.search_material`` and Task 2's ``scout.run_scout`` into a project-scoped endpoint
that picks the asset ITSELF and starts a normal v2 production session on it — exactly the same
session-creation path as ``POST /assets/{asset_id}/production``. Mirrors
test_production_api.py's app-factory + token-header pattern and test_discovery.py's DB seed
(duplicated here per this repo's self-contained-test-file convention). ``run_scout`` is
monkeypatched at ``laura.api.short_creator.run_scout`` — imported at module level exactly so
this works — so these tests never touch a real LLM; ``search_material`` runs for real against a
seeded DB.

Distinct from ``POST /assets/{asset_id}/auto-short`` (the v1 per-asset NL-agent endpoint, left
untouched — see test_short_creator_api.py for that one).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.main import create_app
from laura.short_creator import discovery
from laura.short_creator.scout import ScoutDecision

FPS = 30
_TOKEN = "test-token"
_H = {"X-Laura-Token": _TOKEN}


def _app(tmp_path: Path) -> tuple[TestClient, SqliteDatabase]:
    settings = Settings(workspace_root=tmp_path / "ws", token=_TOKEN, start_runner=False)
    app = create_app(settings)
    db: SqliteDatabase = app.state.db
    return TestClient(app), db


def _seed_asset_with_scenes(
    db: Database, project_id: str, name: str, *, segments: list[tuple[int, int, str]]
) -> str:
    """Asset + succeeded analysis run with *segments* (start_frame, end_frame, text) + a
    rough-cut timeline with one 1:1 clip over [0, 600) and two scenes [0,300)/[300,600).

    Mirrors test_discovery.py's / test_scout.py's helper of the same name.
    """
    asset = repos.create_asset(
        db, project_id=project_id, type="video", display_name=name, source_path=f"/tmp/{name}"
    )
    run = repos.create_analysis_run(db, asset_id=asset["id"], pipeline_version="t", config={})
    repos.start_analysis_run(db, run["id"])
    for start, end, text in segments:
        repos.insert_segment_with_words(
            db,
            asset_id=asset["id"],
            run_id=run["id"],
            speaker_id=None,
            segment={
                "start_sample": start * 1600,
                "end_sample": end * 1600,
                "start_frame": start,
                "end_frame": end,
                "text": text,
                "confidence": 1.0,
            },
            words=[],
        )
    repos.finish_analysis_run(db, run["id"], status="succeeded", diagnostics={})
    timeline = repos.create_timeline(
        db, project_id=project_id, name="Rough Cut", kind="rough_cut", created_from=asset["id"]
    )
    repos.add_timeline_clip(
        db, timeline_id=timeline["id"], asset_id=asset["id"],
        src_in_frame=0, src_out_frame_exclusive=600,
        seq_in_frame=0, seq_out_frame_exclusive=600,
    )
    repos.replace_scenes(db, project_id, timeline["id"], [(0, 300), (300, 600)])
    return str(asset["id"])


def _seed_project_with_material(db: Database) -> tuple[str, str]:
    """Project + one asset whose transcript matches the topic "mission" used throughout below.
    Returns ``(project_id, asset_id)``."""
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset_id = _seed_asset_with_scenes(
        db, project["id"], "strong.mp4",
        segments=[(10, 60, "the agent farm plans the mission")],
    )
    return str(project["id"]), asset_id


def _fixed_decision(asset_id: str, *, fallback: bool = False) -> ScoutDecision:
    return {
        "asset_id": asset_id,
        "scene_numbers": [1],
        "rationale": "covers the mission plan directly",
        "fallback": fallback,
    }


def _no_session_rows(db: Database) -> bool:
    with db.connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM production_sessions").fetchone()[0]
    return bool(count == 0)


# --- (a) happy path -------------------------------------------------------------------------


def test_happy_path_creates_session_on_the_scouted_asset(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    monkeypatch.setattr(discovery, "get_index", lambda: None)  # force lexical, deterministic
    client, db = _app(tmp_path)
    project_id, asset_id = _seed_project_with_material(db)
    decision = _fixed_decision(asset_id)
    monkeypatch.setattr("laura.api.short_creator.run_scout", lambda *_a, **_kw: decision)

    r = client.post(f"/projects/{project_id}/auto-short", json={"topic": "mission"}, headers=_H)

    assert r.status_code == 202, r.text
    body = r.json()
    assert body["asset_id"] == asset_id
    assert body["scene_numbers"] == [1]
    assert body["rationale"] == "covers the mission plan directly"
    assert body["fallback"] is False
    assert body["ranking"], "the ranking must be surfaced, not just the winner"
    assert isinstance(body["warnings"], list)

    # Session row exists on the SCOUTED asset (not just any asset).
    session = repos.get_production_session(db, body["session_id"])
    assert session is not None
    assert session["asset_id"] == asset_id

    # The enqueued job's task carries topic + scene focus + rationale.
    job = repos.get_job(db, body["job_id"])
    assert job is not None
    assert job["kind"] == "production.run"
    payload = json.loads(job["payload_json"])
    assert payload["asset_id"] == asset_id
    assert payload["session_id"] == body["session_id"]
    assert "mission" in payload["task"]
    assert "Focus on scenes" in payload["task"]
    assert "covers the mission plan directly" in payload["task"]


# --- (b) fallback decision flows through ----------------------------------------------------


def test_fallback_decision_is_visible_in_the_response(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    client, db = _app(tmp_path)
    project_id, asset_id = _seed_project_with_material(db)
    decision = _fixed_decision(asset_id, fallback=True)
    monkeypatch.setattr("laura.api.short_creator.run_scout", lambda *_a, **_kw: decision)

    r = client.post(f"/projects/{project_id}/auto-short", json={"topic": "mission"}, headers=_H)

    assert r.status_code == 202, r.text
    assert r.json()["fallback"] is True


# --- (c) no material -> 422 before any session --------------------------------------------


def test_no_material_returns_422_before_any_session_is_created(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    client, db = _app(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    # An asset exists, but its transcript has nothing to do with the topic searched for.
    _seed_asset_with_scenes(db, project["id"], "a.mp4", segments=[(10, 60, "hello world")])

    def _boom(*_a: Any, **_kw: Any) -> ScoutDecision:
        raise AssertionError("the scout must never run when there is no material")

    monkeypatch.setattr("laura.api.short_creator.run_scout", _boom)

    r = client.post(
        f"/projects/{project['id']}/auto-short",
        json={"topic": "quantum chromodynamics"},
        headers=_H,
    )

    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["reason"] == "no material found for topic"
    assert detail["skipped"] == []
    assert detail["source"] == "lexical"
    assert _no_session_rows(db), "a 422 on no material must not leave a corpse session"


# --- (d) unknown project -> 404 -------------------------------------------------------------


def test_unknown_project_404(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    client, _db = _app(tmp_path)

    r = client.post(
        "/projects/does-not-exist/auto-short", json={"topic": "mission"}, headers=_H
    )

    assert r.status_code == 404, r.text


# --- (f) preflight 503 before scout or session ----------------------------------------------


def test_preflight_503_refuses_before_scout_or_session(tmp_path: Path, monkeypatch: Any) -> None:
    """Mirrors test_production_api.py's preflight test: openai-compat with no key must never
    reach the scout or create a session."""
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    monkeypatch.setenv("LAURA_AGENT_PROVIDER", "openai-compat")
    monkeypatch.setenv("LAURA_AGENT_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.delenv("LAURA_AGENT_API_KEY", raising=False)
    client, db = _app(tmp_path)
    project_id, _asset_id = _seed_project_with_material(db)

    def _boom(*_a: Any, **_kw: Any) -> ScoutDecision:
        raise AssertionError("the scout must never run when the agent config is unusable")

    monkeypatch.setattr("laura.api.short_creator.run_scout", _boom)

    r = client.post(f"/projects/{project_id}/auto-short", json={"topic": "mission"}, headers=_H)

    assert r.status_code == 503, r.text
    assert "LAURA_AGENT_API_KEY" in r.text
    assert _no_session_rows(db), "the refusal must be before the board and the job"
