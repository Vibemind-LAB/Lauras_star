"""Auto-transitions endpoint (#1) — apply the transition-review heuristic to all boundaries.

``POST /timelines/{id}/auto-transitions`` runs the ``StubVlmBackend`` heuristic over every
lane-0 boundary and applies each suggested crossfade, skipping boundaries that already carry a
manual (non-hard) transition, wrapped in an undo checkpoint. Pure reuse of ``transition_review``
(same convention as "Übergänge prüfen") — no second, competing rule set.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from laura.db import repos
from laura.db.database import Database


def _seed_two_contiguous(db: Database) -> str:
    """rough_cut with two contiguous same-source clips → one jump-cut boundary. Returns tl_id."""
    project = repos.create_project(
        db, name="P", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/ws"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a.mov", source_path="/a.mov"
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.add_timeline_clip(
        db, timeline_id=tl["id"], asset_id=asset["id"],
        src_in_frame=0, src_out_frame_exclusive=100,
        seq_in_frame=0, seq_out_frame_exclusive=100,
    )
    repos.add_timeline_clip(
        db, timeline_id=tl["id"], asset_id=asset["id"],
        src_in_frame=100, src_out_frame_exclusive=200,
        seq_in_frame=100, seq_out_frame_exclusive=200,
    )
    return str(tl["id"])


def test_auto_transitions_sets_crossfade_on_contiguous_boundary(
    client: TestClient, db: Database
) -> None:
    tl_id = _seed_two_contiguous(db)
    clips0 = repos.list_timeline_clips(db, tl_id)
    assert all(c["transition_after_kind"] == "hard" for c in clips0)

    r = client.post(f"/timelines/{tl_id}/auto-transitions")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["boundaries"] == 1
    assert body["applied"] == 1

    clips = sorted(repos.list_timeline_clips(db, tl_id), key=lambda c: c["seq_in_frame"])
    assert clips[0]["transition_after_kind"] == "crossfade"
    assert clips[0]["transition_after_frames"] == 8

    hist = client.get(f"/timelines/{tl_id}/history").json()
    assert hist["can_undo"] is True
    assert hist["undo_label"] == "Übergänge automatisch gesetzt"


def test_auto_transitions_preserves_manual_transition(
    client: TestClient, db: Database
) -> None:
    tl_id = _seed_two_contiguous(db)
    clips = sorted(repos.list_timeline_clips(db, tl_id), key=lambda c: c["seq_in_frame"])
    repos.set_clip_transition(db, clip_id=str(clips[0]["id"]), kind="fade", frames=15)

    r = client.post(f"/timelines/{tl_id}/auto-transitions")
    assert r.status_code == 200, r.text
    assert r.json()["applied"] == 0  # manual transition preserved

    a = sorted(repos.list_timeline_clips(db, tl_id), key=lambda c: c["seq_in_frame"])[0]
    assert a["transition_after_kind"] == "fade"
    assert a["transition_after_frames"] == 15


def test_auto_transitions_is_idempotent(client: TestClient, db: Database) -> None:
    tl_id = _seed_two_contiguous(db)
    assert client.post(f"/timelines/{tl_id}/auto-transitions").json()["applied"] == 1
    assert client.post(f"/timelines/{tl_id}/auto-transitions").json()["applied"] == 0


def test_auto_transitions_unknown_timeline_404(client: TestClient) -> None:
    assert client.post("/timelines/nope/auto-transitions").status_code == 404
