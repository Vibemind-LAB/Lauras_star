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

from laura.api.short_creator import _overview_fps
from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.main import create_app
from laura.short_creator import discovery
from laura.short_creator.overview_scout import OverviewDecision
from laura.short_creator.overview_windows import Candidate, trim_to_target

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
    """Asset + succeeded analysis run with *segments* + a rough cut over [0,600) with two
    scenes [0,300)/[300,600). Mirrors test_auto_short_endpoint.py's helper of the same name.

    ``media_dir`` (when given) is where the asset's source file is actually CREATED. The
    endpoint drops assets whose source has vanished before it builds anything, so a seed with
    a path that never existed is no longer a neutral stand-in — it seeds an unusable asset.
    Leave it None to seed exactly that: a dead source.
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


def _seed_two_assets(db: Database, media_dir: Path) -> tuple[str, str, str]:
    """Two assets whose source files really exist under *media_dir* — the normal case."""
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    a = _seed_asset_with_scenes(
        db, project["id"], "a.mp4", segments=[(10, 200, "the agent farm plans the mission")],
        media_dir=media_dir,
    )
    b = _seed_asset_with_scenes(
        db, project["id"], "b.mp4", segments=[(20, 220, "the mission handoff is executed")],
        media_dir=media_dir,
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
    project_id, a, b = _seed_two_assets(db, tmp_path / "media")
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
    project_id, _a, _b = _seed_two_assets(db, tmp_path / "media")
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
    project_id, _a, _b = _seed_two_assets(db, tmp_path / "media")
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
        db, project["id"], "tiny.mp4", segments=[(10, 12, "the mission")],
        media_dir=tmp_path / "media",
    )
    before = _counts(db)

    r = client.post(
        f"/projects/{project['id']}/auto-overview", json={"topic": "mission"}, headers=_H
    )

    assert r.status_code == 422
    assert r.json()["detail"]["reason"] == "no usable windows for topic"
    assert _counts(db) == before


def _decision_for_target(a: str, b: str, target_seconds: int) -> OverviewDecision:
    """A scout answer whose ``clips`` went through the REAL ``trim_to_target`` — reproduces
    what ``run_overview_scout`` itself does at the end of a real run, so a test that
    monkeypatches the scout still exercises the exact emptying behavior the route must guard
    against (10s + 8s candidates against a tight target_seconds*1.2 budget)."""
    candidates = [
        Candidate(a, "a.mp4", 1, 0, 300, "the agent farm plans the mission"),
        Candidate(b, "b.mp4", 1, 0, 240, "the mission handoff is executed"),
    ]
    fps_by_asset = {a: (FPS, 1), b: (FPS, 1)}
    return {
        "clips": trim_to_target(
            candidates, target_seconds=target_seconds, fps_by_asset=fps_by_asset
        ),
        "rationale": "one video sets it up, the other shows it running",
        "fallback": False,
    }


def test_target_shorter_than_every_clip_is_422_not_500(tmp_path: Path, monkeypatch: Any) -> None:
    """A legitimate request (target_seconds is in the model's 1..1800 range) whose target is
    below every candidate's length must 422, not crash into build_overview's empty-clips
    ValueError. Real material matches; the scout's post-trim selection is what empties."""
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    client, db = _app(tmp_path)
    project_id, a, b = _seed_two_assets(db, tmp_path / "media")
    monkeypatch.setattr(
        "laura.api.short_creator.run_overview_scout",
        lambda *_a, **_kw: _decision_for_target(a, b, 2),
    )
    before = _counts(db)

    r = client.post(
        f"/projects/{project_id}/auto-overview",
        json={"topic": "mission", "target_seconds": 2},
        headers=_H,
    )

    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "reason" in detail
    assert _counts(db) == before


def test_single_source_is_warned_but_still_succeeds(tmp_path: Path, monkeypatch: Any) -> None:
    """Only one asset matches the topic — the scout can only ever hand back clips from that
    one source. That is honest, not an error: the request still succeeds (202), and the
    single-source warning the route appends must show up in the response."""
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    client, db = _app(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    a = _seed_asset_with_scenes(
        db, project["id"], "a.mp4", segments=[(10, 200, "the agent farm plans the mission")],
        media_dir=tmp_path / "media",
    )
    monkeypatch.setattr(
        "laura.api.short_creator.run_overview_scout",
        lambda *_a, **_kw: {
            "clips": [Candidate(a, "a.mp4", 1, 0, 300, "the agent farm plans the mission")],
            "rationale": "only one video matched the topic",
            "fallback": True,
        },
    )

    r = client.post(
        f"/projects/{project['id']}/auto-overview", json={"topic": "mission"}, headers=_H
    )

    assert r.status_code == 202, r.text
    body = r.json()
    assert "overview covers a single source: only a.mp4 matched the topic" in body["warnings"]


def test_trim_to_one_clip_warns_about_target_length_not_single_source(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Both assets have real material — the ranking and the endpoint's own `candidates`
    span two videos — but a tight `target_seconds` trims the (validated, two-source)
    selection down to one clip via `trim_to_target`. The warning must name the target
    length as the cause, not claim only one video matched the topic: that claim would be a
    lie the response's own `ranking` visibly contradicts (it still lists both videos)."""
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    client, db = _app(tmp_path)
    project_id, a, b = _seed_two_assets(db, tmp_path / "media")
    monkeypatch.setattr(
        "laura.api.short_creator.run_overview_scout",
        lambda *_a, **_kw: _decision_for_target(a, b, 10),
    )

    r = client.post(
        f"/projects/{project_id}/auto-overview",
        json={"topic": "mission", "target_seconds": 10},
        headers=_H,
    )

    assert r.status_code == 202, r.text
    body = r.json()
    # Sanity: the trim really did leave exactly one clip, from one asset, while both videos
    # are still visible in the ranking — otherwise this test wouldn't exercise case (b).
    assert len({c["asset_id"] for c in body["clips"]}) == 1
    assert len({e["asset_id"] for e in body["ranking"]}) == 2

    warning = next(w for w in body["warnings"] if "single source" in w)
    assert "10" in warning, warning
    assert "matched the topic" not in warning, warning


def test_overview_fps_falls_back_to_the_project_rate(tmp_path: Path) -> None:
    """An asset whose probe left rate_num/rate_den NULL must fall back to the PROJECT's own
    rate, per _overview_fps's own docstring — not a hardcoded 25fps. The projects table
    column is `sequence_rate_num`/`sequence_rate_den` (db/migrations/0001_init.sql), not
    `rate_num`/`rate_den` (that name belongs to media_assets); reading the wrong column left
    the fallback dead and every rate-less asset silently defaulted to 25fps."""
    _client, db = _app(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a", source_path="/tmp/a"
    )
    assert asset["rate_num"] is None, "sanity: a fresh asset has no probed rate yet"

    fps_by_asset = _overview_fps(db, project["id"], [{"asset_id": asset["id"]}])

    assert fps_by_asset[asset["id"]] == (30, 1)


# --- assets whose source file is gone (live finding 2026-07-31) --------------------------------


def test_asset_with_a_vanished_source_is_dropped_before_anything_is_built(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Live 2026-07-31: two videos were picked with full confidence, the sequence was built, the
    render job started — and only ffmpeg found out their source files were gone (they lived in a
    cleaned temp dir). `online` stays True forever in that case, so nothing upstream noticed.
    The overview must drop such an asset BEFORE it builds, and say which one and why."""
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    client, db = _app(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    alive = _seed_asset_with_scenes(
        db, project["id"], "alive.mp4", segments=[(10, 200, "the agent farm plans the mission")],
        media_dir=tmp_path / "media",
    )
    # No media_dir -> the source path was never created: exactly the live shape.
    dead = _seed_asset_with_scenes(
        db, project["id"], "dead.mp4", segments=[(20, 220, "the mission handoff is executed")],
    )
    seen: dict[str, Any] = {}

    def _capture(_config: Any, **kwargs: Any) -> OverviewDecision:
        seen["candidates"] = kwargs["candidates"]
        return {
            "clips": [Candidate(alive, "alive.mp4", 1, 0, 300, "the agent farm plans")],
            "rationale": "the one video that is actually there",
            "fallback": False,
        }

    monkeypatch.setattr("laura.api.short_creator.run_overview_scout", _capture)

    r = client.post(
        f"/projects/{project['id']}/auto-overview", json={"topic": "mission"}, headers=_H
    )

    assert r.status_code == 202, r.text
    body = r.json()
    # The scout never even saw the dead asset — it cannot pick what it cannot render.
    assert {c.asset_id for c in seen["candidates"]} == {alive}
    assert all(c["asset_id"] == alive for c in body["clips"])
    assert any("dead.mp4" in w and "source" in w for w in body["warnings"]), body["warnings"]
    assert dead not in [c["asset_id"] for c in body["clips"]]


def test_all_sources_vanished_is_422_and_writes_nothing(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Every matching video's media is gone: nothing can be built, so nothing is — and the
    reason names the missing sources instead of claiming the topic found no material."""
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    client, db = _app(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    _seed_asset_with_scenes(
        db, project["id"], "gone.mp4", segments=[(10, 200, "the agent farm plans the mission")],
    )
    before = _counts(db)

    r = client.post(
        f"/projects/{project['id']}/auto-overview", json={"topic": "mission"}, headers=_H
    )

    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["reason"] == "no usable material: every matching video's source file is missing"
    assert detail["missing_sources"] == ["gone.mp4"]
    assert _counts(db) == before
