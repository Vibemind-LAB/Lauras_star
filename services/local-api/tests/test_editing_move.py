"""Tests for move_clip: drag-reorder with contiguous re-pack.

Unit tests (pure ops) + one API integration test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.editing.operations import EditClip, move_clip, ordered
from laura.main import create_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clip(
    asset: str,
    src_in: int,
    src_out: int,
    seq_in: int,
    seq_out: int,
    speed_num: int = 1,
    speed_den: int = 1,
) -> EditClip:
    return EditClip(
        asset_id=asset,
        src_in_frame=src_in,
        src_out_frame_exclusive=src_out,
        seq_in_frame=seq_in,
        seq_out_frame_exclusive=seq_out,
        speed_num=speed_num,
        speed_den=speed_den,
    )


def _three() -> list[EditClip]:
    """Three contiguous clips: A[0,10), B[10,25), C[25,40).
    Different lengths so positional mis-packing is visible.
    All from same asset with distinct non-overlapping src ranges."""
    return [
        _clip("asset", 0, 10, 0, 10),    # A: length 10
        _clip("asset", 20, 35, 10, 25),  # B: length 15
        _clip("asset", 40, 55, 25, 40),  # C: length 15
    ]


# ---------------------------------------------------------------------------
# Pure operation tests
# ---------------------------------------------------------------------------


def test_move_first_to_end() -> None:
    """Moving A (seq_in=0) to the end rotates order to B, C, A."""
    clips = _three()
    result = ordered(move_clip(clips, at_seq_frame=0, to_seq_frame=40))
    assert len(result) == 3

    # Order: B, C, A
    assert result[0].src_in_frame == 20   # B
    assert result[1].src_in_frame == 40   # C
    assert result[2].src_in_frame == 0    # A

    # Sequence must be re-packed contiguously from 0
    assert (result[0].seq_in_frame, result[0].seq_out_frame_exclusive) == (0, 15)
    assert (result[1].seq_in_frame, result[1].seq_out_frame_exclusive) == (15, 30)
    assert (result[2].seq_in_frame, result[2].seq_out_frame_exclusive) == (30, 40)


def test_move_last_to_front() -> None:
    """Moving C (seq_in=25) to the front (to_seq_frame=0) gives C, A, B."""
    clips = _three()
    result = ordered(move_clip(clips, at_seq_frame=25, to_seq_frame=0))
    assert len(result) == 3

    # Order: C, A, B
    assert result[0].src_in_frame == 40  # C
    assert result[1].src_in_frame == 0   # A
    assert result[2].src_in_frame == 20  # B

    # Re-packed lengths: C=15, A=10, B=15 → 0,15,25,40
    assert (result[0].seq_in_frame, result[0].seq_out_frame_exclusive) == (0, 15)
    assert (result[1].seq_in_frame, result[1].seq_out_frame_exclusive) == (15, 25)
    assert (result[2].seq_in_frame, result[2].seq_out_frame_exclusive) == (25, 40)


def test_move_middle_to_end() -> None:
    """Moving B (seq_in=10) to the end gives A, C, B."""
    clips = _three()
    result = ordered(move_clip(clips, at_seq_frame=10, to_seq_frame=40))
    assert len(result) == 3

    # Order: A, C, B
    assert result[0].src_in_frame == 0   # A
    assert result[1].src_in_frame == 40  # C
    assert result[2].src_in_frame == 20  # B

    # Re-packed: A=10, C=15, B=15 → 0,10,25,40
    assert (result[0].seq_in_frame, result[0].seq_out_frame_exclusive) == (0, 10)
    assert (result[1].seq_in_frame, result[1].seq_out_frame_exclusive) == (10, 25)
    assert (result[2].seq_in_frame, result[2].seq_out_frame_exclusive) == (25, 40)


def test_move_noop_within_own_span() -> None:
    """to_seq_frame within the moved clip's own span: target=0 after pop, lands back at front."""
    clips = _three()
    # A occupies [0, 10). to_seq_frame=5 is inside A's range.
    # After popping A, remaining = [B[10,25), C[25,40)].
    # target = sum(1 for c in remaining if c.seq_in_frame < 5) = 0 → inserts at front.
    result = ordered(move_clip(clips, at_seq_frame=0, to_seq_frame=5))
    assert len(result) == 3

    # A stays first (target index 0)
    assert result[0].src_in_frame == 0   # A
    assert result[1].src_in_frame == 20  # B
    assert result[2].src_in_frame == 40  # C

    # Re-packed contiguous (same order as original, so identical positions)
    assert (result[0].seq_in_frame, result[0].seq_out_frame_exclusive) == (0, 10)
    assert (result[1].seq_in_frame, result[1].seq_out_frame_exclusive) == (10, 25)
    assert (result[2].seq_in_frame, result[2].seq_out_frame_exclusive) == (25, 40)


