from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app

_TOKEN = "test-token"


def test_delete_words_ripple_closes_gap(tmp_path: Path) -> None:
    app = create_app(Settings(workspace_root=tmp_path / "ws", start_runner=False, token=_TOKEN))
    client, db = TestClient(app), app.state.db

    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a", source_path="/tmp/a.mp4"
    )
    run = repos.create_analysis_run(
        db, asset_id=asset["id"], pipeline_version="t", config={}
    )

    # insert_segment_with_words is the real helper in repos.py
    seg_id = repos.insert_segment_with_words(
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

    # fetch the inserted word so we have its id
    transcript = repos.get_transcript(db, asset["id"], run["id"])
    assert transcript, "transcript must have at least one segment"
    w = transcript[0]["words"][0]
    assert w["start_frame"] == 10 and w["end_frame"] == 20

    tl = repos.create_timeline(db, project_id=project["id"], name="s", kind="scene")
    repos.replace_timeline_clips(
        db,
        tl["id"],
        [
            {
                "asset_id": asset["id"],
                "src_in_frame": 0,
                "src_out_frame_exclusive": 30,
                "seq_in_frame": 0,
                "seq_out_frame_exclusive": 30,
                "lane": 0,
                "speed_num": 1,
                "speed_den": 1,
            }
        ],
    )

    r = client.post(
        f"/timelines/{tl['id']}/operations",
        json={"op": "delete_words", "word_start_id": w["id"], "word_end_id": w["id"]},
        headers={"X-Laura-Token": _TOKEN},
    )
    assert r.status_code == 200, r.text
    clips = r.json()["clips"]
    # word at src 10..20 removed (ripple): two clips 0..10 and 10..20 remain, total length 20
    assert max(c["seq_out_frame_exclusive"] for c in clips) == 20
