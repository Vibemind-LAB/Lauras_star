"""run_scout: a single agent picks asset+scenes for a topic out of search_material's ranking,
validates the reply against real data, retries ONCE on a bad reply, and falls back
deterministically on anything else that goes wrong (spec 2026-07-21-auto-short-design.md §2).

Injected ``runner`` fakes stand in for the real LLM — no autogen, no network. Scene validation
runs against a real DB seed (mirrors test_discovery.py's ``_seed_asset_with_scenes``, duplicated
here per this repo's self-contained-test-file convention).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.short_creator import discovery, scout
from laura.short_creator.providers import resolve_from_env

FPS = 30


def _db(tmp_path: Path) -> Database:
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db: Database = SqliteDatabase(settings.db_path)
    db.migrate()
    return db


def _seed_asset_with_scenes(
    db: Database, project_id: str, name: str, *, segments: list[tuple[int, int, str]]
) -> str:
    """Asset + succeeded analysis run with *segments* (start_frame, end_frame, text) + a
    rough-cut timeline with one 1:1 clip over [0, 600) and two scenes [0,300)/[300,600)."""
    asset = repos.create_asset(
        db, project_id=project_id, type="video", display_name=name, source_path=f"/tmp/{name}"
    )
    run = repos.create_analysis_run(
        db, asset_id=asset["id"], pipeline_version="t", config={}
    )
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
        db, project_id=project_id, name="Rough Cut", kind="rough_cut",
        created_from=asset["id"],
    )
    repos.add_timeline_clip(
        db, timeline_id=timeline["id"], asset_id=asset["id"],
        src_in_frame=0, src_out_frame_exclusive=600,
        seq_in_frame=0, seq_out_frame_exclusive=600,
    )
    repos.replace_scenes(db, project_id, timeline["id"], [(0, 300), (300, 600)])
    return str(asset["id"])


def _scripted_runner(*replies: str) -> tuple[Callable[[str], str], list[str]]:
    """A fake runner that returns *replies* in order and records every task text it was called
    with (so tests can assert what the retry task looked like)."""
    calls: list[str] = []
    remaining = list(replies)

    def run(task: str) -> str:
        calls.append(task)
        return remaining.pop(0)

    return run, calls


def _raising_runner() -> tuple[Callable[[str], str], list[str]]:
    calls: list[str] = []

    def run(task: str) -> str:
        calls.append(task)
        raise RuntimeError("model unreachable")

    return run, calls


def _config() -> Any:
    return resolve_from_env({})


def test_valid_reply_is_adopted_verbatim(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset_id = _seed_asset_with_scenes(
        db, project["id"], "strong.mp4",
        segments=[(10, 60, "the agent farm plans the mission")],
    )
    material = discovery.search_material(db, project["id"], "mission")
    assert material["ranking"], "sanity: the seeded transcript must actually rank"

    reply = json.dumps(
        {
            "asset_id": asset_id,
            "scene_numbers": [1],
            "rationale": "covers the mission plan directly",
        }
    )
    run, calls = _scripted_runner(reply)

    decision = scout.run_scout(
        db, _config(), project_id=project["id"], topic="mission", material=material, runner=run
    )

    assert decision == {
        "asset_id": asset_id,
        "scene_numbers": [1],
        "rationale": "covers the mission plan directly",
        "fallback": False,
    }
    assert len(calls) == 1
    assert "mission" in calls[0]
    assert asset_id in calls[0]


def test_scene_not_in_snippets_but_in_rough_cut_is_valid(tmp_path: Path, monkeypatch: Any) -> None:
    """The ranking's scene_hits only cover scene 1 (the only segment seeded); the agent picking
    scene 2 — a real rough-cut scene the snippets never showed — must still be accepted (spec:
    "an agent may legitimately pick a scene the snippets didn't show")."""
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset_id = _seed_asset_with_scenes(
        db, project["id"], "strong.mp4",
        segments=[(10, 60, "the agent farm plans the mission")],
    )
    material = discovery.search_material(db, project["id"], "mission")
    top = material["ranking"][0]
    assert [h["scene_number"] for h in top["scene_hits"]] == [1]  # only scene 1 has a hit

    reply = json.dumps(
        {"asset_id": asset_id, "scene_numbers": [2], "rationale": "the payoff lands in scene 2"}
    )
    run, _calls = _scripted_runner(reply)

    decision = scout.run_scout(
        db, _config(), project_id=project["id"], topic="mission", material=material, runner=run
    )

    assert decision["fallback"] is False
    assert decision["scene_numbers"] == [2]


def test_invalid_asset_id_then_valid_retry_is_adopted(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset_id = _seed_asset_with_scenes(
        db, project["id"], "strong.mp4",
        segments=[(10, 60, "the agent farm plans the mission")],
    )
    material = discovery.search_material(db, project["id"], "mission")

    bad_reply = json.dumps(
        {"asset_id": "does-not-exist", "scene_numbers": [1], "rationale": "wrong asset"}
    )
    good_reply = json.dumps(
        {"asset_id": asset_id, "scene_numbers": [1], "rationale": "on point after correction"}
    )
    run, calls = _scripted_runner(bad_reply, good_reply)

    decision = scout.run_scout(
        db, _config(), project_id=project["id"], topic="mission", material=material, runner=run
    )

    assert decision == {
        "asset_id": asset_id,
        "scene_numbers": [1],
        "rationale": "on point after correction",
        "fallback": False,
    }
    assert len(calls) == 2
    # the SECOND task text carries the validation error from the first (invalid) reply
    assert "does-not-exist" in calls[1]
    assert "invalid" in calls[1].lower()
    # the retry task still contains the original task (topic + ranking), not just the error
    assert "mission" in calls[1]


def test_invalid_twice_falls_back(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    _seed_asset_with_scenes(
        db, project["id"], "strong.mp4",
        segments=[(10, 60, "the agent farm plans the mission")],
    )
    material = discovery.search_material(db, project["id"], "mission")
    top = material["ranking"][0]

    run, calls = _scripted_runner("not json at all", "still not json")

    decision = scout.run_scout(
        db, _config(), project_id=project["id"], topic="mission", material=material, runner=run
    )

    assert decision == {
        "asset_id": top["asset_id"],
        "scene_numbers": [h["scene_number"] for h in top["scene_hits"]],
        "rationale": "automatic fallback: top search score",
        "fallback": True,
    }
    assert len(calls) == 2  # exactly one retry, never more


def test_scene_ranges_raising_during_validation_falls_back(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """An infra failure UNDER validation (here: discovery._scene_ranges, e.g. a malformed
    timeline row or a rough cut torn down mid-request) must not escape run_scout as an
    exception — it degrades like any other invalid reply: first occurrence feeds the retry,
    still-broken on the retry falls back."""
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset_id = _seed_asset_with_scenes(
        db, project["id"], "strong.mp4",
        segments=[(10, 60, "the agent farm plans the mission")],
    )
    material = discovery.search_material(db, project["id"], "mission")
    top = material["ranking"][0]

    def _raise(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("timeline row is malformed")

    monkeypatch.setattr(discovery, "_scene_ranges", _raise)

    # Otherwise-perfectly-valid replies both times — the raise is the only reason this falls
    # back, proving the infra error (not a bad reply) drives the outcome.
    valid_reply = json.dumps(
        {"asset_id": asset_id, "scene_numbers": [1], "rationale": "on point"}
    )
    run, calls = _scripted_runner(valid_reply, valid_reply)

    decision = scout.run_scout(
        db, _config(), project_id=project["id"], topic="mission", material=material, runner=run
    )

    assert decision == {
        "asset_id": top["asset_id"],
        "scene_numbers": [h["scene_number"] for h in top["scene_hits"]],
        "rationale": "automatic fallback: top search score",
        "fallback": True,
    }
    assert len(calls) == 2  # first reply's validation raised -> counted as invalid -> ONE retry


def test_runner_raising_falls_back_without_retry(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    _seed_asset_with_scenes(
        db, project["id"], "strong.mp4",
        segments=[(10, 60, "the agent farm plans the mission")],
    )
    material = discovery.search_material(db, project["id"], "mission")
    top = material["ranking"][0]

    run, calls = _raising_runner()

    decision = scout.run_scout(
        db, _config(), project_id=project["id"], topic="mission", material=material, runner=run
    )

    assert decision == {
        "asset_id": top["asset_id"],
        "scene_numbers": [h["scene_number"] for h in top["scene_hits"]],
        "rationale": "automatic fallback: top search score",
        "fallback": True,
    }
    assert len(calls) == 1  # a runner exception is NOT retried — no retry storm


def test_empty_ranking_raises_value_error(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    material: dict[str, Any] = {"source": "lexical", "ranking": [], "skipped": []}

    def run(task: str) -> str:
        raise AssertionError("the runner must never be called for an empty ranking")

    with pytest.raises(ValueError):
        scout.run_scout(
            db, _config(), project_id=project["id"], topic="mission", material=material, runner=run
        )
