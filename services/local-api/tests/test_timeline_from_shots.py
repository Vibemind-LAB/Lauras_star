"""API test: build a rough cut from an asset's detected shots (one clip per scene)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app


def _setup(tmp_path: Path) -> tuple[TestClient, SqliteDatabase, dict[str, Any], dict[str, Any]]:
    settings = Settings(workspace_root=tmp_path, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    client = TestClient(create_app(settings))
    client.__enter__()
    project = client.post(
        "/projects", json={"name": "p", "sequence_rate_num": 30, "sequence_rate_den": 1}
    ).json()
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a.mov", source_path="a.mov"
    )
    return client, db, project, asset


def _add_run_with_shots(db: SqliteDatabase, asset_id: str) -> str:
    run = repos.create_analysis_run(
        db, asset_id=asset_id, pipeline_version="1", config={"stages": {}}
    )
    # Source ranges deliberately have a gap (60->100) to prove the sequence is packed
    # contiguously regardless of where the scenes sit in the source.
    repos.insert_shots(
        db, asset_id=asset_id, run_id=run["id"],
        shots=[
            {"src_in_frame": 10, "src_out_frame_exclusive": 60, "method": "test"},
            {"src_in_frame": 100, "src_out_frame_exclusive": 170, "method": "test"},
            {"src_in_frame": 170, "src_out_frame_exclusive": 200, "method": "test"},
        ],
    )
    return str(run["id"])


def test_from_shots_builds_one_contiguous_clip_per_scene(tmp_path: Path) -> None:
    client, db, project, asset = _setup(tmp_path)
    try:
        _add_run_with_shots(db, asset["id"])
        resp = client.post(
            f"/projects/{project['id']}/timelines/from-shots",
            json={"asset_id": asset["id"]},
        )
        assert resp.status_code == 201, resp.text
        clips = resp.json()["timeline"]["clips"]
        # one clip per shot, source ranges preserved, sequence packed back-to-back
        assert [(c["src_in_frame"], c["src_out_frame_exclusive"]) for c in clips] == [
            (10, 60), (100, 170), (170, 200),
        ]
        assert [(c["seq_in_frame"], c["seq_out_frame_exclusive"]) for c in clips] == [
            (0, 50), (50, 120), (120, 150),
        ]
        assert all(c["speed_num"] == 1 and c["speed_den"] == 1 for c in clips)
        # OTIO regenerated and persisted
        tl = repos.get_timeline(db, resp.json()["timeline"]["id"])
        assert tl is not None and "OTIO_SCHEMA" in tl["otio_json"]
    finally:
        client.__exit__(None, None, None)


def test_from_shots_fills_empty_timeline_but_refuses_non_empty(tmp_path: Path) -> None:
    client, db, project, asset = _setup(tmp_path)
    try:
        _add_run_with_shots(db, asset["id"])
        empty = client.post(
            f"/projects/{project['id']}/timelines", json={"name": "RC", "kind": "rough_cut"}
        ).json()
        # fills the existing empty rough cut (same id, now populated)
        filled = client.post(
            f"/projects/{project['id']}/timelines/from-shots",
            json={"asset_id": asset["id"], "timeline_id": empty["id"]},
        )
        assert filled.status_code == 201, filled.text
        assert filled.json()["timeline"]["id"] == empty["id"]
        assert len(filled.json()["timeline"]["clips"]) == 3
        # refuses to clobber a non-empty timeline
        again = client.post(
            f"/projects/{project['id']}/timelines/from-shots",
            json={"asset_id": asset["id"], "timeline_id": empty["id"]},
        )
        assert again.status_code == 422
    finally:
        client.__exit__(None, None, None)


def test_from_shots_aligns_clip_in_to_word_gap(tmp_path: Path) -> None:
    """A shot boundary that bisects a spoken word is snapped to the nearest word gap, and
    the two clips stay source-contiguous and end-exclusive after the move."""
    client, db, project, asset = _setup(tmp_path)
    try:
        run = repos.create_analysis_run(
            db, asset_id=asset["id"], pipeline_version="ed", config={"stages": {}}
        )
        # Two source-contiguous shots sharing a cut at frame 50.
        repos.insert_shots(
            db, asset_id=asset["id"], run_id=run["id"],
            shots=[
                {"src_in_frame": 0, "src_out_frame_exclusive": 50, "method": "t"},
                {"src_in_frame": 50, "src_out_frame_exclusive": 120, "method": "t"},
            ],
        )
        # Transcript: a silence ends at frame 48, then a word [48,60) straddles the cut at 50.
        repos.insert_segment_with_words(
            db, asset_id=asset["id"], run_id=run["id"], speaker_id=None,
            segment={"start_sample": 0, "end_sample": 1, "start_frame": 20,
                     "end_frame": 75, "text": "alpha straddle omega"},
            words=[
                {"idx": 0, "start_sample": 0, "end_sample": 1,
                 "start_frame": 20, "end_frame": 40, "text": "alpha"},
                {"idx": 1, "start_sample": 1, "end_sample": 2,
                 "start_frame": 48, "end_frame": 60, "text": "straddle"},  # covers frame 50
                {"idx": 2, "start_sample": 2, "end_sample": 3,
                 "start_frame": 60, "end_frame": 75, "text": "omega"},
            ],
        )
        resp = client.post(
            f"/projects/{project['id']}/timelines/from-shots",
            json={"asset_id": asset["id"]},  # align_editorial defaults to True
        )
        assert resp.status_code == 201, resp.text
        clips = resp.json()["timeline"]["clips"]
        # The shared cut moved off frame 50 (mid-word) onto the gap edge 48.
        assert [(c["src_in_frame"], c["src_out_frame_exclusive"]) for c in clips] == [
            (0, 48), (48, 120),
        ]
        # Sequence repacked back-to-back, contiguous and end-exclusive.
        assert [(c["seq_in_frame"], c["seq_out_frame_exclusive"]) for c in clips] == [
            (0, 48), (48, 120),
        ]
        for a, b in zip(clips, clips[1:], strict=False):
            assert a["seq_out_frame_exclusive"] == b["seq_in_frame"]
        # First clip's start (frame 0) is never touched.
        assert clips[0]["src_in_frame"] == 0
    finally:
        client.__exit__(None, None, None)


def test_from_shots_align_editorial_false_keeps_visual_cut(tmp_path: Path) -> None:
    """With ``align_editorial=False`` the visual frame is kept even when a word straddles it."""
    client, db, project, asset = _setup(tmp_path)
    try:
        run = repos.create_analysis_run(
            db, asset_id=asset["id"], pipeline_version="ed2", config={"stages": {}}
        )
        repos.insert_shots(
            db, asset_id=asset["id"], run_id=run["id"],
            shots=[
                {"src_in_frame": 0, "src_out_frame_exclusive": 50, "method": "t"},
                {"src_in_frame": 50, "src_out_frame_exclusive": 120, "method": "t"},
            ],
        )
        repos.insert_segment_with_words(
            db, asset_id=asset["id"], run_id=run["id"], speaker_id=None,
            segment={"start_sample": 0, "end_sample": 1, "start_frame": 48,
                     "end_frame": 60, "text": "straddle"},
            words=[
                {"idx": 0, "start_sample": 0, "end_sample": 1,
                 "start_frame": 48, "end_frame": 60, "text": "straddle"},
            ],
        )
        resp = client.post(
            f"/projects/{project['id']}/timelines/from-shots",
            json={"asset_id": asset["id"], "align_editorial": False},
        )
        assert resp.status_code == 201, resp.text
        clips = resp.json()["timeline"]["clips"]
        assert [(c["src_in_frame"], c["src_out_frame_exclusive"]) for c in clips] == [
            (0, 50), (50, 120),
        ]
    finally:
        client.__exit__(None, None, None)


def test_from_shots_422_when_no_analysis_run(tmp_path: Path) -> None:
    client, _db, project, asset = _setup(tmp_path)
    try:
        resp = client.post(
            f"/projects/{project['id']}/timelines/from-shots",
            json={"asset_id": asset["id"]},
        )
        assert resp.status_code == 422
        assert "no analysis run" in resp.text
    finally:
        client.__exit__(None, None, None)


def test_from_shots_drops_weak_and_reports_them(tmp_path: Path) -> None:
    client, db, project, asset = _setup(tmp_path)
    try:
        run = repos.create_analysis_run(
            db, asset_id=asset["id"], pipeline_version="2", config={"stages": {}}
        )
        repos.insert_shots(
            db, asset_id=asset["id"], run_id=run["id"],
            shots=[
                {"src_in_frame": 0, "src_out_frame_exclusive": 50, "method": "t",
                 "keep": True, "drop_reason": None},
                {"src_in_frame": 50, "src_out_frame_exclusive": 90, "method": "t",
                 "keep": False, "drop_reason": "black"},
                {"src_in_frame": 90, "src_out_frame_exclusive": 160, "method": "t",
                 "keep": True, "drop_reason": None},
            ],
        )
        resp = client.post(
            f"/projects/{project['id']}/timelines/from-shots",
            json={"asset_id": asset["id"], "quality": True},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        # only the two kept shots, packed contiguously
        clips = body["timeline"]["clips"]
        assert [(c["seq_in_frame"], c["seq_out_frame_exclusive"]) for c in clips] == [
            (0, 50), (50, 120),
        ]
        assert body["dropped"] == [{"src_in_frame": 50, "src_out_frame_exclusive": 90,
                                     "drop_reason": "black"}]
    finally:
        client.__exit__(None, None, None)
