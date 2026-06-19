"""Plan B / Task B6 — roll_boundary (pure) + apply_fix (resnap + transition)."""

from __future__ import annotations

from pathlib import Path

import pytest

from laura.analysis.transition_review import SuggestedFix, apply_fix
from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.editing.operations import EditClip, roll_boundary


def _ec(asset: str, si: int, so: int, qi: int, qo: int, **kw: int) -> EditClip:
    return EditClip(
        asset_id=asset,
        src_in_frame=si,
        src_out_frame_exclusive=so,
        seq_in_frame=qi,
        seq_out_frame_exclusive=qo,
        **kw,
    )


# --- roll_boundary (pure) ----------------------------------------------------


def test_roll_boundary_worked_example() -> None:
    a = _ec("A", 100, 200, 0, 100)
    b = _ec("A", 200, 300, 100, 200)
    out = roll_boundary([a, b], 100, 10)
    a2 = next(c for c in out if c.seq_in_frame == 0)
    b2 = next(c for c in out if c.seq_out_frame_exclusive == 200)
    assert a2.src_out_frame_exclusive == 210 and a2.seq_out_frame_exclusive == 110
    assert b2.src_in_frame == 210 and b2.seq_in_frame == 110
    assert b2.seq_out_frame_exclusive == 200  # downstream end unchanged (total length preserved)


def test_roll_boundary_negative_delta() -> None:
    out = roll_boundary([_ec("A", 100, 200, 0, 100), _ec("A", 200, 300, 100, 200)], 100, -10)
    a2 = next(c for c in out if c.seq_in_frame == 0)
    assert a2.src_out_frame_exclusive == 190 and a2.seq_out_frame_exclusive == 90


def test_roll_boundary_out_of_range_raises() -> None:
    a = _ec("A", 100, 200, 0, 100)
    b = _ec("A", 200, 210, 100, 110)  # len_b == 10 -> hi == 9
    with pytest.raises(ValueError):
        roll_boundary([a, b], 100, 50)


def test_roll_boundary_speed_guard() -> None:
    a = _ec("A", 100, 200, 0, 100, speed_num=1, speed_den=2)
    b = _ec("A", 200, 300, 100, 200)
    with pytest.raises(ValueError):
        roll_boundary([a, b], 100, 5)


def test_roll_boundary_missing_boundary_raises() -> None:
    with pytest.raises(ValueError):
        roll_boundary([_ec("A", 0, 30, 0, 30), _ec("A", 30, 60, 30, 60)], 999, 5)


# --- apply_fix (DB) ----------------------------------------------------------


def _db(tmp_path: Path) -> SqliteDatabase:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    return db


def _seed(db: SqliteDatabase) -> tuple[str, str, str, str]:
    project = repos.create_project(
        db, name="P", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/ws"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a.mov", source_path="/a.mov"
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    c0 = repos.add_timeline_clip(
        db,
        timeline_id=tl["id"],
        asset_id=asset["id"],
        src_in_frame=0,
        src_out_frame_exclusive=100,
        seq_in_frame=0,
        seq_out_frame_exclusive=100,
    )
    c1 = repos.add_timeline_clip(
        db,
        timeline_id=tl["id"],
        asset_id=asset["id"],
        src_in_frame=100,
        src_out_frame_exclusive=200,
        seq_in_frame=100,
        seq_out_frame_exclusive=200,
    )
    return tl["id"], c0["id"], c1["id"], asset["id"]


def _identity(asset: str) -> dict[str, object]:
    return {"asset_a": asset, "asset_b": asset, "src_out_a": 100, "src_in_b": 100}


def _by_id(db: SqliteDatabase, tl_id: str) -> dict[str, dict[str, object]]:
    return {c["id"]: c for c in repos.list_timeline_clips(db, tl_id)}


def test_apply_transition_sets_crossfade(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tl, c0, _c1, asset = _seed(db)
    res = apply_fix(
        db,
        timeline_id=tl,
        identity=_identity(asset),
        fix=SuggestedFix(kind="transition", transition_style="crossfade", transition_frames=6),
    )
    assert res["status"] == "ok"
    a = _by_id(db, tl)[c0]
    assert a["transition_after_kind"] == "crossfade" and a["transition_after_frames"] == 6


def test_apply_resnap_moves_frames_and_preserves_transition(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tl, c0, c1, asset = _seed(db)
    repos.set_clip_transition(db, clip_id=c0, kind="crossfade", frames=6)  # must survive the roll
    res = apply_fix(
        db,
        timeline_id=tl,
        identity=_identity(asset),
        fix=SuggestedFix(kind="resnap", resnap_delta_frames=10),
    )
    assert res["status"] == "ok" and res["delta"] == 10
    clips = _by_id(db, tl)
    assert (
        clips[c0]["src_out_frame_exclusive"] == 110 and clips[c0]["seq_out_frame_exclusive"] == 110
    )
    assert clips[c1]["src_in_frame"] == 110 and clips[c1]["seq_in_frame"] == 110
    assert clips[c1]["seq_out_frame_exclusive"] == 200  # total length preserved
    assert clips[c0]["transition_after_kind"] == "crossfade"  # PRESERVED across resnap


def test_apply_resnap_clamps_to_window(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tl, c0, _c1, asset = _seed(db)
    res = apply_fix(
        db,
        timeline_id=tl,
        identity=_identity(asset),
        fix=SuggestedFix(kind="resnap", resnap_delta_frames=999),
    )
    assert res["status"] == "ok" and res["delta"] == 12  # clamped to the editorial window


def test_apply_resnap_zero_delta_is_error(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tl, _c0, _c1, asset = _seed(db)
    res = apply_fix(
        db,
        timeline_id=tl,
        identity=_identity(asset),
        fix=SuggestedFix(kind="resnap", resnap_delta_frames=0),
    )
    assert res["status"] == "error"


def test_apply_fix_boundary_not_found(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tl, _c0, _c1, asset = _seed(db)
    bad = {"asset_a": asset, "asset_b": asset, "src_out_a": 9999, "src_in_b": 100}
    res = apply_fix(
        db, timeline_id=tl, identity=bad, fix=SuggestedFix(kind="resnap", resnap_delta_frames=5)
    )
    assert res["status"] == "error"


def test_apply_none_is_noop(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tl, _c0, _c1, asset = _seed(db)
    res = apply_fix(db, timeline_id=tl, identity=_identity(asset), fix=SuggestedFix(kind="none"))
    assert res["status"] == "ok"
