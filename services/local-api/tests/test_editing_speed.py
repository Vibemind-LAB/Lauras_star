"""Portion 15.3 — speed/retiming: pure-ops golden deltas, OTIO round-trip, API, preflight."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.editing.operations import EditClip, append_clip, ordered, set_speed
from laura.interchange.otio_io import otio_string_to_timeline, timeline_to_otio_string
from laura.interchange.timeline import Clip, Timeline
from laura.interchange.validate import validate_export
from laura.main import create_app
from laura.timebase import retimed_seq_length


def _clip(seq_in: int, seq_out: int, src_in: int = 0, src_out: int = 30) -> EditClip:
    return EditClip(
        asset_id="a", src_in_frame=src_in, src_out_frame_exclusive=src_out,
        seq_in_frame=seq_in, seq_out_frame_exclusive=seq_out,
    )


def test_retimed_seq_length_projection() -> None:
    assert retimed_seq_length(30, 1, 1) == 30      # identity
    assert retimed_seq_length(30, 2, 1) == 15      # 2x faster -> half
    assert retimed_seq_length(30, 1, 2) == 60      # half speed -> double
    assert retimed_seq_length(5, 2, 1) == 2        # 2.5 -> HALF_EVEN -> 2
    with pytest.raises(ValueError):
        retimed_seq_length(30, 0, 1)


def test_set_speed_faster_shortens_and_ripples() -> None:
    out = ordered(set_speed([_clip(0, 30), _clip(30, 60)], 0, 2, 1))
    assert (out[0].seq_in_frame, out[0].seq_out_frame_exclusive) == (0, 15)
    assert (out[0].speed_num, out[0].speed_den) == (2, 1)
    assert (out[0].src_in_frame, out[0].src_out_frame_exclusive) == (0, 30)  # src unchanged
    assert (out[1].seq_in_frame, out[1].seq_out_frame_exclusive) == (15, 45)  # rippled left


def test_set_speed_slower_lengthens_and_ripples() -> None:
    out = ordered(set_speed([_clip(0, 30), _clip(30, 60)], 0, 1, 2))
    assert (out[0].seq_in_frame, out[0].seq_out_frame_exclusive) == (0, 60)
    assert (out[1].seq_in_frame, out[1].seq_out_frame_exclusive) == (60, 90)  # rippled right


def test_set_speed_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        set_speed([_clip(0, 30)], 999, 2, 1)   # no clip at that frame
    with pytest.raises(ValueError):
        set_speed([_clip(0, 30)], 0, 0, 1)     # non-positive speed


def test_append_then_speed_is_deterministic() -> None:
    clips = append_clip([], _clip(0, 0, src_in=0, src_out=40))  # appended -> seq [0,40)
    out = ordered(set_speed(clips, 0, 4, 1))                    # 4x -> 10 frames
    assert (out[0].seq_in_frame, out[0].seq_out_frame_exclusive) == (0, 10)


def test_otio_roundtrip_preserves_speed_and_source() -> None:
    tl = Timeline(
        name="RC", rate_num=30, rate_den=1,
        clips=[Clip("A", 0, 30, 0, 15, source_url="C:/a.mov", asset_id="a1",
                    speed_num=2, speed_den=1)],
    )
    text = timeline_to_otio_string(tl)
    assert "LinearTimeWarp" in text
    back = otio_string_to_timeline(text, rate_num=30, rate_den=1)
    c = back.clips[0]
    assert (c.src_in_frame, c.src_out_frame_exclusive) == (0, 30)   # true media preserved
    assert (c.seq_in_frame, c.seq_out_frame_exclusive) == (0, 15)
    assert (c.speed_num, c.speed_den) == (2, 1)


def test_otio_normal_clip_has_no_timewarp() -> None:
    tl = Timeline(
        name="RC", rate_num=30, rate_den=1,
        clips=[Clip("A", 0, 30, 0, 30, source_url="C:/a.mov", asset_id="a1")],
    )
    text = timeline_to_otio_string(tl)
    assert "LinearTimeWarp" not in text
    back = otio_string_to_timeline(text, rate_num=30, rate_den=1)
    assert (back.clips[0].speed_num, back.clips[0].speed_den) == (1, 1)


def test_edl_preflight_flags_speed() -> None:
    tl = Timeline(
        name="RC", rate_num=30, rate_den=1,
        clips=[Clip("A", 0, 30, 0, 15, source_url="C:/a.mov", speed_num=2, speed_den=1)],
    )
    diag = validate_export(tl, "edl")
    assert diag["lossy"] is True
    assert any("speed" in d.lower() for d in diag["drops"])


def _ctx(tmp_path: Path) -> tuple[SqliteDatabase, TestClient]:
    settings = Settings(workspace_root=tmp_path, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    client = TestClient(create_app(settings))
    client.__enter__()
    return db, client


def test_set_speed_via_api(tmp_path: Path) -> None:
    db, client = _ctx(tmp_path)
    try:
        p: dict[str, Any] = client.post(
            "/projects", json={"name": "P", "sequence_rate_num": 30, "sequence_rate_den": 1}
        ).json()
        asset = repos.create_asset(
            db, project_id=p["id"], type="video", display_name="a.mov", source_path="a.mov"
        )
        tl = client.post(
            f"/projects/{p['id']}/timelines", json={"name": "RC", "kind": "rough_cut"}
        ).json()
        for _ in range(2):
            client.post(
                f"/timelines/{tl['id']}/operations",
                json={"op": "append_clip", "asset_id": asset["id"],
                      "src_in_frame": 0, "src_out_frame_exclusive": 30},
            )

        resp = client.post(
            f"/timelines/{tl['id']}/operations",
            json={"op": "set_speed", "at_seq_frame": 0, "speed_num": 2, "speed_den": 1},
        )
        assert resp.status_code == 200, resp.text
        clips = resp.json()["clips"]
        assert clips[0]["speed_num"] == 2
        assert clips[0]["seq_out_frame_exclusive"] == 15
        assert clips[1]["seq_in_frame"] == 15
        assert clips[1]["seq_out_frame_exclusive"] == 45

        # missing speed args -> 422
        bad = client.post(
            f"/timelines/{tl['id']}/operations",
            json={"op": "set_speed", "at_seq_frame": 0},
        )
        assert bad.status_code == 422
    finally:
        client.__exit__(None, None, None)
