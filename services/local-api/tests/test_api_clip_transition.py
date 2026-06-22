"""Plan A / Task 5 — PATCH /timelines/{id}/clips/{clip_id}/transition."""

from __future__ import annotations

from fastapi.testclient import TestClient

from laura.db import repos
from laura.db.database import Database


def _seed_clip(db: Database) -> tuple[str, str]:
    project = repos.create_project(
        db, name="P", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/ws"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="c.mov", source_path="/c.mov"
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    clip = repos.add_timeline_clip(
        db,
        timeline_id=tl["id"],
        asset_id=asset["id"],
        src_in_frame=0,
        src_out_frame_exclusive=30,
        seq_in_frame=0,
        seq_out_frame_exclusive=30,
    )
    return tl["id"], clip["id"]


def _kind(db: Database, tl_id: str, clip_id: str) -> tuple[str, int]:
    row = next(c for c in repos.list_timeline_clips(db, tl_id) if c["id"] == clip_id)
    return row["transition_after_kind"], row["transition_after_frames"]


def test_set_clip_transition_crossfade(client: TestClient, db: Database) -> None:
    tl_id, clip_id = _seed_clip(db)
    r = client.patch(
        f"/timelines/{tl_id}/clips/{clip_id}/transition",
        json={"kind": "crossfade", "duration_frames": 12},
    )
    assert r.status_code == 200
    assert _kind(db, tl_id, clip_id) == ("crossfade", 12)


def test_set_clip_transition_hard_forces_zero_frames(client: TestClient, db: Database) -> None:
    tl_id, clip_id = _seed_clip(db)
    r = client.patch(
        f"/timelines/{tl_id}/clips/{clip_id}/transition",
        json={"kind": "hard", "duration_frames": 12},
    )
    assert r.status_code == 200
    assert _kind(db, tl_id, clip_id) == ("hard", 0)


def test_set_clip_transition_unknown_kind_coerced_to_hard(client: TestClient, db: Database) -> None:
    tl_id, clip_id = _seed_clip(db)
    r = client.patch(
        f"/timelines/{tl_id}/clips/{clip_id}/transition",
        json={"kind": "banana", "duration_frames": 5},
    )
    assert r.status_code == 200
    assert _kind(db, tl_id, clip_id) == ("hard", 0)


def test_set_clip_transition_unknown_clip_404(client: TestClient, db: Database) -> None:
    tl_id, _clip_id = _seed_clip(db)
    r = client.patch(
        f"/timelines/{tl_id}/clips/does-not-exist/transition",
        json={"kind": "crossfade", "duration_frames": 6},
    )
    assert r.status_code == 404


def test_set_clip_transition_frames_over_cap_422(client: TestClient, db: Database) -> None:
    tl_id, clip_id = _seed_clip(db)
    r = client.patch(
        f"/timelines/{tl_id}/clips/{clip_id}/transition",
        json={"kind": "crossfade", "duration_frames": 9999},
    )
    assert r.status_code == 422  # SequenceTransitionRequest caps duration_frames at 240
