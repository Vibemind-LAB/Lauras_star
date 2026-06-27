# services/local-api/tests/test_flatten_sequence.py
from __future__ import annotations

from pathlib import Path
from typing import Any

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.sequences.flatten import flatten_sequence


def _scene_timeline(
    db: SqliteDatabase,
    project_id: str,
    asset_id: str,
    src_in: int,
    src_out: int,
) -> dict[str, Any]:
    """A materialized scene timeline with one clip, rebased to seq 0."""
    tl = repos.create_timeline(db, project_id=project_id, name="s", kind="scene")
    repos.replace_timeline_clips(
        db,
        tl["id"],
        [
            {
                "asset_id": asset_id,
                "src_in_frame": src_in,
                "src_out_frame_exclusive": src_out,
                "seq_in_frame": 0,
                "seq_out_frame_exclusive": src_out - src_in,
                "lane": 0,
                "speed_num": 1,
                "speed_den": 1,
            }
        ],
    )
    return tl


def test_flatten_concatenates_scenes_with_offsets(tmp_path: Path) -> None:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    a1 = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="a1",
        source_path="/tmp/a1.mp4",
    )
    a2 = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="a2",
        source_path="/tmp/a2.mp4",
    )
    rc = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.replace_scenes(db, project["id"], rc["id"], [(0, 30), (30, 70)])
    s1, s2 = repos.list_scenes(db, rc["id"])
    # materialize each scene to its own timeline (40f and 30f from two assets)
    t1 = _scene_timeline(db, project["id"], a1["id"], 100, 130)  # 30 frames
    t2 = _scene_timeline(db, project["id"], a2["id"], 200, 240)  # 40 frames
    repos.set_scene_timeline(db, s1["id"], t1["id"])
    repos.set_scene_timeline(db, s2["id"], t2["id"])
    seq = repos.create_timeline(db, project_id=project["id"], name="seq", kind="sequence")
    repos.replace_sequence_items(db, seq["id"], [s2["id"], s1["id"]])  # s2 first
    rows = flatten_sequence(db, seq["id"])
    assert [(r["asset_id"], r["seq_in_frame"], r["seq_out_frame_exclusive"]) for r in rows] == [
        (a2["id"], 0, 40),     # s2: 40 frames at offset 0
        (a1["id"], 40, 70),    # s1: 30 frames at offset 40
    ]
    assert rows[0]["src_in_frame"] == 200 and rows[1]["src_in_frame"] == 100


# ---------------------------------------------------------------------------
# Modus B1 tests (spec §3.3, Tests C §8)
# ---------------------------------------------------------------------------


