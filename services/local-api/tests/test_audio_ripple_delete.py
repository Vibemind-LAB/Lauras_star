"""Tests: ripple_timeline_audio_clips and apply_operation ripple integration.

Fix 2 — delete/delete_words must shift timeline_audio_clips so VO/music stays
in sync with the post-delete video.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app

_TOKEN = "test-token"


def _setup(tmp_path: Path) -> tuple[SqliteDatabase, str, str, str]:
    """Create project, asset with words, timeline with a single clip.

    Returns (db, timeline_id, asset_id, word_id).
    """
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False, token=_TOKEN)
    db = SqliteDatabase(settings.db_path)
    db.migrate()

    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a", source_path="/tmp/a.mp4"
    )
    run = repos.create_analysis_run(
        db, asset_id=asset["id"], pipeline_version="t", config={}
    )
    repos.insert_segment_with_words(
        db,
        asset_id=asset["id"],
        run_id=run["id"],
        speaker_id=None,
        segment={
            "start_sample": 0,
            "end_sample": 0,
            "start_frame": 10,
            "end_frame": 20,
            "text": "uh",
            "confidence": 1.0,
        },
        words=[
            {
                "idx": 0,
                "start_sample": 0,
                "end_sample": 0,
                "start_frame": 10,
                "end_frame": 20,
                "text": "uh",
                "confidence": 1.0,
                "is_punctuation": False,
            }
        ],
    )
    transcript = repos.get_transcript(db, asset["id"], run["id"])
    assert transcript
    word_id = transcript[0]["words"][0]["id"]

    tl = repos.create_timeline(db, project_id=project["id"], name="s", kind="scene")
    repos.replace_timeline_clips(
        db,
        tl["id"],
        [
            {
                "asset_id": asset["id"],
                "src_in_frame": 0,
                "src_out_frame_exclusive": 60,
                "seq_in_frame": 0,
                "seq_out_frame_exclusive": 60,
                "lane": 0,
                "speed_num": 1,
                "speed_den": 1,
            }
        ],
    )
    return db, tl["id"], asset["id"], word_id


def test_audio_clip_ripples_on_delete_words(tmp_path: Path) -> None:
    """VO clip at [40,60) shifts to [30,50) after deleting words that span seq [10,20)."""
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False, token=_TOKEN)
    app = create_app(settings)
    client = TestClient(app)
    db = app.state.db

    db_direct, tl_id, asset_id, word_id = _setup(tmp_path)
    # Re-use app's db (it's the same file)
    db = app.state.db

    # Set up timeline using the app's db
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a", source_path="/tmp/a.mp4"
    )
    run = repos.create_analysis_run(db, asset_id=asset["id"], pipeline_version="t", config={})
    repos.insert_segment_with_words(
        db,
        asset_id=asset["id"],
        run_id=run["id"],
        speaker_id=None,
        segment={
            "start_sample": 0, "end_sample": 0,
            "start_frame": 10, "end_frame": 20,
            "text": "uh", "confidence": 1.0,
        },
        words=[{
            "idx": 0, "start_sample": 0, "end_sample": 0,
            "start_frame": 10, "end_frame": 20,
            "text": "uh", "confidence": 1.0, "is_punctuation": False,
        }],
    )
    transcript = repos.get_transcript(db, asset["id"], run["id"])
    assert transcript
    word = transcript[0]["words"][0]

    tl = repos.create_timeline(db, project_id=project["id"], name="s", kind="scene")
    repos.replace_timeline_clips(
        db, tl["id"],
        [{
            "asset_id": asset["id"],
            "src_in_frame": 0, "src_out_frame_exclusive": 60,
            "seq_in_frame": 0, "seq_out_frame_exclusive": 60,
            "lane": 0, "speed_num": 1, "speed_den": 1,
        }],
    )

    audio_asset = repos.create_asset(
        db, project_id=project["id"], type="audio",
        display_name="vo.wav", source_path="/tmp/vo.wav",
    )
    repos.add_timeline_audio_clip(
        db, timeline_id=tl["id"], asset_id=audio_asset["id"],
        seq_in_frame=40, seq_out_frame_exclusive=60,
    )

    r = client.post(
        f"/timelines/{tl['id']}/operations",
        json={"op": "delete_words", "word_start_id": word["id"], "word_end_id": word["id"]},
        headers={"X-Laura-Token": _TOKEN},
    )
    assert r.status_code == 200, r.text

    # Audio clip should have shifted left by 10 frames (deleted span was [10,20))
    clips = repos.list_timeline_audio_clips(db, tl["id"])
    assert len(clips) == 1
    assert clips[0]["seq_in_frame"] == 30
    assert clips[0]["seq_out_frame_exclusive"] == 50


def test_audio_clip_fully_inside_delete_removed(tmp_path: Path) -> None:
    """Audio clip fully inside [10,20) is dropped."""
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False, token=_TOKEN)
    app = create_app(settings)
    db = app.state.db

    project = repos.create_project(
        db, name="p2", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p2"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a", source_path="/tmp/a.mp4"
    )
    run = repos.create_analysis_run(db, asset_id=asset["id"], pipeline_version="t", config={})
    repos.insert_segment_with_words(
        db,
        asset_id=asset["id"],
        run_id=run["id"],
        speaker_id=None,
        segment={
            "start_sample": 0, "end_sample": 0,
            "start_frame": 10, "end_frame": 20,
            "text": "uh", "confidence": 1.0,
        },
        words=[{
            "idx": 0, "start_sample": 0, "end_sample": 0,
            "start_frame": 10, "end_frame": 20,
            "text": "uh", "confidence": 1.0, "is_punctuation": False,
        }],
    )
    transcript = repos.get_transcript(db, asset["id"], run["id"])
    assert transcript
    word = transcript[0]["words"][0]

    tl = repos.create_timeline(db, project_id=project["id"], name="s", kind="scene")
    repos.replace_timeline_clips(
        db, tl["id"],
        [{
            "asset_id": asset["id"],
            "src_in_frame": 0, "src_out_frame_exclusive": 60,
            "seq_in_frame": 0, "seq_out_frame_exclusive": 60,
            "lane": 0, "speed_num": 1, "speed_den": 1,
        }],
    )

    audio_asset = repos.create_asset(
        db, project_id=project["id"], type="audio",
        display_name="vo.wav", source_path="/tmp/vo.wav",
    )
    repos.add_timeline_audio_clip(
        db, timeline_id=tl["id"], asset_id=audio_asset["id"],
        seq_in_frame=12, seq_out_frame_exclusive=18,
    )

    client = TestClient(app)
    r = client.post(
        f"/timelines/{tl['id']}/operations",
        json={"op": "delete_words", "word_start_id": word["id"], "word_end_id": word["id"]},
        headers={"X-Laura-Token": _TOKEN},
    )
    assert r.status_code == 200, r.text

    clips = repos.list_timeline_audio_clips(db, tl["id"])
    assert len(clips) == 0, "clip fully inside deleted span should be removed"


def test_audio_clip_ripples_on_delete_op(tmp_path: Path) -> None:
    """op='delete' with explicit seq_in/out also ripples audio clips."""
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False, token=_TOKEN)
    app = create_app(settings)
    db = app.state.db

    project = repos.create_project(
        db, name="p3", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p3"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a", source_path="/tmp/a.mp4"
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="s", kind="scene")
    repos.replace_timeline_clips(
        db, tl["id"],
        [{
            "asset_id": asset["id"],
            "src_in_frame": 0, "src_out_frame_exclusive": 60,
            "seq_in_frame": 0, "seq_out_frame_exclusive": 60,
            "lane": 0, "speed_num": 1, "speed_den": 1,
        }],
    )

    audio_asset = repos.create_asset(
        db, project_id=project["id"], type="audio",
        display_name="vo.wav", source_path="/tmp/vo.wav",
    )
    # Audio at [25, 40) — delete [10, 20) → shifts to [15, 30)
    repos.add_timeline_audio_clip(
        db, timeline_id=tl["id"], asset_id=audio_asset["id"],
        seq_in_frame=25, seq_out_frame_exclusive=40,
    )

    client = TestClient(app)
    r = client.post(
        f"/timelines/{tl['id']}/operations",
        json={"op": "delete", "seq_in_frame": 10, "seq_out_frame_exclusive": 20},
        headers={"X-Laura-Token": _TOKEN},
    )
    assert r.status_code == 200, r.text

    clips = repos.list_timeline_audio_clips(db, tl["id"])
    assert len(clips) == 1
    assert clips[0]["seq_in_frame"] == 15
    assert clips[0]["seq_out_frame_exclusive"] == 30
