"""API tests for POST/DELETE /timelines/{id}/overlays (overlay replacement-lane clips)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database
from laura.main import create_app


def _client_db(tmp_path: Path) -> tuple[TestClient, Database]:
    settings = Settings(workspace_root=tmp_path, token=None, start_runner=False)
    app = create_app(settings)
    from typing import cast
    return TestClient(app), cast(Database, app.state.db)


def _project(client: TestClient) -> str:
    resp = client.post(
        "/projects", json={"name": "p", "sequence_rate_num": 30, "sequence_rate_den": 1}
    )
    return str(resp.json()["id"])


def _setup(tmp_path: Path) -> tuple[TestClient, Database, str, str, str]:
    """Return (client, db, project_id, timeline_id, overlay_asset_id)."""
    client, db = _client_db(tmp_path)
    pid = _project(client)

    # Base asset with known duration
    base_asset = repos.create_asset(
        db,
        project_id=pid,
        type="video",
        display_name="base.mp4",
        source_path="/fake/base.mp4",
    )
    repos.update_asset_probe(
        db,
        base_asset["id"],
        type="video",
        duration_frames=300,
        rate_num=30,
        rate_den=1,
        audio_sample_rate=48000,
        start_timecode=None,
        width=1920,
        height=1080,
        codec_video="h264",
        codec_audio="aac",
        is_vfr=False,
        sha256=None,
    )

    # Timeline with one base clip (frames 0-30)
    tl = repos.create_timeline(db, project_id=pid, name="cut", kind="rough_cut")
    repos.add_timeline_clip(
        db,
        timeline_id=tl["id"],
        asset_id=base_asset["id"],
        src_in_frame=0,
        src_out_frame_exclusive=30,
        seq_in_frame=0,
        seq_out_frame_exclusive=30,
        lane=0,
        role="base",
    )

    # Overlay/replacement asset
    overlay_asset = repos.create_asset(
        db,
        project_id=pid,
        type="video",
        display_name="overlay.mp4",
        source_path="/fake/overlay.mp4",
    )
    repos.update_asset_probe(
        db,
        overlay_asset["id"],
        type="video",
        duration_frames=60,
        rate_num=30,
        rate_den=1,
        audio_sample_rate=48000,
        start_timecode=None,
        width=1920,
        height=1080,
        codec_video="h264",
        codec_audio="aac",
        is_vfr=False,
        sha256=None,
    )

    return client, db, pid, tl["id"], overlay_asset["id"]


def test_add_overlay_201(tmp_path: Path) -> None:
    """POST a valid overlay returns 201 with role=='replace' and correct frame mapping."""
    client, db, _pid, tl_id, overlay_id = _setup(tmp_path)

    r = client.post(
        f"/timelines/{tl_id}/overlays",
        json={
            "asset_id": overlay_id,
            "seq_in_frame": 15,
            "seq_out_frame_exclusive": 30,
            "lane": 1,
            "src_in_frame": 0,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["role"] == "replace"
    assert body["lane"] == 1
    assert body["src_in_frame"] == 0
    assert body["src_out_frame_exclusive"] == 15  # range_len = 30 - 15 = 15
    assert body["seq_in_frame"] == 15
    assert body["seq_out_frame_exclusive"] == 30
    assert body["timeline_id"] == tl_id
    assert body["asset_id"] == overlay_id

    # Clip must appear in list_timeline_clips
    clips = repos.list_timeline_clips(db, tl_id)
    clip_ids = [c["id"] for c in clips]
    assert body["id"] in clip_ids


def test_delete_overlay_204(tmp_path: Path) -> None:
    """DELETE an overlay returns 204; clip is gone from list_timeline_clips."""
    client, db, _pid, tl_id, overlay_id = _setup(tmp_path)

    add_r = client.post(
        f"/timelines/{tl_id}/overlays",
        json={
            "asset_id": overlay_id,
            "seq_in_frame": 15,
            "seq_out_frame_exclusive": 30,
            "lane": 1,
        },
    )
    assert add_r.status_code == 201
    clip_id = add_r.json()["id"]

    del_r = client.delete(f"/timelines/{tl_id}/overlays/{clip_id}")
    assert del_r.status_code == 204

    clips = repos.list_timeline_clips(db, tl_id)
    assert all(c["id"] != clip_id for c in clips)


def test_add_overlay_400_invalid_range(tmp_path: Path) -> None:
    """400 when seq_out_frame_exclusive <= seq_in_frame."""
    client, _db, _pid, tl_id, overlay_id = _setup(tmp_path)

    r = client.post(
        f"/timelines/{tl_id}/overlays",
        json={
            "asset_id": overlay_id,
            "seq_in_frame": 20,
            "seq_out_frame_exclusive": 20,
            "lane": 1,
        },
    )
    assert r.status_code == 400

    r2 = client.post(
        f"/timelines/{tl_id}/overlays",
        json={
            "asset_id": overlay_id,
            "seq_in_frame": 25,
            "seq_out_frame_exclusive": 10,
            "lane": 1,
        },
    )
    assert r2.status_code == 400


def test_add_overlay_404_unknown_timeline(tmp_path: Path) -> None:
    """404 when the timeline id does not exist."""
    client, _db, _pid, _tl_id, overlay_id = _setup(tmp_path)

    r = client.post(
        "/timelines/nonexistent-tl/overlays",
        json={
            "asset_id": overlay_id,
            "seq_in_frame": 0,
            "seq_out_frame_exclusive": 10,
            "lane": 1,
        },
    )
    assert r.status_code == 404


def test_add_overlay_404_unknown_asset(tmp_path: Path) -> None:
    """404 when the asset id does not exist."""
    client, _db, _pid, tl_id, _overlay_id = _setup(tmp_path)

    r = client.post(
        f"/timelines/{tl_id}/overlays",
        json={
            "asset_id": "nonexistent-asset",
            "seq_in_frame": 0,
            "seq_out_frame_exclusive": 10,
            "lane": 1,
        },
    )
    assert r.status_code == 404


def test_add_overlay_400_asset_too_short(tmp_path: Path) -> None:
    """400 when the requested src range exceeds the asset's duration_frames."""
    client, _db, _pid, tl_id, overlay_id = _setup(tmp_path)

    # overlay_id has duration_frames=60; requesting src_in_frame=50, range_len=20 → src_out=70 > 60
    r = client.post(
        f"/timelines/{tl_id}/overlays",
        json={
            "asset_id": overlay_id,
            "seq_in_frame": 0,
            "seq_out_frame_exclusive": 20,
            "lane": 1,
            "src_in_frame": 50,
        },
    )
    assert r.status_code == 400


def test_delete_overlay_404_unknown_clip(tmp_path: Path) -> None:
    """DELETE 404 for a clip id that does not exist."""
    client, _db, _pid, tl_id, _overlay_id = _setup(tmp_path)

    r = client.delete(f"/timelines/{tl_id}/overlays/nonexistent-clip")
    assert r.status_code == 404
