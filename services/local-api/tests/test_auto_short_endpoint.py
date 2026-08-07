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
    db: Database,
    project_id: str,
    name: str,
    *,
    segments: list[tuple[int, int, str]],
    media_dir: Path | None = None,
) -> str:
    """Asset + succeeded analysis run with *segments* (start_frame, end_frame, text) + a
    rough-cut timeline with one 1:1 clip over [0, 600) and two scenes [0,300)/[300,600).

    Mirrors test_discovery.py's / test_scout.py's helper of the same name.

    ``media_dir`` (when given) is where the asset's source file is actually CREATED — needed
    since ``create_project_auto_short`` now drops assets whose source has vanished before it
    builds anything (mirrors test_auto_overview_endpoint.py's helper of the same name). Leave
    it ``None`` (the default, unchanged for every pre-existing caller) to seed a dead source on
    purpose.
    """
    source = str(media_dir / name) if media_dir is not None else f"/tmp/{name}"
    if media_dir is not None:
        media_dir.mkdir(parents=True, exist_ok=True)
        (media_dir / name).write_bytes(b"")
    asset = repos.create_asset(
        db, project_id=project_id, type="video", display_name=name, source_path=source
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


def _seed_project_with_material(
    db: Database, *, media_dir: Path | None = None
) -> tuple[str, str]:
    """Project + one asset whose transcript matches the topic "mission" used throughout below.
    Returns ``(project_id, asset_id)``.

    ``media_dir`` (when given) is threaded straight through to :func:`_seed_asset_with_scenes`
    so the asset's source file really exists — every asset this system produces is imported
    from a real file, so a test exercising the "happy path" must seed one, now that
    ``create_project_auto_short`` drops assets whose source has vanished. Left ``None`` (the
    default) for callers that intentionally do not care (or want the dead-source shape).
    """
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset_id = _seed_asset_with_scenes(
        db, project["id"], "strong.mp4",
        segments=[(10, 60, "the agent farm plans the mission")],
        media_dir=media_dir,
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


def _no_job_rows(db: Database) -> bool:
    with db.connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    return bool(count == 0)


# --- (a) happy path -------------------------------------------------------------------------


def test_happy_path_creates_session_on_the_scouted_asset(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    monkeypatch.setattr(discovery, "get_index", lambda: None)  # force lexical, deterministic
    client, db = _app(tmp_path)
    project_id, asset_id = _seed_project_with_material(db, media_dir=tmp_path)
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
    project_id, asset_id = _seed_project_with_material(db, media_dir=tmp_path)
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


# --- (g) assets whose source file is gone (live finding 2026-08-01) -------------------------


def test_auto_short_drops_an_asset_whose_source_file_is_missing(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Live 2026-08-01: a repair restored two assets to the search index and they now rank
    first for agent topics — while their source files are gone (imported from a temp directory
    that was since cleaned; their proxies survive, but the renderer resolves the SOURCE).
    Mirrors test_auto_overview_endpoint.py's sibling test for the overview route: the scout
    must never see, let alone pick, an asset it cannot render."""
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    monkeypatch.setattr(discovery, "get_index", lambda: None)  # force lexical, deterministic
    client, db = _app(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    alive = _seed_asset_with_scenes(
        db, project["id"], "alive.mp4",
        segments=[(10, 60, "the agent farm plans the mission")],
        media_dir=tmp_path / "media",
    )
    # No media_dir -> the source path was never created: exactly the live shape.
    dead = _seed_asset_with_scenes(
        db, project["id"], "dead.mp4",
        segments=[(10, 60, "the mission handoff is executed")],
    )
    decision = _fixed_decision(alive)
    seen: dict[str, Any] = {}

    def _capture(*_args: Any, **kwargs: Any) -> ScoutDecision:
        seen["material"] = kwargs["material"]
        return decision

    monkeypatch.setattr("laura.api.short_creator.run_scout", _capture)

    r = client.post(f"/projects/{project['id']}/auto-short", json={"topic": "mission"}, headers=_H)

    assert r.status_code == 202, r.text
    body = r.json()
    # The scout never even saw the dead asset — it cannot pick what it cannot render.
    assert {e["asset_id"] for e in seen["material"]["ranking"]} == {alive}
    assert any("dead.mp4" in w and "source" in w for w in body["warnings"]), body["warnings"]
    # The response's own ranking is the same filtered one the scout chose from.
    assert {e["asset_id"] for e in body["ranking"]} == {alive}
    assert dead not in [e["asset_id"] for e in body["ranking"]]


def test_auto_short_422_when_every_source_is_missing(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Every matching asset's source file is gone: nothing can be produced, so nothing is —
    same corpse rule test_no_material_returns_422_before_any_session_is_created already proves
    for "nothing matched", now for "matched but unusable"."""
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    client, db = _app(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    _seed_asset_with_scenes(
        db, project["id"], "gone.mp4",
        segments=[(10, 60, "the agent farm plans the mission")],
    )

    def _boom(*_a: Any, **_kw: Any) -> ScoutDecision:
        raise AssertionError("the scout must never run when every source is missing")

    monkeypatch.setattr("laura.api.short_creator.run_scout", _boom)

    r = client.post(f"/projects/{project['id']}/auto-short", json={"topic": "mission"}, headers=_H)

    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["reason"] == "no usable material: every matching video's source file is missing"
    assert detail["missing_sources"] == ["gone.mp4"]
    assert _no_session_rows(db), "a 422 on missing sources must not leave a corpse session"
    assert _no_job_rows(db), "a 422 on missing sources must not leave a corpse job"