def test_move_preserves_source_ranges_and_speed() -> None:
    """Source ranges, asset ids, and speed ratios must be unchanged after a move."""
    clips = [
        _clip("vid1", 100, 200, 0, 10, speed_num=2, speed_den=1),
        _clip("vid2", 300, 340, 10, 25, speed_num=1, speed_den=2),
        _clip("vid3", 0, 15, 25, 40),
    ]
    result = ordered(move_clip(clips, at_seq_frame=0, to_seq_frame=40))
    # Order should be vid2, vid3, vid1
    by_asset = {c.asset_id: c for c in result}
    assert by_asset["vid1"].src_in_frame == 100
    assert by_asset["vid1"].src_out_frame_exclusive == 200
    assert by_asset["vid1"].speed_num == 2
    assert by_asset["vid1"].speed_den == 1
    assert by_asset["vid2"].src_in_frame == 300
    assert by_asset["vid2"].src_out_frame_exclusive == 340
    assert by_asset["vid2"].speed_num == 1
    assert by_asset["vid2"].speed_den == 2
    assert by_asset["vid3"].src_in_frame == 0
    assert by_asset["vid3"].src_out_frame_exclusive == 15


def test_move_sequence_is_contiguous_from_zero() -> None:
    """After any move the re-packed sequence starts at 0 and has no gaps."""
    clips = _three()
    result = ordered(move_clip(clips, at_seq_frame=10, to_seq_frame=0))
    # Walk and verify back-to-back
    prev_out = 0
    for c in result:
        assert c.seq_in_frame == prev_out
        prev_out = c.seq_out_frame_exclusive
    assert prev_out == 40  # total length unchanged (10+15+15)


def test_move_does_not_mutate_input() -> None:
    """Original clip list must be untouched after move_clip."""
    clips = _three()
    originals = [(c.seq_in_frame, c.seq_out_frame_exclusive) for c in clips]
    move_clip(clips, at_seq_frame=0, to_seq_frame=40)
    assert [(c.seq_in_frame, c.seq_out_frame_exclusive) for c in clips] == originals


def test_move_missing_at_seq_frame_raises() -> None:
    """ValueError when no clip starts at at_seq_frame."""
    clips = _three()
    with pytest.raises(ValueError, match="no clip starts at 99"):
        move_clip(clips, at_seq_frame=99, to_seq_frame=0)


def test_move_empty_list_raises() -> None:
    """move_clip on an empty list raises ValueError."""
    with pytest.raises(ValueError):
        move_clip([], at_seq_frame=0, to_seq_frame=0)


# ---------------------------------------------------------------------------
# API integration test
# ---------------------------------------------------------------------------


