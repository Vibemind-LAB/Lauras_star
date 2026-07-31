"""build_overview writes the montage on three levels — its own source timeline, one scene per
clip, its own sequence — and touches nothing else (spec 2026-07-31-auto-overview-design.md §5).
"""

from __future__ import annotations

from pathlib import Path

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.short_creator.overview_build import build_overview
from laura.short_creator.overview_windows import Candidate

FPS = 30


def _db(tmp_path: Path) -> Database:
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db: Database = SqliteDatabase(settings.db_path)
    db.migrate()
    return db


def _project_with_two_assets(db: Database) -> tuple[str, str, str]:
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    a = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="A", source_path="/tmp/a"
    )
    b = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="B", source_path="/tmp/b"
    )
    return str(project["id"]), str(a["id"]), str(b["id"])


def test_builds_source_timeline_scenes_and_sequence(tmp_path: Path) -> None:
    db = _db(tmp_path)
    project_id, a, b = _project_with_two_assets(db)
    clips = [
        Candidate(a, "A", 1, 100, 400, "alpha"),   # 300 frames
        Candidate(b, "B", 2, 500, 650, "beta"),    # 150 frames
    ]

    out = build_overview(db, project_id=project_id, topic="mission", clips=clips)

    source = repos.get_timeline(db, out["source_timeline_id"])
    assert source is not None
    assert source["kind"] == "overview", "must NOT be rough_cut — see get_asset_rough_cut"
    assert "mission" in source["name"]

    # One clip per window, laid out contiguously, source ranges preserved.
    rows = repos.list_timeline_clips(db, out["source_timeline_id"])
    assert [(r["asset_id"], r["src_in_frame"], r["src_out_frame_exclusive"]) for r in rows] == [
        (a, 100, 400),
        (b, 500, 650),
    ]
    assert [(r["seq_in_frame"], r["seq_out_frame_exclusive"]) for r in rows] == [
        (0, 300),
        (300, 450),
    ]

    # One scene per clip, each materialized, in clip order.
    scenes = repos.list_scenes(db, out["source_timeline_id"])
    assert len(scenes) == 2
    assert out["scene_ids"] == [str(s["id"]) for s in scenes]
    assert all(s["scene_timeline_id"] for s in scenes)

    # The sequence references those scenes, in order.
    sequence = repos.get_timeline(db, out["sequence_id"])
    assert sequence is not None
    assert sequence["kind"] == "sequence"
    items = repos.list_sequence_items(db, out["sequence_id"])
    assert [str(i["scene_id"]) for i in items] == out["scene_ids"]


def test_the_project_sequence_is_left_alone(tmp_path: Path) -> None:
    """`get_or_create_project_sequence` returns the OLDEST sequence — the user's assembly."""
    db = _db(tmp_path)
    project_id, a, _b = _project_with_two_assets(db)
    existing = repos.get_or_create_project_sequence(db, project_id)

    out = build_overview(
        db, project_id=project_id, topic="mission",
        clips=[Candidate(a, "A", 1, 0, 300, "alpha")],
    )

    assert out["sequence_id"] != existing["id"]
    assert repos.list_sequence_items(db, existing["id"]) == []
    # And it is still the one the project sequence lookup returns.
    assert repos.get_or_create_project_sequence(db, project_id)["id"] == existing["id"]


def test_a_materialized_scene_holds_exactly_its_own_clip(tmp_path: Path) -> None:
    """materialize_scene re-offsets to 0 — the scene timeline is what flatten_sequence reads."""
    db = _db(tmp_path)
    project_id, a, b = _project_with_two_assets(db)
    clips = [
        Candidate(a, "A", 1, 100, 400, "alpha"),
        Candidate(b, "B", 2, 500, 650, "beta"),
    ]

    out = build_overview(db, project_id=project_id, topic="mission", clips=clips)

    scenes = repos.list_scenes(db, out["source_timeline_id"])
    second = repos.list_timeline_clips(db, str(scenes[1]["scene_timeline_id"]))
    assert len(second) == 1
    assert second[0]["asset_id"] == b
    assert (second[0]["src_in_frame"], second[0]["src_out_frame_exclusive"]) == (500, 650)
    assert (second[0]["seq_in_frame"], second[0]["seq_out_frame_exclusive"]) == (0, 150)


def test_empty_clips_is_a_programming_error(tmp_path: Path) -> None:
    db = _db(tmp_path)
    project_id, _a, _b = _project_with_two_assets(db)
    try:
        build_overview(db, project_id=project_id, topic="mission", clips=[])
    except ValueError as exc:
        assert "clips" in str(exc)
    else:  # pragma: no cover - the raise is the contract
        raise AssertionError("expected ValueError")
