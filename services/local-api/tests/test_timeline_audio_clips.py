from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from laura.db import repos
from laura.db.database import Database


def _seed(db: Database) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    project = repos.create_project(
        db,
        name="p",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root="/tmp/p",
    )
    timeline = repos.create_timeline(
        db,
        project_id=project["id"],
        name="sequence",
        kind="sequence",
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="audio",
        display_name="voiceover",
        source_path="/tmp/voiceover.wav",
    )
    repos.update_asset_probe(
        db,
        asset["id"],
        type="audio",
        duration_frames=300,
        rate_num=30,
        rate_den=1,
        audio_sample_rate=48_000,
        start_timecode=None,
        width=None,
        height=None,
        codec_video=None,
        codec_audio="pcm_s16le",
        is_vfr=False,
        sha256=None,
    )
    asset = repos.get_asset(db, asset["id"])
    assert asset is not None
    return project, timeline, asset


def test_timeline_audio_clip_migration(db: Database) -> None:
    with db.connection() as conn:
        rows = conn.execute("PRAGMA table_info(timeline_audio_clips)").fetchall()
        indexes = conn.execute("PRAGMA index_list(timeline_audio_clips)").fetchall()

    cols = {row["name"] for row in rows}
    assert {
        "id",
        "timeline_id",
        "asset_id",
        "seq_in_frame",
        "seq_out_frame_exclusive",
        "asset_in_frame",
        "gain_percent",
        "fade_in_frames",
        "fade_out_frames",
        "mix_mode",
        "ducking_percent",
        "label",
        "created_at",
    } <= cols
    assert any(row["name"] == "idx_timeline_audio_clips_timeline_order" for row in indexes)


def test_timeline_audio_clip_repo_crud(db: Database) -> None:
    _, timeline, asset = _seed(db)

    first = repos.add_timeline_audio_clip(
        db,
        timeline_id=timeline["id"],
        asset_id=asset["id"],
        seq_in_frame=60,
        seq_out_frame_exclusive=120,
        asset_in_frame=10,
        gain_percent=80,
        fade_in_frames=6,
        fade_out_frames=12,
        mix_mode="mix",
        ducking_percent=35,
        label="VO",
    )
    second = repos.add_timeline_audio_clip(
        db,
        timeline_id=timeline["id"],
        asset_id=asset["id"],
        seq_in_frame=0,
        seq_out_frame_exclusive=30,
    )

    clips = repos.list_timeline_audio_clips(db, timeline["id"])
    assert [clip["id"] for clip in clips] == [second["id"], first["id"]]
    assert clips[1]["gain_percent"] == 80
    assert clips[1]["fade_out_frames"] == 12
    assert clips[1]["ducking_percent"] == 35
    assert clips[1]["label"] == "VO"

    updated = repos.update_timeline_audio_clip(
        db,
        first["id"],
        gain_percent=120,
        fade_in_frames=3,
        ducking_percent=20,
        label="VO 2",
    )
    assert updated is not None
    assert updated["gain_percent"] == 120
    assert updated["fade_in_frames"] == 3
    assert updated["ducking_percent"] == 20
    assert updated["label"] == "VO 2"

    assert repos.delete_timeline_audio_clip(db, second["id"]) is True
    assert [clip["id"] for clip in repos.list_timeline_audio_clips(db, timeline["id"])] == [
        first["id"]
    ]


def test_timeline_audio_clip_api_crud(client: TestClient, db: Database) -> None:
    _, timeline, asset = _seed(db)

    created = client.post(
        f"/timelines/{timeline['id']}/audio-clips",
        json={
            "asset_id": asset["id"],
            "seq_in_frame": 12,
            "seq_out_frame_exclusive": 72,
            "asset_in_frame": 4,
            "gain_percent": 90,
            "fade_in_frames": 5,
            "fade_out_frames": 8,
            "label": "Narration",
            "mix_mode": "replace_original",
            "ducking_percent": 0,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["timeline_id"] == timeline["id"]
    assert body["asset_id"] == asset["id"]
    assert body["mix_mode"] == "replace_original"
    assert body["ducking_percent"] == 0
    assert body["label"] == "Narration"

    listed = client.get(f"/timelines/{timeline['id']}/audio-clips")
    assert listed.status_code == 200
    assert [clip["id"] for clip in listed.json()] == [body["id"]]

    patched = client.patch(
        f"/timelines/{timeline['id']}/audio-clips/{body['id']}",
        json={
            "gain_percent": 110,
            "fade_out_frames": 4,
            "mix_mode": "replace_original",
            "ducking_percent": 25,
            "label": "Narration clean",
        },
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["gain_percent"] == 110
    assert patched.json()["fade_out_frames"] == 4
    assert patched.json()["mix_mode"] == "replace_original"
    assert patched.json()["ducking_percent"] == 25
    assert patched.json()["label"] == "Narration clean"

    deleted = client.request("DELETE", f"/timelines/{timeline['id']}/audio-clips/{body['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/timelines/{timeline['id']}/audio-clips").json() == []


def test_timeline_audio_clip_api_validation(client: TestClient, db: Database) -> None:
    _, timeline, asset = _seed(db)

    invalid_range = client.post(
        f"/timelines/{timeline['id']}/audio-clips",
        json={
            "asset_id": asset["id"],
            "seq_in_frame": 20,
            "seq_out_frame_exclusive": 20,
        },
    )
    assert invalid_range.status_code == 422

    invalid_fades = client.post(
        f"/timelines/{timeline['id']}/audio-clips",
        json={
            "asset_id": asset["id"],
            "seq_in_frame": 0,
            "seq_out_frame_exclusive": 10,
            "fade_in_frames": 6,
            "fade_out_frames": 5,
        },
    )
    assert invalid_fades.status_code == 422

    invalid_gain = client.post(
        f"/timelines/{timeline['id']}/audio-clips",
        json={
            "asset_id": asset["id"],
            "seq_in_frame": 0,
            "seq_out_frame_exclusive": 10,
            "gain_percent": 401,
        },
    )
    assert invalid_gain.status_code == 422

    unknown_asset = client.post(
        f"/timelines/{timeline['id']}/audio-clips",
        json={
            "asset_id": "missing",
            "seq_in_frame": 0,
            "seq_out_frame_exclusive": 10,
        },
    )
    assert unknown_asset.status_code == 404

    unknown_timeline = client.get("/timelines/missing/audio-clips")
    assert unknown_timeline.status_code == 404