def _ctx(tmp_path: Path) -> tuple[SqliteDatabase, TestClient]:
    settings = Settings(workspace_root=tmp_path, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    client = TestClient(create_app(settings))
    client.__enter__()
    return db, client


def _make_timeline_three_clips(
    db: SqliteDatabase, client: TestClient
) -> tuple[str, str]:
    """Create a project + asset + timeline with three back-to-back clips.

    Returns (project_id, timeline_id).
    Clip layout: A[0,10), B[10,25), C[25,40) (lengths 10, 15, 15).
    """
    p: dict[str, Any] = client.post(
        "/projects", json={"name": "MoveTest", "sequence_rate_num": 30, "sequence_rate_den": 1}
    ).json()
    asset = repos.create_asset(
        db, project_id=p["id"], type="video", display_name="clip.mov", source_path="clip.mov"
    )
    tl: dict[str, Any] = client.post(
        f"/projects/{p['id']}/timelines", json={"name": "RC", "kind": "rough_cut"}
    ).json()
    tid = tl["id"]
    # Clip A: src 0..10, seq auto-placed at 0..10
    client.post(
        f"/timelines/{tid}/operations",
        json={"op": "append_clip", "asset_id": asset["id"],
              "src_in_frame": 0, "src_out_frame_exclusive": 10},
    )
    # Clip B: src 20..35 (length 15), seq 10..25
    client.post(
        f"/timelines/{tid}/operations",
        json={"op": "append_clip", "asset_id": asset["id"],
              "src_in_frame": 20, "src_out_frame_exclusive": 35},
    )
    # Clip C: src 40..55 (length 15), seq 25..40
    client.post(
        f"/timelines/{tid}/operations",
        json={"op": "append_clip", "asset_id": asset["id"],
              "src_in_frame": 40, "src_out_frame_exclusive": 55},
    )
    return p["id"], tid


def test_move_via_api_reorders_and_repacks(tmp_path: Path) -> None:
    """POST op:move moves A to end → returns reordered B,C,A with contiguous seq positions
    and regenerates OTIO."""
    db, client = _ctx(tmp_path)
    try:
        _proj_id, tid = _make_timeline_three_clips(db, client)

        resp = client.post(
            f"/timelines/{tid}/operations",
            json={"op": "move", "at_seq_frame": 0, "to_seq_frame": 40},
        )
        assert resp.status_code == 200, resp.text

        clips = resp.json()["clips"]
        assert len(clips) == 3

        # Re-packed positions: B=15, C=15, A=10 → [0,15), [15,30), [30,40)
        seq_pairs = [(c["seq_in_frame"], c["seq_out_frame_exclusive"]) for c in clips]
        assert seq_pairs == [(0, 15), (15, 30), (30, 40)]

        # Order by src_in to verify clip identity: B(20), C(40), A(0)
        by_seq = {c["seq_in_frame"]: c for c in clips}
        assert by_seq[0]["src_in_frame"] == 20   # B first
        assert by_seq[15]["src_in_frame"] == 40  # C second
        assert by_seq[30]["src_in_frame"] == 0   # A last

        # OTIO regenerated
        tl_row = repos.get_timeline(db, tid)
        assert tl_row is not None
        assert "OTIO_SCHEMA" in tl_row["otio_json"]
    finally:
        client.__exit__(None, None, None)


def test_move_via_api_missing_clip_returns_422(tmp_path: Path) -> None:
    """op:move with a non-existent at_seq_frame returns 422."""
    db, client = _ctx(tmp_path)
    try:
        _proj_id, tid = _make_timeline_three_clips(db, client)

        resp = client.post(
            f"/timelines/{tid}/operations",
            json={"op": "move", "at_seq_frame": 999, "to_seq_frame": 0},
        )
        assert resp.status_code == 422
    finally:
        client.__exit__(None, None, None)


def test_move_via_api_missing_fields_returns_422(tmp_path: Path) -> None:
    """op:move without to_seq_frame returns 422."""
    db, client = _ctx(tmp_path)
    try:
        _proj_id, tid = _make_timeline_three_clips(db, client)

        resp = client.post(
            f"/timelines/{tid}/operations",
            json={"op": "move", "at_seq_frame": 0},
        )
        assert resp.status_code == 422
    finally:
        client.__exit__(None, None, None)
