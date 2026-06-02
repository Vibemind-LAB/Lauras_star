"""Portion 15.4 — timeline captions: SRT/VTT from the rough cut at sequence positions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.interchange.captions import join_words
from laura.main import create_app


def _ctx(tmp_path: Path) -> tuple[SqliteDatabase, TestClient]:
    settings = Settings(workspace_root=tmp_path, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    client = TestClient(create_app(settings))
    client.__enter__()
    return db, client


def _seed(
    db: SqliteDatabase, client: TestClient
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    p: dict[str, Any] = client.post(
        "/projects", json={"name": "P", "sequence_rate_num": 30, "sequence_rate_den": 1}
    ).json()
    asset = repos.create_asset(
        db, project_id=p["id"], type="video", display_name="a.mov", source_path="a.mov"
    )
    run = repos.create_analysis_run(
        db, asset_id=asset["id"], pipeline_version="1", config={}
    )
    seg_id = repos.insert_segment_with_words(
        db, asset_id=asset["id"], run_id=run["id"], speaker_id=None,
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
    return p, repos.get_segment_words(db, seg_id)


def test_join_words_no_space_before_punctuation() -> None:
    words = [
        {"text": "Hallo", "is_punctuation": False},
        {"text": "Welt", "is_punctuation": False},
        {"text": "!", "is_punctuation": True},
    ]
    assert join_words(words) == "Hallo Welt!"


def test_timeline_srt_uses_sequence_positions(tmp_path: Path) -> None:
    db, client = _ctx(tmp_path)
    try:
        p, words = _seed(db, client)
        tl = client.post(
            f"/projects/{p['id']}/timelines", json={"name": "RC", "kind": "rough_cut"}
        ).json()
        body = {"op": "append_from_words",
                "word_start_id": words[0]["id"], "word_end_id": words[2]["id"]}
        client.post(f"/timelines/{tl['id']}/operations", json=body)
        client.post(f"/timelines/{tl['id']}/operations", json=body)  # second clip -> [30,60)

        srt = client.get(f"/timelines/{tl['id']}/captions.srt")
        assert srt.status_code == 200
        text = srt.text
        assert text.count("Hallo schoene Welt") == 2
        # timed at SEQUENCE positions (30 fps): 0-1s and 1-2s, not source frames
        assert "00:00:00,000 --> 00:00:01,000" in text
        assert "00:00:01,000 --> 00:00:02,000" in text
    finally:
        client.__exit__(None, None, None)


def test_timeline_vtt_and_manual_clip_excluded(tmp_path: Path) -> None:
    db, client = _ctx(tmp_path)
    try:
        p, words = _seed(db, client)
        asset = repos.list_assets(db, p["id"])[0]
        tl = client.post(
            f"/projects/{p['id']}/timelines", json={"name": "RC", "kind": "rough_cut"}
        ).json()
        client.post(
            f"/timelines/{tl['id']}/operations",
            json={"op": "append_from_words",
                  "word_start_id": words[0]["id"], "word_end_id": words[1]["id"]},
        )
        client.post(  # manual clip -> no origin words -> not captioned
            f"/timelines/{tl['id']}/operations",
            json={"op": "append_clip", "asset_id": asset["id"],
                  "src_in_frame": 0, "src_out_frame_exclusive": 15},
        )
        vtt = client.get(f"/timelines/{tl['id']}/captions.vtt")
        assert vtt.status_code == 200
        assert vtt.text.startswith("WEBVTT")
        assert vtt.text.count("Hallo schoene") == 1
    finally:
        client.__exit__(None, None, None)


def test_empty_timeline_captions(tmp_path: Path) -> None:
    db, client = _ctx(tmp_path)
    try:
        p, _ = _seed(db, client)
        tl = client.post(
            f"/projects/{p['id']}/timelines", json={"name": "RC", "kind": "rough_cut"}
        ).json()
        srt = client.get(f"/timelines/{tl['id']}/captions.srt")
        assert srt.status_code == 200
        assert srt.text.strip() == ""
        assert client.get("/timelines/nope/captions.srt").status_code == 404
    finally:
        client.__exit__(None, None, None)
