"""POST /projects/{project_id}/auto-overview — topic in, a new sequence plus a render out
(spec 2026-07-31-auto-overview-design.md §1).

Mirrors test_auto_short_endpoint.py: app factory + token header, a real DB seed, and the
scout monkeypatched at ``laura.api.short_creator.run_overview_scout`` — imported at module
level exactly so this works — so no test ever touches an LLM.
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
from laura.short_creator.overview_scout import OverviewDecision
from laura.short_creator.overview_windows import Candidate

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
    """Asset + succeeded analysis run with *segments* + a rough cut over [0,600) with two
    scenes [0,300)/[300,600). Mirrors test_auto_short_endpoint.py's helper of the same name."""
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


def _seed_two_assets(db: Database) -> tuple[str, str, str]:
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    a = _seed_asset_with_scenes(
        db, project["id"], "a.mp4", segments=[(10, 200, "the agent farm plans the mission")]
    )
    b = _seed_asset_with_scenes(
        db, project["id"], "b.mp4", segments=[(20, 220, "the mission handoff is executed")]
    )
    return str(project["id"]), a, b


def _decision(a: str, b: str) -> OverviewDecision:
    return {
        "clips": [
            Candidate(a, "a.mp4", 1, 0, 300, "the agent farm plans the mission"),
            Candidate(b, "b.mp4", 1, 0, 240, "the mission handoff is executed"),
        ],
        "rationale": "one video sets it up, the other shows it running",
        "fallback": False,
    }


def _counts(db: Database) -> tuple[int, int]:
    with db.connection() as conn:
        timelines = conn.execute("SELECT COUNT(*) FROM timelines").fetchone()[0]
        scenes = conn.execute("SELECT COUNT(*) FROM scenes").fetchone()[0]
    return int(timelines), int(scenes)


def test_happy_path_builds_a_sequence_and_enqueues_a_render(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    monkeypatch.setattr(discovery, "get_index", lambda: None)  # force lexical, deterministic
    client, db = _app(tmp_path)
    project_id, a, b = _seed_two_assets(db)
    monkeypatch.setattr(
        "laura.api.short_creator.run_overview_scout", lambda *_a, **_kw: _decision(a, b)
    )
    # get_or_create_project_sequence returns the OLDEST sequence-kind timeline (the user's own
    # Zusammenfügen assembly) — establish it BEFORE the call so the assertion below actually
    # distinguishes "the project's own sequence" from the new one auto-overview builds. Without
    # this, a fresh project has no sequence yet, and the new one auto-overview creates would be
    # the oldest by definition, making the two ids coincide vacuously (mirrors
    # test_overview_build.py::test_the_project_sequence_is_left_alone's setup).
    existing_sequence = repos.get_or_create_project_sequence(db, project_id)

    r = client.post(
        f"/projects/{project_id}/auto-overview", json={"topic": "mission"}, headers=_H
    )

    assert r.status_code == 202, r.text
    body = r.json()
    assert body["fallback"] is False
    assert body["rationale"].startswith("one video sets it up")
    assert [c["asset_id"] for c in body["clips"]] == [a, b]
    assert body["ranking"], "the ranking must be surfaced, not just the winner"
    assert isinstance(body["warnings"], list)

    # The sequence exists, references two scenes, and is NOT the project sequence.
    sequence = repos.get_timeline(db, body["sequence_id"])
    assert sequence is not None and sequence["kind"] == "sequence"
    assert len(repos.list_sequence_items(db, body["sequence_id"])) == 2
    assert body["sequence_id"] != existing_sequence["id"]
    assert repos.get_or_create_project_sequence(db, project_id)["id"] == existing_sequence["id"]

    # The source timeline carries the protective kind.
    source = repos.get_timeline(db, body["source_timeline_id"])
    assert source is not None and source["kind"] == "overview"

    # A render job was enqueued for THIS sequence.
    job = repos.get_job(db, body["job_id"])
    assert job is not None and job["kind"] == "export.render"
    payload = json.loads(job["payload_json"])
    assert payload["export_id"] == body["export_id"]
    export = repos.get_export(db, body["export_id"])
    assert export is not None
    assert export["timeline_id"] == body["sequence_id"]


def test_unknown_project_is_404(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    client, _db = _app(tmp_path)
    r = client.post("/projects/nope/auto-overview", json={"topic": "mission"}, headers=_H)
    assert r.status_code == 404


def test_missing_extra_is_503(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: False)
    client, db = _app(tmp_path)
    project_id, _a, _b = _seed_two_assets(db)
    r = client.post(
        f"/projects/{project_id}/auto-overview", json={"topic": "mission"}, headers=_H
    )
    assert r.status_code == 503
    assert "autoshort" in r.json()["detail"]


def test_no_material_is_422_and_writes_nothing(tmp_path: Path, monkeypatch: Any) -> None:
    """The corpse rule: a topic nothing matches leaves no timeline and no scene behind."""
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    client, db = _app(tmp_path)
    project_id, _a, _b = _seed_two_assets(db)
    before = _counts(db)

    r = client.post(
        f"/projects/{project_id}/auto-overview",
        json={"topic": "quantum chromodynamics"},
        headers=_H,
    )

    assert r.status_code == 422
    assert r.json()["detail"]["reason"] == "no material found for topic"
    assert _counts(db) == before


def test_hits_too_short_for_a_window_are_422_not_an_empty_sequence(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Material exists but every window falls under the 4s floor -> 422, still no writes."""
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    client, db = _app(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    # A 2-frame segment padded by 1s each side is 62 frames ~ 2.1s — under the floor.
    _seed_asset_with_scenes(
        db, project["id"], "tiny.mp4", segments=[(10, 12, "the mission")]
    )
    before = _counts(db)

    r = client.post(
        f"/projects/{project['id']}/auto-overview", json={"topic": "mission"}, headers=_H
    )

    assert r.status_code == 422
    assert r.json()["detail"]["reason"] == "no usable windows for topic"
    assert _counts(db) == before
