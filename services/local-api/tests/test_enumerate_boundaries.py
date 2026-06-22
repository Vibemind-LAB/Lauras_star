"""Plan B / Task B2 — kind-aware enumerate_boundaries (rough_cut/scene path)."""

from __future__ import annotations

from pathlib import Path

from laura.analysis.transition_review import enumerate_boundaries
from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase


def _db(tmp_path: Path) -> SqliteDatabase:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    return db


def _seed(db: SqliteDatabase) -> tuple[str, str, str]:
    project = repos.create_project(
        db, name="P", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/ws"
    )
    a = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a.mov", source_path="/a.mov"
    )
    b = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="b.mov", source_path="/b.mov"
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")

    def add(asset: str, si: int, so: int, qi: int, qo: int) -> None:
        repos.add_timeline_clip(
            db, timeline_id=tl["id"], asset_id=asset,
            src_in_frame=si, src_out_frame_exclusive=so,
            seq_in_frame=qi, seq_out_frame_exclusive=qo,
        )

    add(a["id"], 0, 100, 0, 100)      # clip0
    add(a["id"], 100, 160, 100, 160)  # clip1 -> boundary0: contiguous same-source (jump)
    add(a["id"], 200, 260, 160, 220)  # clip2 -> boundary1: same asset, src gap 40
    add(b["id"], 0, 50, 220, 270)     # clip3 -> boundary2: distinct asset
    return tl["id"], a["id"], b["id"]


def test_enumerate_rough_cut_boundaries(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tl_id, asset_a, asset_b = _seed(db)
    bs = enumerate_boundaries(db, tl_id)
    assert len(bs) == 3
    b0, b1, b2 = bs

    # boundary0: contiguous same-source -> the canonical dead-air jump cut
    assert b0.same_source is True and b0.removed_gap_frames == 0
    assert b0.asset_a == asset_a and b0.asset_b == asset_a
    assert b0.src_out_a == 100 and b0.src_in_b == 100 and b0.seq_out_a == 100

    # boundary1: same asset but a real source gap -> NOT a jump cut
    assert b1.same_source is False and b1.removed_gap_frames == 40

    # boundary2: distinct assets -> not same-source, no gap notion
    assert b2.same_source is False and b2.removed_gap_frames == 0 and b2.asset_b == asset_b

    assert all(b.kind == "rough_cut" for b in bs)


def test_enumerate_single_clip_has_no_boundary(tmp_path: Path) -> None:
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="P", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/ws"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a.mov", source_path="/a.mov"
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.add_timeline_clip(
        db, timeline_id=tl["id"], asset_id=asset["id"],
        src_in_frame=0, src_out_frame_exclusive=30, seq_in_frame=0, seq_out_frame_exclusive=30,
    )
    assert enumerate_boundaries(db, tl["id"]) == []


def test_enumerate_missing_timeline(tmp_path: Path) -> None:
    db = _db(tmp_path)
    assert enumerate_boundaries(db, "does-not-exist") == []
