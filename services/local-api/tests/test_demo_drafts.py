from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from laura.db import repos
from laura.db.database import Database
from laura.demo.drafts import build_demo_draft_items


def _seed_video(db: Database, tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    project = repos.create_project(
        db,
        name="demo",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(tmp_path / "project"),
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="screen.mp4",
        source_path=str(tmp_path / "screen.mp4"),
    )
    repos.update_asset_probe(
        db,
        asset["id"],
        type="video",
        duration_frames=180,
        rate_num=30,
        rate_den=1,
        audio_sample_rate=48_000,
        start_timecode=None,
        width=1280,
        height=720,
        codec_video="h264",
        codec_audio="aac",
        is_vfr=False,
        sha256=None,
    )
    fresh = repos.get_asset(db, asset["id"])
    assert fresh is not None
    return project, fresh


def _add_analysis(db: Database, asset_id: str) -> None:
    run = repos.create_analysis_run(db, asset_id=asset_id, pipeline_version="test", config={})
    repos.insert_shots(
        db,
        asset_id=asset_id,
        run_id=run["id"],
        shots=[
            {"src_in_frame": 0, "src_out_frame_exclusive": 60, "confidence": 0.9},
            {"src_in_frame": 60, "src_out_frame_exclusive": 120, "confidence": 0.8},
        ],
    )
    repos.insert_segment_with_words(
        db,
        asset_id=asset_id,
        run_id=run["id"],
        speaker_id=None,
        segment={
            "start_sample": 0,
            "end_sample": 96_000,
            "start_frame": 0,
            "end_frame": 60,
            "text": "Import your clip and review the result",
            "confidence": 1.0,
        },
        words=[],
    )


def test_build_demo_draft_items_prefers_shots_and_transcript(
    db: Database,
    tmp_path: Path,
) -> None:
    _project, asset = _seed_video(db, tmp_path)
    _add_analysis(db, asset["id"])

    items = build_demo_draft_items(db, asset["id"])

    assert [item["src_in_frame"] for item in items] == [0, 60]
    assert [item["src_out_frame_exclusive"] for item in items] == [60, 120]
    assert items[0]["label"] == "Import your clip and review the result"
    assert items[0]["voiceover_text"] == "Import your clip and review the result"
    assert items[0]["enabled"] is True


def test_build_demo_draft_items_falls_back_to_six_second_blocks(
    db: Database,
    tmp_path: Path,
) -> None:
    _project, asset = _seed_video(db, tmp_path)

    items = build_demo_draft_items(db, asset["id"])

    assert len(items) == 1
    assert items[0]["src_in_frame"] == 0
    assert items[0]["src_out_frame_exclusive"] == 180
    assert items[0]["label"] == "Schritt 1"


def test_demo_draft_api_create_update_apply(
    client: TestClient,
    tmp_path: Path,
) -> None:
    app = cast(Any, client.app)
    db: Database = app.state.db
    project, asset = _seed_video(db, tmp_path)
    _add_analysis(db, asset["id"])

    accepted = client.post(f"/assets/{asset['id']}/demo-drafts")
    assert accepted.status_code == 202, accepted.text
    draft_id = accepted.json()["draft_id"]
    job_id = accepted.json()["job_id"]

    assert app.state.runner.run_once() is True
    job = repos.get_job(db, job_id)
    assert job is not None
    assert job["status"] == "succeeded"

    draft = client.get(f"/demo-drafts/{draft_id}")
    assert draft.status_code == 200, draft.text
    body = draft.json()
    assert body["status"] == "ready"
    assert len(body["items"]) == 2

    edited_items = body["items"]
    edited_items[0]["label"] = "Intro"
    edited_items[0]["voiceover_text"] = "Start with the import screen."
    edited_items[1]["enabled"] = False
    patched = client.patch(f"/demo-drafts/{draft_id}", json={"items": edited_items})
    assert patched.status_code == 200, patched.text
    assert patched.json()["items"][0]["label"] == "Intro"

    applied = client.post(f"/demo-drafts/{draft_id}/apply")
    assert applied.status_code == 200, applied.text
    sequence = applied.json()["sequence"]
    assert len(sequence["items"]) == 1
    assert sequence["items"][0]["scene_name"] == "Intro"

    scenes = repos.list_project_scenes(db, project["id"])
    assert len(scenes) == 1
    assert scenes[0]["name"] == "Intro"

    stored = repos.get_demo_draft(db, draft_id)
    assert stored is not None
    assert stored["status"] == "applied"
    result = json.loads(stored["result_json"])
    assert result["sequence_id"] == sequence["timeline_id"]


def test_demo_draft_rejects_non_video_asset(client: TestClient, tmp_path: Path) -> None:
    app = cast(Any, client.app)
    db: Database = app.state.db
    project = repos.create_project(
        db,
        name="demo",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(tmp_path / "project"),
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="audio",
        display_name="voice.wav",
        source_path=str(tmp_path / "voice.wav"),
    )

    response = client.post(f"/assets/{asset['id']}/demo-drafts")

    assert response.status_code == 422
    assert "video asset" in response.text
