"""Portion 16.4 — split/trim ops + wholesale set-clips (undo/redo restore)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.editing.operations import EditClip, ordered, split_clip, trim_clip
from laura.main import create_app


def _clip(
    seq_in: int, seq_out: int, src_in: int, src_out: int,
    speed_num: int = 1, speed_den: int = 1,
) -> EditClip:
    return EditClip(
        asset_id="a", src_in_frame=src_in, src_out_frame_exclusive=src_out,
        seq_in_frame=seq_in, seq_out_frame_exclusive=seq_out,
        speed_num=speed_num, speed_den=speed_den,
    )


def test_split_at_frame() -> None:
    out = ordered(split_clip([_clip(0, 30, 0, 30)], 10))
    assert len(out) == 2
    assert (out[0].seq_in_frame, out[0].seq_out_frame_exclusive) == (0, 10)
    assert (out[0].src_in_frame, out[0].src_out_frame_exclusive) == (0, 10)
    assert (out[1].seq_in_frame, out[1].seq_out_frame_exclusive) == (10, 30)
    assert (out[1].src_in_frame, out[1].src_out_frame_exclusive) == (10, 30)


def test_split_respects_speed() -> None:
    # 2x clip: seq [0,15) maps to src [0,30); split at seq 5 -> src 10
    out = ordered(split_clip([_clip(0, 15, 0, 30, speed_num=2)], 5))
    assert (out[0].seq_out_frame_exclusive, out[0].src_out_frame_exclusive) == (5, 10)
    assert (out[1].seq_in_frame, out[1].src_in_frame) == (5, 10)
    assert out[0].speed_num == 2 and out[1].speed_num == 2


def test_split_on_edge_raises() -> None:
    with pytest.raises(ValueError):
        split_clip([_clip(0, 30, 0, 30)], 0)
    with pytest.raises(ValueError):
        split_clip([_clip(0, 30, 0, 30)], 30)


def test_trim_shortens_and_ripples() -> None:
    out = ordered(trim_clip([_clip(0, 30, 0, 30), _clip(30, 60, 0, 30)], 0, 0, 15))
    assert (out[0].seq_in_frame, out[0].seq_out_frame_exclusive) == (0, 15)
    assert (out[0].src_in_frame, out[0].src_out_frame_exclusive) == (0, 15)
    assert (out[1].seq_in_frame, out[1].seq_out_frame_exclusive) == (15, 45)


def test_trim_empty_range_raises() -> None:
    with pytest.raises(ValueError):
        trim_clip([_clip(0, 30, 0, 30)], 0, 10, 10)


def _ctx(tmp_path: Path) -> tuple[SqliteDatabase, TestClient]:
    settings = Settings(workspace_root=tmp_path, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    client = TestClient(create_app(settings))
    client.__enter__()
    return db, client


def _timeline_with_clip(db: SqliteDatabase, client: TestClient) -> dict[str, Any]:
    p = client.post(
        "/projects", json={"name": "P", "sequence_rate_num": 30, "sequence_rate_den": 1}
    ).json()
    asset = repos.create_asset(
        db, project_id=p["id"], type="video", display_name="a.mov", source_path="a.mov"
    )
    tl: dict[str, Any] = client.post(
        f"/projects/{p['id']}/timelines", json={"name": "RC", "kind": "rough_cut"}
    ).json()
    client.post(
        f"/timelines/{tl['id']}/operations",
        json={"op": "append_clip", "asset_id": asset["id"],
              "src_in_frame": 0, "src_out_frame_exclusive": 30},
    )
    return tl


def test_split_and_trim_via_api(tmp_path: Path) -> None:
    db, client = _ctx(tmp_path)
    try:
        tl = _timeline_with_clip(db, client)
        split = client.post(
            f"/timelines/{tl['id']}/operations", json={"op": "split", "at_seq_frame": 12}
        )
        assert split.status_code == 200, split.text
        assert len(split.json()["clips"]) == 2

        trim = client.post(
            f"/timelines/{tl['id']}/operations",
            json={"op": "trim", "at_seq_frame": 0,
                  "new_src_in_frame": 0, "new_src_out_frame_exclusive": 6},
        )
        assert trim.status_code == 200, trim.text
        assert trim.json()["clips"][0]["seq_out_frame_exclusive"] == 6

        bad = client.post(
            f"/timelines/{tl['id']}/operations", json={"op": "split", "at_seq_frame": 0}
        )
        assert bad.status_code == 422
    finally:
        client.__exit__(None, None, None)


def test_set_clips_restores_snapshot(tmp_path: Path) -> None:
    db, client = _ctx(tmp_path)
    try:
        tl = _timeline_with_clip(db, client)
        snapshot = client.get(f"/timelines/{tl['id']}").json()["clips"]

        client.post(
            f"/timelines/{tl['id']}/operations",
            json={"op": "delete", "seq_in_frame": 0, "seq_out_frame_exclusive": 30},
        )
        assert client.get(f"/timelines/{tl['id']}").json()["clips"] == []

        restored = client.put(f"/timelines/{tl['id']}/clips", json={"clips": snapshot})
        assert restored.status_code == 200, restored.text
        clips = restored.json()["clips"]
        assert len(clips) == 1
        assert clips[0]["seq_out_frame_exclusive"] == 30
    finally:
        client.__exit__(None, None, None)
