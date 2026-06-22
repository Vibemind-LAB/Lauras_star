"""Task A2: delete_words op reconciles scene markers and returns them in the response."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app

_TOKEN = "test-token"
_H = {"X-Laura-Token": _TOKEN}


def _make_client(tmp_path: Path) -> tuple[TestClient, SqliteDatabase]:
    settings = Settings(workspace_root=tmp_path / "ws", token=_TOKEN, start_runner=False)
    app = create_app(settings)
    db: SqliteDatabase = app.state.db
    return TestClient(app), db


@pytest.fixture
def client_with_scenes(
    tmp_path: Path,
) -> tuple[TestClient, str, list[dict[str, Any]]]:
    """Build a project + asset + rough_cut with 3 clips (30 frames each), 2 scenes,
    and a transcript with 4 words spread across the first two clips.

    Returns ``(client, timeline_id, words)`` where words is an ordered list of dicts
    each containing at least ``id``, ``start_frame``, ``end_frame``.
    Scenes: scene 1 = [0..60), scene 2 = [60..90).
    Words (in src frames, all in the same asset):
        word 0: src 5..10   -> seq 5..10   (inside scene 1)
        word 1: src 12..17  -> seq 12..17  (inside scene 1)
        word 2: src 20..25  -> seq 20..25  (inside scene 1)
        word 3: src 62..67  -> seq 62..67  (inside scene 2)
    """
    client, db = _make_client(tmp_path)

    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="a",
        source_path="/tmp/a.mp4",
    )
    run = repos.create_analysis_run(db, asset_id=asset["id"], pipeline_version="t", config={})

    # Insert a transcript segment with 4 words.
    repos.insert_segment_with_words(
        db,
        asset_id=asset["id"],
        run_id=run["id"],
        speaker_id=None,
        segment={
            "start_sample": 0,
            "end_sample": 0,
            "start_frame": 5,
            "end_frame": 70,
            "text": "one two three four",
            "confidence": 1.0,
        },
        words=[
            {
                "idx": 0,
                "start_sample": 0,
                "end_sample": 0,
                "start_frame": 5,
                "end_frame": 10,
                "text": "one",
                "confidence": 1.0,
                "is_punctuation": False,
            },
            {
                "idx": 1,
                "start_sample": 0,
                "end_sample": 0,
                "start_frame": 12,
                "end_frame": 17,
                "text": "two",
                "confidence": 1.0,
                "is_punctuation": False,
            },
            {
                "idx": 2,
                "start_sample": 0,
                "end_sample": 0,
                "start_frame": 20,
                "end_frame": 25,
                "text": "three",
                "confidence": 1.0,
                "is_punctuation": False,
            },
            {
                "idx": 3,
                "start_sample": 0,
                "end_sample": 0,
                "start_frame": 62,
                "end_frame": 67,
                "text": "four",
                "confidence": 1.0,
                "is_punctuation": False,
            },
        ],
    )

    # Build a rough_cut timeline with 3 clips (each 30 frames, back-to-back, src == seq).
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.replace_timeline_clips(
        db,
        tl["id"],
        [
            {
                "asset_id": asset["id"],
                "src_in_frame": 0,
                "src_out_frame_exclusive": 30,
                "seq_in_frame": 0,
                "seq_out_frame_exclusive": 30,
                "lane": 0,
                "speed_num": 1,
                "speed_den": 1,
            },
            {
                "asset_id": asset["id"],
                "src_in_frame": 30,
                "src_out_frame_exclusive": 60,
                "seq_in_frame": 30,
                "seq_out_frame_exclusive": 60,
                "lane": 0,
                "speed_num": 1,
                "speed_den": 1,
            },
            {
                "asset_id": asset["id"],
                "src_in_frame": 60,
                "src_out_frame_exclusive": 90,
                "seq_in_frame": 60,
                "seq_out_frame_exclusive": 90,
                "lane": 0,
                "speed_num": 1,
                "speed_den": 1,
            },
        ],
    )

    # Seed 2 scenes directly via replace_scenes: scene 1 = [0..60), scene 2 = [60..90).
    repos.replace_scenes(
        db,
        project["id"],
        tl["id"],
        [(0, 60), (60, 90)],
    )

    # Fetch word ids in order.
    transcript = repos.get_transcript(db, asset["id"], run["id"])
    assert transcript, "expected at least one segment"
    words = transcript[0]["words"]
    assert len(words) == 4

    return client, tl["id"], words


def test_delete_words_reconciles_scene_bounds_and_returns_scenes(
    client_with_scenes: tuple[TestClient, str, list[dict[str, Any]]],
) -> None:
    api, timeline_id, words = client_with_scenes

    before = api.get(f"/timelines/{timeline_id}/scenes", headers=_H).json()
    assert len(before) >= 2

    # Delete words[1] and words[2] (src 12..17 and 20..25 -> seq span 12..25, 13 frames).
    w0, w1 = words[1]["id"], words[2]["id"]
    resp = api.post(
        f"/timelines/{timeline_id}/operations",
        json={"op": "delete_words", "word_start_id": w0, "word_end_id": w1},
        headers=_H,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # The response must include a "scenes" key.
    assert "scenes" in body, f"'scenes' missing from response: {list(body.keys())}"
    after = body["scenes"]

    # Same scene count — a within-scene delete keeps all scene boundaries.
    assert len(after) == len(before)

    # The sequence is shorter: last scene's out_frame_exclusive must be less than before.
    total_before = before[-1]["seq_out_frame_exclusive"]
    total_after = after[-1]["seq_out_frame_exclusive"]
    assert total_after < total_before, (
        f"expected sequence to shrink: before={total_before}, after={total_after}"
    )

    # Persisted: a re-GET must match the reconciled bounds.
    reget = api.get(f"/timelines/{timeline_id}/scenes", headers=_H).json()
    assert [(s["seq_in_frame"], s["seq_out_frame_exclusive"]) for s in reget] == [
        (s["seq_in_frame"], s["seq_out_frame_exclusive"]) for s in after
    ], "re-GET scenes do not match the scenes returned in the op response"
