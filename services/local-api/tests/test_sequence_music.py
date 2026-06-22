"""Unit tests for sequences.music.sequence_music_tracks.

Seeds two scenes into a sequence (scene 1: no music, scene 2: music set).
Verifies that exactly one track is returned, at the correct offset (after
scene 1's length), with the expected path and gain.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.sequences.music import sequence_music_tracks


def _db(tmp_path: Path) -> SqliteDatabase:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    return db


def _scene_timeline(
    db: SqliteDatabase, project_id: str, asset_id: str, scene_len: int
) -> dict[str, Any]:
    """Create a materialised scene timeline with one clip of *scene_len* frames."""
    tl = repos.create_timeline(db, project_id=project_id, name="s", kind="scene")
    repos.replace_timeline_clips(db, tl["id"], [{
        "asset_id": asset_id,
        "src_in_frame": 0,
        "src_out_frame_exclusive": scene_len,
        "seq_in_frame": 0,
        "seq_out_frame_exclusive": scene_len,
        "lane": 0,
        "speed_num": 1,
        "speed_den": 1,
    }])
    return tl


def test_sequence_music_tracks_single_scene_with_music(tmp_path: Path) -> None:
    """Two scenes; only scene 2 has music — one track returned at the right offset."""
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(tmp_path / "ws" / "proj"),
    )
    video_asset = repos.create_asset(
        db, project_id=project["id"], type="video",
        display_name="v.mp4", source_path="/tmp/v.mp4",
    )
    music_asset = repos.create_asset(
        db, project_id=project["id"], type="audio",
        display_name="music.m4a", source_path="/tmp/music.m4a",
    )

    # Two scenes in a rough-cut timeline
    rc = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.replace_scenes(db, project["id"], rc["id"], [(0, 30), (30, 60)])
    scene1, scene2 = repos.list_scenes(db, rc["id"])

    # Materialise both scenes
    tl1 = _scene_timeline(db, project["id"], video_asset["id"], scene_len=30)
    tl2 = _scene_timeline(db, project["id"], video_asset["id"], scene_len=30)
    repos.set_scene_timeline(db, scene1["id"], tl1["id"])
    repos.set_scene_timeline(db, scene2["id"], tl2["id"])

    # Only scene 2 gets music
    repos.set_scene_music(db, scene2["id"], music_asset["id"], 75)

    # Build a sequence with scene1 first, scene2 second
    seq = repos.create_timeline(db, project_id=project["id"], name="seq", kind="sequence")
    repos.replace_sequence_items(db, seq["id"], [scene1["id"], scene2["id"]])

    tracks = sequence_music_tracks(db, seq["id"])

    # Exactly one track (scene 1 has no music)
    assert len(tracks) == 1
    path, seq_in, seq_out, gain = tracks[0]
    # Path resolved from the asset
    assert path == Path("/tmp/music.m4a")
    # Scene 2 starts at offset = scene_1_len = 30
    assert seq_in == 30
    # Scene 2 ends at offset + scene_2_len = 30 + 30 = 60
    assert seq_out == 60
    # Gain as set
    assert gain == 75


def test_sequence_music_tracks_no_music(tmp_path: Path) -> None:
    """Sequence where no scene has music → empty list."""
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p2", rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(tmp_path / "ws" / "proj2"),
    )
    video_asset = repos.create_asset(
        db, project_id=project["id"], type="video",
        display_name="v.mp4", source_path="/tmp/v.mp4",
    )
    rc = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.replace_scenes(db, project["id"], rc["id"], [(0, 30)])
    (scene1,) = repos.list_scenes(db, rc["id"])
    tl1 = _scene_timeline(db, project["id"], video_asset["id"], scene_len=30)
    repos.set_scene_timeline(db, scene1["id"], tl1["id"])
    seq = repos.create_timeline(db, project_id=project["id"], name="seq", kind="sequence")
    repos.replace_sequence_items(db, seq["id"], [scene1["id"]])

    tracks = sequence_music_tracks(db, seq["id"])
    assert tracks == []


def test_sequence_music_tracks_both_scenes_have_music(tmp_path: Path) -> None:
    """Both scenes have different music → two tracks at correct offsets."""
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p3", rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(tmp_path / "ws" / "proj3"),
    )
    video_asset = repos.create_asset(
        db, project_id=project["id"], type="video",
        display_name="v.mp4", source_path="/tmp/v.mp4",
    )
    music1 = repos.create_asset(
        db, project_id=project["id"], type="audio",
        display_name="m1.m4a", source_path="/tmp/m1.m4a",
    )
    music2 = repos.create_asset(
        db, project_id=project["id"], type="audio",
        display_name="m2.m4a", source_path="/tmp/m2.m4a",
    )
    rc = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.replace_scenes(db, project["id"], rc["id"], [(0, 20), (20, 50)])
    scene1, scene2 = repos.list_scenes(db, rc["id"])
    tl1 = _scene_timeline(db, project["id"], video_asset["id"], scene_len=20)
    tl2 = _scene_timeline(db, project["id"], video_asset["id"], scene_len=30)
    repos.set_scene_timeline(db, scene1["id"], tl1["id"])
    repos.set_scene_timeline(db, scene2["id"], tl2["id"])
    repos.set_scene_music(db, scene1["id"], music1["id"], 100)
    repos.set_scene_music(db, scene2["id"], music2["id"], 50)

    seq = repos.create_timeline(db, project_id=project["id"], name="seq", kind="sequence")
    repos.replace_sequence_items(db, seq["id"], [scene1["id"], scene2["id"]])

    tracks = sequence_music_tracks(db, seq["id"])
    assert len(tracks) == 2

    p0, in0, out0, g0 = tracks[0]
    assert p0 == Path("/tmp/m1.m4a") and in0 == 0 and out0 == 20 and g0 == 100

    p1, in1, out1, g1 = tracks[1]
    assert p1 == Path("/tmp/m2.m4a") and in1 == 20 and out1 == 50 and g1 == 50
