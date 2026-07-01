"""Fix 1 + Fix 2 regression tests.

Fix 1 — ``delete``/``lift`` ops thread ``body.lane`` through so lane≥1 clips are
scoped correctly and lane-0 clips are untouched.

Fix 2 — a ``role="replace"`` clip survives any op round-trip (place_clip, move, trim,
etc.) with its role intact; ``replace_timeline_clips`` now writes the role column.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app

# ---------------------------------------------------------------------------
# Shared setup helper
# ---------------------------------------------------------------------------


def _setup(tmp_path: Path) -> tuple[TestClient, SqliteDatabase, str, str]:
    """Create a project + timeline with two clips:

    Lane 0: clip A  src [0, 10)  seq [0, 10)   role="base"
    Lane 1: clip B  src [20, 35) seq [20, 35)  role="replace"
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

    # Lane-0 base clip
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
    # Lane-1 replace-overlay clip
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
# Fix 1 — lane-scoped delete / lift
# ---------------------------------------------------------------------------


def test_delete_lane1_removes_only_lane1_clip(tmp_path: Path) -> None:
    """A delete op with lane=1 ripples only lane-1 clips; lane-0 is byte-identical."""
    client, db, tl_id, _asset = _setup(tmp_path)

    resp = client.post(
        f"/timelines/{tl_id}/operations",
        json={
            "op": "delete",
            "seq_in_frame": 20,
            "seq_out_frame_exclusive": 35,
            "lane": 1,
        },
    )
    assert resp.status_code == 200, resp.text

    clips = repos.list_timeline_clips(db, tl_id)
    lane0 = [c for c in clips if c["lane"] == 0]
    lane1 = [c for c in clips if c["lane"] == 1]

    # Lane-1 clip was deleted
    assert lane1 == [], "expected lane-1 clip to be removed by lane=1 delete"

    # Lane-0 clip is byte-identical
    assert len(lane0) == 1
    assert lane0[0]["seq_in_frame"] == 0
    assert lane0[0]["seq_out_frame_exclusive"] == 10


def test_delete_lane0_default_leaves_lane1_untouched(tmp_path: Path) -> None:
    """A delete op without explicit lane (defaults to 0) only affects lane-0 clips."""
    client, db, tl_id, _asset = _setup(tmp_path)

    resp = client.post(
        f"/timelines/{tl_id}/operations",
        json={
            "op": "delete",
            "seq_in_frame": 0,
            "seq_out_frame_exclusive": 10,
            # lane omitted → defaults to 0
        },
    )
    assert resp.status_code == 200, resp.text

    clips = repos.list_timeline_clips(db, tl_id)
    lane0 = [c for c in clips if c["lane"] == 0]
    lane1 = [c for c in clips if c["lane"] == 1]

    assert lane0 == [], "expected lane-0 clip to be deleted"
    assert len(lane1) == 1
    assert lane1[0]["seq_in_frame"] == 20
    assert lane1[0]["seq_out_frame_exclusive"] == 35


def test_lift_lane1_leaves_gap_and_lane0_untouched(tmp_path: Path) -> None:
    """A lift op with lane=1 leaves a gap (no ripple) and lane-0 is byte-identical."""
    client, db, tl_id, _asset = _setup(tmp_path)

    resp = client.post(
        f"/timelines/{tl_id}/operations",
        json={
            "op": "lift",
            "seq_in_frame": 20,
            "seq_out_frame_exclusive": 35,
            "lane": 1,
        },
    )
    assert resp.status_code == 200, resp.text

    clips = repos.list_timeline_clips(db, tl_id)
    lane0 = [c for c in clips if c["lane"] == 0]
    lane1 = [c for c in clips if c["lane"] == 1]

    assert lane1 == [], "expected lane-1 clip to be lifted"
    # Lift leaves no ripple — lane-0 clip is at same position
    assert len(lane0) == 1
    assert lane0[0]["seq_in_frame"] == 0
    assert lane0[0]["seq_out_frame_exclusive"] == 10


# ---------------------------------------------------------------------------
# Fix 2 — role preserved through op pipeline
# ---------------------------------------------------------------------------


def test_place_clip_preserves_replace_role(tmp_path: Path) -> None:
    """A role='replace' lane-1 clip keeps its role after place_clip (move)."""
    client, db, tl_id, _asset = _setup(tmp_path)

    # Verify the clip starts as role="replace"
    before = next(c for c in repos.list_timeline_clips(db, tl_id) if c["lane"] == 1)
    assert before["role"] == "replace"

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

    after_clips = repos.list_timeline_clips(db, tl_id)
    lane1 = [c for c in after_clips if c["lane"] == 1]
    assert len(lane1) == 1
    assert lane1[0]["seq_in_frame"] == 40
    # Role must survive the op round-trip
    assert lane1[0]["role"] == "replace", (
        f"role was reset to {lane1[0]['role']!r} — Fix 2 broken"
    )


def test_role_preserved_through_lane1_delete(tmp_path: Path) -> None:
    """After a lane-0 delete, the surviving lane-1 replace clip still has role='replace'."""
    client, db, tl_id, _asset = _setup(tmp_path)

    # Delete the lane-0 clip — this re-writes ALL clips via replace_timeline_clips
    resp = client.post(
        f"/timelines/{tl_id}/operations",
        json={
            "op": "delete",
            "seq_in_frame": 0,
            "seq_out_frame_exclusive": 10,
            "lane": 0,
        },
    )
    assert resp.status_code == 200, resp.text

    clips = repos.list_timeline_clips(db, tl_id)
    lane1 = [c for c in clips if c["lane"] == 1]
    assert len(lane1) == 1
    assert lane1[0]["role"] == "replace", (
        f"role was silently reset to {lane1[0]['role']!r} by replace_timeline_clips"
    )


def test_replace_timeline_clips_writes_role(tmp_path: Path) -> None:
    """replace_timeline_clips writes the role column from the row dict (not the DB default)."""
    from laura.editing.operations import EditClip, ordered

    settings = Settings(workspace_root=tmp_path, start_runner=False, token=None)
    db = SqliteDatabase(settings.db_path)
    db.migrate()

    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video",
        display_name="a.mp4", source_path="/tmp/a.mp4",
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    tl_id: str = str(tl["id"])

    clip = EditClip(
        asset_id=asset["id"],
        src_in_frame=0,
        src_out_frame_exclusive=10,
        seq_in_frame=0,
        seq_out_frame_exclusive=10,
        lane=1,
        role="replace",
    )
    repos.replace_timeline_clips(db, tl_id, [c.to_row() for c in ordered([clip])])

    persisted = repos.list_timeline_clips(db, tl_id)
    assert len(persisted) == 1
    assert persisted[0]["role"] == "replace", (
        f"replace_timeline_clips lost the role; got {persisted[0]['role']!r}"
    )