def _make_db(
    tmp_path: Path,
) -> tuple[SqliteDatabase, dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Create a DB, project, and two video assets."""
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    a1 = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a1", source_path="/tmp/a1.mp4"
    )
    a2 = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a2", source_path="/tmp/a2.mp4"
    )
    return db, project, a1, a2


def test_flatten_lane0_only_regression(tmp_path: Path) -> None:
    """Test C §8 #13 — lane-0-only sequence: B1 flatten is byte-identical to the old behaviour.

    Two scenes, s1=30 frames, s2=40 frames, ordered s2 then s1.
    Expected: s2 at [0,40), s1 at [40,70) — exactly as before B1.
    """
    db, project, a1, a2 = _make_db(tmp_path)
    rc = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.replace_scenes(db, project["id"], rc["id"], [(0, 30), (30, 70)])
    s1, s2 = repos.list_scenes(db, rc["id"])
    t1 = _scene_timeline(db, project["id"], a1["id"], 100, 130)  # 30 frames, lane 0
    t2 = _scene_timeline(db, project["id"], a2["id"], 200, 240)  # 40 frames, lane 0
    repos.set_scene_timeline(db, s1["id"], t1["id"])
    repos.set_scene_timeline(db, s2["id"], t2["id"])
    seq = repos.create_timeline(db, project_id=project["id"], name="seq", kind="sequence")
    repos.replace_sequence_items(db, seq["id"], [s2["id"], s1["id"]])

    rows = flatten_sequence(db, seq["id"])

    # Lane-0 scenes are contiguous — exactly today's behaviour
    expected = [(a2["id"], 0, 40, 0), (a1["id"], 40, 70, 0)]
    actual = [(r["asset_id"], r["seq_in_frame"], r["seq_out_frame_exclusive"], r["lane"])
              for r in rows]
    assert actual == expected
    # No overlay rows present
    assert all(r["lane"] == 0 for r in rows)


def test_flatten_b1_overlay_absolute_position(tmp_path: Path) -> None:
    """Test C §8 #14 — B1: lane-≥1 overlays on the sequence timeline appear at their
    absolute seq_in positions; lane-0 scene clips remain contiguous.

    Setup:
    - Scene s1 = 30 frames (a1, lane 0)
    - Scene s2 = 40 frames (a2, lane 0)
    - Sequence order: s2 first (0..40), then s1 (40..70)
    - One lane-1 overlay clip placed directly on the sequence at seq_in=10, 20 frames duration

    Expected flatten result:
    - a2 lane 0 at [0, 40)    (s2, offset 0)
    - a1 lane 0 at [40, 70)   (s1, offset 40)
    - a1 lane 1 at [10, 30)   (overlay at its absolute position, no re-offset)
    """
    db, project, a1, a2 = _make_db(tmp_path)
    rc = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.replace_scenes(db, project["id"], rc["id"], [(0, 30), (30, 70)])
    s1, s2 = repos.list_scenes(db, rc["id"])
    t1 = _scene_timeline(db, project["id"], a1["id"], 100, 130)  # 30 frames, lane 0
    t2 = _scene_timeline(db, project["id"], a2["id"], 200, 240)  # 40 frames, lane 0
    repos.set_scene_timeline(db, s1["id"], t1["id"])
    repos.set_scene_timeline(db, s2["id"], t2["id"])

    seq = repos.create_timeline(db, project_id=project["id"], name="seq", kind="sequence")
    repos.replace_sequence_items(db, seq["id"], [s2["id"], s1["id"]])

    # Place a lane-1 overlay directly on the sequence timeline (absolute position)
    repos.replace_timeline_clips(
        db,
        seq["id"],
        [
            {
                "asset_id": a1["id"],
                "src_in_frame": 0,
                "src_out_frame_exclusive": 20,
                "seq_in_frame": 10,     # absolute position in sequence
                "seq_out_frame_exclusive": 30,
                "lane": 1,              # overlay lane — must NOT be re-offset
                "speed_num": 1,
                "speed_den": 1,
            }
        ],
    )

    rows = flatten_sequence(db, seq["id"])

    # Extract lane-0 and lane-1 rows separately for clear assertions
    lane0 = [
        (r["asset_id"], r["seq_in_frame"], r["seq_out_frame_exclusive"])
        for r in rows if r["lane"] == 0
    ]
    lane1 = [
        (r["asset_id"], r["seq_in_frame"], r["seq_out_frame_exclusive"])
        for r in rows if r["lane"] == 1
    ]

    # Lane-0: scenes contiguous (unchanged B1 behaviour)
    assert lane0 == [
        (a2["id"], 0, 40),   # s2 at offset 0
        (a1["id"], 40, 70),  # s1 at offset 40
    ]

    # Lane-1: overlay at its absolute seq_in=10, NOT shifted by scene_len
    assert lane1 == [(a1["id"], 10, 30)]

    # The overlay seq_in must NOT equal 10 + 40 (i.e. must not have been re-offset
    # by the s2 scene length of 40 frames)
    assert lane1[0][1] != 50, "overlay was incorrectly re-offset by scene length"


def test_flatten_b1_scene_lane1_clips_excluded(tmp_path: Path) -> None:
    """Lane-≥1 clips that live *inside* a scene timeline are NOT included in the flatten result.

    Per spec §3.3 B1: only lane-0 clips from scenes contribute to the contiguous primary
    track; overlay clips from scene timelines have no defined absolute sequence position
    and are intentionally skipped.
    """
    db, project, a1, a2 = _make_db(tmp_path)
    rc = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.replace_scenes(db, project["id"], rc["id"], [(0, 30)])
    (s1,) = repos.list_scenes(db, rc["id"])

    # Scene timeline with a lane-0 clip AND a lane-1 clip
    t1 = repos.create_timeline(db, project_id=project["id"], name="s1", kind="scene")
    repos.replace_timeline_clips(
        db,
        t1["id"],
        [
            {
                "asset_id": a1["id"],
                "src_in_frame": 0,
                "src_out_frame_exclusive": 30,
                "seq_in_frame": 0,
                "seq_out_frame_exclusive": 30,
                "lane": 0,
                "speed_num": 1,
                "speed_den": 1,
            },
            {
                "asset_id": a2["id"],
                "src_in_frame": 0,
                "src_out_frame_exclusive": 10,
                "seq_in_frame": 5,
                "seq_out_frame_exclusive": 15,
                "lane": 1,   # this scene-local overlay must NOT appear in the sequence flatten
                "speed_num": 1,
                "speed_den": 1,
            },
        ],
    )
    repos.set_scene_timeline(db, s1["id"], t1["id"])

    seq = repos.create_timeline(db, project_id=project["id"], name="seq", kind="sequence")
    repos.replace_sequence_items(db, seq["id"], [s1["id"]])

    rows = flatten_sequence(db, seq["id"])

    # Only the lane-0 clip from the scene should appear
    assert len(rows) == 1
    assert rows[0]["lane"] == 0
    assert rows[0]["asset_id"] == a1["id"]
    assert rows[0]["seq_in_frame"] == 0
    assert rows[0]["seq_out_frame_exclusive"] == 30
