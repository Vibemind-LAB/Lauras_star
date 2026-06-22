"""Plan C / Task C2 — transition review + apply-fix API endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from laura.db import repos
from laura.db.database import Database


def _seed_rough_cut(db: Database) -> tuple[str, str, str]:
    """rough_cut, two contiguous same-source clips -> one jump-cut boundary. (tl, clip0, asset)."""
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
    repos.add_timeline_clip(
        db,
        timeline_id=tl["id"],
        asset_id=asset["id"],
        src_in_frame=100,
        src_out_frame_exclusive=200,
        seq_in_frame=100,
        seq_out_frame_exclusive=200,
    )
    return str(tl["id"]), str(c0["id"]), str(asset["id"])


def test_post_review_enqueues_job(client: TestClient, db: Database) -> None:
    tl_id, _c0, _asset = _seed_rough_cut(db)
    r = client.post(f"/timelines/{tl_id}/transitions/review")
    assert r.status_code == 202 and "job_id" in r.json()


def test_post_review_unknown_timeline_404(client: TestClient) -> None:
    assert client.post("/timelines/nope/transitions/review").status_code == 404


def test_get_review_returns_seeded_verdicts(client: TestClient, db: Database) -> None:
    tl_id, _c0, asset = _seed_rough_cut(db)
    repos.upsert_transition_review(
        db,
        timeline_id=tl_id,
        asset_a=asset,
        asset_b=asset,
        src_out_a=100,
        src_in_b=100,
        boundary_seq_frame=100,
        boundary_signature="s",
        smoothness=0.2,
        label="jump_cut",
        reason="dead-air jump",
        suggested_fix_json='{"kind":"transition","transition_style":"crossfade","transition_frames":6}',
        model_id="stub",
        model_digest="m1",
    )
    r = client.get(f"/timelines/{tl_id}/transitions/review")
    assert r.status_code == 200
    verdicts = r.json()["verdicts"]
    assert len(verdicts) == 1
    v = verdicts[0]
    assert v["label"] == "jump_cut" and v["boundary_seq_frame"] == 100
    assert v["suggested_fix"]["kind"] == "transition"
    assert v["suggested_fix"]["transition_style"] == "crossfade"


def test_apply_fix_transition_sets_crossfade(client: TestClient, db: Database) -> None:
    tl_id, c0, asset = _seed_rough_cut(db)
    r = client.post(
        f"/timelines/{tl_id}/transitions/apply-fix",
        json={
            "identity": {"asset_a": asset, "asset_b": asset, "src_out_a": 100, "src_in_b": 100},
            "fix": {"kind": "transition", "transition_style": "crossfade", "transition_frames": 6},
        },
    )
    assert r.status_code == 200 and r.json()["status"] == "ok"
    a = next(c for c in repos.list_timeline_clips(db, tl_id) if c["id"] == c0)
    assert a["transition_after_kind"] == "crossfade" and a["transition_after_frames"] == 6


def test_apply_fix_boundary_not_found(client: TestClient, db: Database) -> None:
    tl_id, _c0, asset = _seed_rough_cut(db)
    r = client.post(
        f"/timelines/{tl_id}/transitions/apply-fix",
        json={
            "identity": {"asset_a": asset, "asset_b": asset, "src_out_a": 999, "src_in_b": 100},
            "fix": {"kind": "resnap", "resnap_delta_frames": 5},
        },
    )
    assert r.status_code == 200 and r.json()["status"] == "error"


def test_apply_fix_unknown_timeline_404(client: TestClient) -> None:
    r = client.post(
        "/timelines/nope/transitions/apply-fix",
        json={
            "identity": {"asset_a": "A", "asset_b": "A", "src_out_a": 1, "src_in_b": 1},
            "fix": {"kind": "none"},
        },
    )
    assert r.status_code == 404
