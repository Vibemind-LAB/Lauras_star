"""P4 tests — place_clip wired through the POST /timelines/{id}/operations API.

These tests drive the full HTTP stack (FastAPI TestClient → _apply → place_clip fn)
to verify that the OperationRequest fields and dispatch are correctly wired (spec §8,
P4 scope).  The pure-function semantics (overlap checks, audio_offset rules, etc.) are
covered in test_place_clip_lane_packing.py; here we focus on the API contract.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app

# ---------------------------------------------------------------------------
# Fixture: a TestClient + DB with a timeline carrying two clips on two lanes
# ---------------------------------------------------------------------------


def _setup(tmp_path: Path) -> tuple[TestClient, SqliteDatabase, str, str]:
    """Return (client, db, timeline_id, asset_id) seeded with two clips.

    Lane 0: clip A  seq [0, 10)
    Lane 1: clip B  seq [20, 35)   (free-placed; gap [10, 20) on lane 1 is intentional)
    """
    settings = Settings(workspace_root=tmp_path, start_runner=False, token=None)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    client = TestClient(create_app(settings))
    client.__enter__()

    project = client.post(
        "/projects",
        json={"name": "test-project", "sequence_rate_num": 30, "sequence_rate_den": 1},
    ).json()
    project_id: str = project["id"]

    tl = repos.create_timeline(db, project_id=project_id, name="rc", kind="rough_cut")
    tl_id: str = str(tl["id"])

    asset = repos.create_asset(
        db,
        project_id=project_id,
        type="video",
        display_name="source.mp4",
        source_path=str(tmp_path / "source.mp4"),
    )
    asset_id: str = str(asset["id"])

    # Lane-0 clip: seq [0, 10), src [0, 10)
    repos.add_timeline_clip(
        db,
        timeline_id=tl_id,
        asset_id=asset_id,
        src_in_frame=0,
        src_out_frame_exclusive=10,
        seq_in_frame=0,
        seq_out_frame_exclusive=10,
        lane=0,
        role="base",
    )
    # Lane-1 clip: seq [20, 35), src [20, 35)
    repos.add_timeline_clip(
        db,
        timeline_id=tl_id,
        asset_id=asset_id,
        src_in_frame=20,
        src_out_frame_exclusive=35,
        seq_in_frame=20,
        seq_out_frame_exclusive=35,
        lane=1,
        role="replace",
    )
    return client, db, tl_id, asset_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_place_clip_moves_lane1_clip_to_new_position(tmp_path: Path) -> None:
    """Moves the lane-1 clip from seq=20 to seq=5 (still lane 1, no lane-0 clip there)."""
    client, db, tl_id, _asset_id = _setup(tmp_path)

    resp = client.post(
        f"/timelines/{tl_id}/operations",
        json={
            "op": "place_clip",
            "at_seq_frame": 20,   # source position (clip B starts here)
            "lane_src": 1,        # clip B is on lane 1
            "to_seq_frame": 5,    # destination
            "lane": 1,            # destination lane (same lane)
        },
    )
    assert resp.status_code == 200, resp.text

    clips = repos.list_timeline_clips(db, tl_id)
    lane1 = [c for c in clips if c["lane"] == 1]
    assert len(lane1) == 1
    assert lane1[0]["seq_in_frame"] == 5
    assert lane1[0]["seq_out_frame_exclusive"] == 5 + 15  # duration preserved
    # Lane-0 clip must be byte-identical.
    lane0 = [c for c in clips if c["lane"] == 0]
    assert len(lane0) == 1
    assert lane0[0]["seq_in_frame"] == 0
    assert lane0[0]["seq_out_frame_exclusive"] == 10


def test_place_clip_moves_clip_from_lane0_to_lane1(tmp_path: Path) -> None:
    """Moves the lane-0 clip to lane 1 at seq=40 (no overlap with existing lane-1 clip)."""
    client, db, tl_id, _asset_id = _setup(tmp_path)

    resp = client.post(
        f"/timelines/{tl_id}/operations",
        json={
            "op": "place_clip",
            "at_seq_frame": 0,    # lane-0 clip starts at 0
            "lane_src": 0,
            "to_seq_frame": 40,
            "lane": 1,            # move to lane 1
        },
    )
    assert resp.status_code == 200, resp.text

    clips = repos.list_timeline_clips(db, tl_id)
    lane1 = [c for c in clips if c["lane"] == 1]
    assert len(lane1) == 2  # original B + moved A
    moved = next(c for c in lane1 if c["seq_in_frame"] == 40)
    assert moved["seq_out_frame_exclusive"] == 50  # dur 10 preserved
    # Original lane-1 clip is untouched.
    orig = next(c for c in lane1 if c["seq_in_frame"] == 20)
    assert orig["seq_out_frame_exclusive"] == 35


def test_place_clip_invalid_source_returns_422(tmp_path: Path) -> None:
    """Requesting a clip that does not exist → 422."""
    client, _db, tl_id, _asset_id = _setup(tmp_path)

    resp = client.post(
        f"/timelines/{tl_id}/operations",
        json={
            "op": "place_clip",
            "at_seq_frame": 999,  # no clip here
            "lane_src": 0,
            "to_seq_frame": 50,
            "lane": 0,
        },
    )
    assert resp.status_code == 422


def test_place_clip_intra_lane_overlap_returns_422(tmp_path: Path) -> None:
    """Placing clip B at seq=0 on lane 1 would overlap existing clip A on lane 0 — allowed
    (cross-lane); but placing B at seq=22 on lane 1 would overlap B itself if lane_src wrong.
    More importantly: trying to place clip A (lane 0, [0,10)) to seq=25 lane 1 WOULD overlap
    B [20,35) on lane 1 → 422."""
    client, _db, tl_id, _asset_id = _setup(tmp_path)

    resp = client.post(
        f"/timelines/{tl_id}/operations",
        json={
            "op": "place_clip",
            "at_seq_frame": 0,   # clip A, lane 0, dur=10
            "lane_src": 0,
            "to_seq_frame": 25,  # would land [25,35) → overlaps B [20,35) on lane 1
            "lane": 1,
        },
    )
    assert resp.status_code == 422


def test_place_clip_cross_lane_overlap_allowed(tmp_path: Path) -> None:
    """Cross-lane temporal overlap is explicitly allowed (spec §1.4).

    Moving clip B (lane 1) to seq=0 creates [0,15) on lane 1, overlapping A [0,10) on lane 0.
    The API must accept this (200).
    """
    client, db, tl_id, _asset_id = _setup(tmp_path)

    resp = client.post(
        f"/timelines/{tl_id}/operations",
        json={
            "op": "place_clip",
            "at_seq_frame": 20,   # clip B, lane 1, dur=15
            "lane_src": 1,
            "to_seq_frame": 0,    # [0,15) — overlaps lane 0's [0,10)
            "lane": 1,
        },
    )
    assert resp.status_code == 200, resp.text

    clips = repos.list_timeline_clips(db, tl_id)
    lane1 = [c for c in clips if c["lane"] == 1]
    assert any(c["seq_in_frame"] == 0 for c in lane1)


def test_place_clip_negative_to_seq_frame_returns_422(tmp_path: Path) -> None:
    client, _db, tl_id, _asset_id = _setup(tmp_path)

    resp = client.post(
        f"/timelines/{tl_id}/operations",
        json={
            "op": "place_clip",
            "at_seq_frame": 20,
            "lane_src": 1,
            "to_seq_frame": -1,  # invalid
            "lane": 1,
        },
    )
    assert resp.status_code == 422


def test_place_clip_lane_src_defaults_to_lane_when_omitted(tmp_path: Path) -> None:
    """If lane_src is omitted, the backend falls back to `lane` (=0 default) — useful for
    the simple single-lane case where no cross-lane move is needed."""
    client, db, tl_id, _asset_id = _setup(tmp_path)

    # Move clip A (lane 0, seq=0) to seq=50 — keep on lane 0.
    # lane_src is omitted; lane=0 (default) identifies the source lane.
    resp = client.post(
        f"/timelines/{tl_id}/operations",
        json={
            "op": "place_clip",
            "at_seq_frame": 0,
            "to_seq_frame": 50,
            # lane omitted → defaults to 0; lane_src omitted → falls back to lane(0)
        },
    )
    assert resp.status_code == 200, resp.text

    clips = repos.list_timeline_clips(db, tl_id)
    lane0 = [c for c in clips if c["lane"] == 0]
    assert len(lane0) == 1
    assert lane0[0]["seq_in_frame"] == 50
    assert lane0[0]["seq_out_frame_exclusive"] == 60  # dur=10 preserved


def test_place_clip_preserves_src_range_and_speed(tmp_path: Path) -> None:
    """src_in, src_out, speed_num, speed_den must not be touched by place_clip."""
    client, db, tl_id, _asset_id = _setup(tmp_path)

    clip_b_before = next(
        c for c in repos.list_timeline_clips(db, tl_id) if c["lane"] == 1
    )

    resp = client.post(
        f"/timelines/{tl_id}/operations",
        json={
            "op": "place_clip",
            "at_seq_frame": 20,
            "lane_src": 1,
            "to_seq_frame": 40,
            "lane": 1,
        },
    )
    assert resp.status_code == 200, resp.text

    clip_b_after = next(
        c for c in repos.list_timeline_clips(db, tl_id) if c["lane"] == 1
    )
    assert clip_b_after["src_in_frame"] == clip_b_before["src_in_frame"]
    assert clip_b_after["src_out_frame_exclusive"] == clip_b_before["src_out_frame_exclusive"]
    assert clip_b_after["speed_num"] == clip_b_before["speed_num"]
    assert clip_b_after["speed_den"] == clip_b_before["speed_den"]
