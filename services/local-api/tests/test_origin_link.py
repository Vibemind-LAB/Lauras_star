"""Portion 15.6 — origin_word anchors on clips + jump-back resolve endpoint."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app


def _ctx(tmp_path: Path) -> tuple[SqliteDatabase, TestClient]:
    settings = Settings(workspace_root=tmp_path, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    client = TestClient(create_app(settings))
    client.__enter__()
    return db, client


def _project(client: TestClient) -> dict[str, Any]:
    return dict(
        client.post(
            "/projects",
            json={"name": "P", "sequence_rate_num": 30, "sequence_rate_den": 1},
        ).json()
    )


def _seed_words(
    db: SqliteDatabase, asset_id: str, run_id: str
) -> tuple[str, list[dict[str, Any]]]:
    seg_id = repos.insert_segment_with_words(
        db, asset_id=asset_id, run_id=run_id, speaker_id=None,
        segment={"start_sample": 0, "end_sample": 48000, "start_frame": 0,
                 "end_frame": 30, "text": "Hallo schoene Welt", "confidence": 0.9},
        words=[
            {"idx": 0, "start_sample": 0, "end_sample": 16000,
             "start_frame": 0, "end_frame": 10, "text": "Hallo"},
            {"idx": 1, "start_sample": 16000, "end_sample": 32000,
             "start_frame": 10, "end_frame": 20, "text": "schoene"},
            {"idx": 2, "start_sample": 32000, "end_sample": 48000,
             "start_frame": 20, "end_frame": 30, "text": "Welt"},
        ],
    )
    return seg_id, repos.get_segment_words(db, seg_id)


def test_clip_exposes_origin_anchor_and_resolves(tmp_path: Path) -> None:
    db, client = _ctx(tmp_path)
    try:
        p = _project(client)
        asset = repos.create_asset(
            db, project_id=p["id"], type="video", display_name="a.mov", source_path="a.mov"
        )
        run = repos.create_analysis_run(
            db, asset_id=asset["id"], pipeline_version="1", config={}
        )
        seg_id, words = _seed_words(db, asset["id"], run["id"])
        tl = client.post(
            f"/projects/{p['id']}/timelines", json={"name": "RC", "kind": "rough_cut"}
        ).json()

        # build a clip from a word range -> sets origin_word_*
        resp = client.post(
            f"/timelines/{tl['id']}/operations",
            json={"op": "append_from_words",
                  "word_start_id": words[0]["id"], "word_end_id": words[2]["id"]},
        )
        assert resp.status_code == 200, resp.text
        clip = resp.json()["clips"][0]
        assert clip["origin_word_start_id"] == words[0]["id"]
        assert clip["origin_word_end_id"] == words[2]["id"]

        # resolve endpoint maps the clip back to its transcript segment + source frames
        src = client.get(f"/timelines/{tl['id']}/clips/{clip['id']}/source")
        assert src.status_code == 200, src.text
        body = src.json()
        assert body["asset_id"] == asset["id"]
        assert body["segment_id"] == seg_id
        assert body["word_start_frame"] == 0
        assert body["word_end_frame"] == 30
        assert body["src_in_frame"] == 0
        assert body["src_out_frame_exclusive"] == 30

        assert client.get(f"/timelines/{tl['id']}/clips/nope/source").status_code == 404
    finally:
        client.__exit__(None, None, None)


def test_manual_clip_has_no_origin(tmp_path: Path) -> None:
    db, client = _ctx(tmp_path)
    try:
        p = _project(client)
        asset = repos.create_asset(
            db, project_id=p["id"], type="video", display_name="a.mov", source_path="a.mov"
        )
        tl = client.post(
            f"/projects/{p['id']}/timelines", json={"name": "RC", "kind": "rough_cut"}
        ).json()
        resp = client.post(
            f"/timelines/{tl['id']}/operations",
            json={"op": "append_clip", "asset_id": asset["id"],
                  "src_in_frame": 0, "src_out_frame_exclusive": 15},
        )
        clip = resp.json()["clips"][0]
        assert clip["origin_word_start_id"] is None

        src = client.get(f"/timelines/{tl['id']}/clips/{clip['id']}/source").json()
        assert src["segment_id"] is None
        assert src["word_start_frame"] is None
        assert src["src_in_frame"] == 0
        assert src["src_out_frame_exclusive"] == 15
    finally:
        client.__exit__(None, None, None)
