"""Sequence transcript + transcript realignment API."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from laura.analysis.types import SegmentResult, WordResult
from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app
from laura.scenes.materialize import materialize_scene

_TOKEN = "test-token"
_H = {"X-Laura-Token": _TOKEN}


def _app(tmp_path: Path) -> tuple[TestClient, SqliteDatabase]:
    settings = Settings(workspace_root=tmp_path / "ws", token=_TOKEN, start_runner=False)
    app = create_app(settings)
    db: SqliteDatabase = app.state.db
    return TestClient(app), db


def _seed_sequence_with_transcript(db: SqliteDatabase, tmp_path: Path) -> dict[str, str]:
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root=str(tmp_path / "p")
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a", source_path="/tmp/a.mp4"
    )
    repos.update_asset_probe(
        db,
        asset["id"],
        type="video",
        duration_frames=120,
        rate_num=30,
        rate_den=1,
        audio_sample_rate=48_000,
        start_timecode=None,
        width=1920,
        height=1080,
        codec_video="h264",
        codec_audio="aac",
        is_vfr=False,
        sha256=None,
    )
    run = repos.create_analysis_run(db, asset_id=asset["id"], pipeline_version="1", config={})
    seg_id = repos.insert_segment_with_words(
        db,
        asset_id=asset["id"],
        run_id=run["id"],
        speaker_id=None,
        segment={
            "start_sample": 0,
            "end_sample": 96_000,
            "start_frame": 0,
            "end_frame": 60,
            "text": "Hello world",
            "confidence": 0.9,
        },
        words=[
            {
                "idx": 0,
                "start_sample": 0,
                "end_sample": 48_000,
                "start_frame": 0,
                "end_frame": 30,
                "text": "Hello",
                "confidence": 0.9,
                "is_punctuation": False,
            },
            {
                "idx": 1,
                "start_sample": 48_000,
                "end_sample": 96_000,
                "start_frame": 30,
                "end_frame": 60,
                "text": "world",
                "confidence": 0.9,
                "is_punctuation": False,
            },
        ],
    )
    rc = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.replace_timeline_clips(
        db,
        rc["id"],
        [
            {
                "asset_id": asset["id"],
                "src_in_frame": 0,
                "src_out_frame_exclusive": 60,
                "seq_in_frame": 0,
                "seq_out_frame_exclusive": 60,
                "lane": 0,
                "speed_num": 1,
                "speed_den": 1,
            }
        ],
    )
    repos.replace_scenes(db, project["id"], rc["id"], [(0, 60)])
    scene = repos.list_scenes(db, rc["id"])[0]
    materialize_scene(db, scene)
    scene_id = scene["id"]
    seq = repos.get_or_create_project_sequence(db, project["id"])
    repos.replace_sequence_items(db, seq["id"], [scene_id])
    return {
        "project_id": project["id"],
        "asset_id": asset["id"],
        "segment_id": seg_id,
        "seq_id": seq["id"],
    }


def test_sequence_transcript_maps_source_words_to_sequence_frames(tmp_path: Path) -> None:
    client, db = _app(tmp_path)
    ids = _seed_sequence_with_transcript(db, tmp_path)

    response = client.get(f"/sequences/{ids['seq_id']}/transcript", headers=_H)

    assert response.status_code == 200, response.text
    blocks = response.json()
    assert len(blocks) == 1
    assert blocks[0]["segment_id"] == ids["segment_id"]
    assert blocks[0]["asset_id"] == ids["asset_id"]
    assert blocks[0]["text"] == "Hello world"
    assert blocks[0]["seq_in_frame"] == 0
    assert blocks[0]["seq_out_frame_exclusive"] == 60
    assert [w["text"] for w in blocks[0]["words"]] == ["Hello", "world"]
    assert [(w["seq_in_frame"], w["seq_out_frame_exclusive"]) for w in blocks[0]["words"]] == [
        (0, 30),
        (30, 60),
    ]


def test_sequence_transcript_reflects_segment_text_patch(tmp_path: Path) -> None:
    client, db = _app(tmp_path)
    ids = _seed_sequence_with_transcript(db, tmp_path)

    patched = client.patch(
        f"/transcript/segments/{ids['segment_id']}", json={"text": "Better caption"}, headers=_H
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["alignment_status"] == "stale"

    blocks = client.get(f"/sequences/{ids['seq_id']}/transcript", headers=_H).json()
    assert blocks[0]["text"] == "Better caption"
    assert blocks[0]["alignment_status"] == "stale"
    assert blocks[0]["alignment_error"] is None


def test_transcript_realign_marks_segment_aligning_and_uses_analysis_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, db = _app(tmp_path)
    ids = _seed_sequence_with_transcript(db, tmp_path)
    run = repos.get_latest_analysis_run(db, ids["asset_id"])
    assert run is not None
    with db.transaction() as conn:
        conn.execute(
            "UPDATE analysis_runs SET config_json=? WHERE id=?",
            ('{"language": "de"}', run["id"]),
        )
    audio = tmp_path / "mono.wav"
    audio.write_bytes(b"fake wav")
    repos.add_asset_file(
        db,
        asset_id=ids["asset_id"],
        kind="audio_mono16k",
        path=str(audio),
        is_audio_extract=True,
    )

    seen: dict[str, str] = {}

    def fake_available() -> bool:
        return True

    def fake_align_words(
        audio_path: Path, segments: list[SegmentResult], *, language: str, device: str | None = None
    ) -> list[SegmentResult]:
        seen["language"] = language
        return segments

    monkeypatch.setattr("laura.analysis.handlers.whisperx_available", fake_available)
    monkeypatch.setattr("laura.analysis.handlers.align_words", fake_align_words)

    accepted = client.post(
        f"/assets/{ids['asset_id']}/transcript:realign",
        json={"segment_ids": [ids["segment_id"]]},
        headers=_H,
    )
    assert accepted.status_code == 202, accepted.text
    job_id = accepted.json()["job_id"]

    blocks = client.get(f"/sequences/{ids['seq_id']}/transcript", headers=_H).json()
    assert blocks[0]["alignment_status"] == "aligning"
    assert blocks[0]["alignment_job_id"] == job_id
    assert blocks[0]["alignment_language"] == "de"

    app = cast(Any, client.app)
    assert app.state.runner.run_once() is True
    assert seen["language"] == "de"

    blocks = client.get(f"/sequences/{ids['seq_id']}/transcript", headers=_H).json()
    assert blocks[0]["alignment_status"] == "aligned"
    assert blocks[0]["alignment_job_id"] == job_id
    assert blocks[0]["alignment_error"] is None


def test_transcript_realign_job_replaces_segment_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, db = _app(tmp_path)
    ids = _seed_sequence_with_transcript(db, tmp_path)
    audio = tmp_path / "mono.wav"
    audio.write_bytes(b"fake wav")
    repos.add_asset_file(
        db,
        asset_id=ids["asset_id"],
        kind="audio_mono16k",
        path=str(audio),
        is_audio_extract=True,
    )

    def fake_available() -> bool:
        return True

    def fake_align_words(
        audio_path: Path, segments: list[SegmentResult], *, language: str, device: str | None = None
    ) -> list[SegmentResult]:
        assert audio_path == audio
        assert language == "en"
        assert segments[0].text == "Hello world"
        return [
            SegmentResult(
                text="Hello world",
                start_sec=0.0,
                end_sec=2.0,
                words=[
                    WordResult(text="Hello", start_sec=0.0, end_sec=22 / 30),
                    WordResult(text="wide", start_sec=22 / 30, end_sec=1.5),
                    WordResult(text="world", start_sec=1.5, end_sec=2.0),
                ],
            )
        ]

    monkeypatch.setattr("laura.analysis.handlers.whisperx_available", fake_available)
    monkeypatch.setattr("laura.analysis.handlers.align_words", fake_align_words)

    accepted = client.post(
        f"/assets/{ids['asset_id']}/transcript:realign",
        json={"segment_ids": [ids["segment_id"]], "language": "en"},
        headers=_H,
    )
    assert accepted.status_code == 202, accepted.text
    app = cast(Any, client.app)
    assert app.state.runner.run_once() is True

    blocks = client.get(f"/sequences/{ids['seq_id']}/transcript", headers=_H).json()
    assert blocks[0]["alignment_status"] == "aligned"
    assert [w["text"] for w in blocks[0]["words"]] == ["Hello", "wide", "world"]
    assert [(w["seq_in_frame"], w["seq_out_frame_exclusive"]) for w in blocks[0]["words"]] == [
        (0, 22),
        (22, 45),
        (45, 60),
    ]


def test_transcript_realign_rejects_foreign_segment(tmp_path: Path) -> None:
    client, db = _app(tmp_path)
    ids = _seed_sequence_with_transcript(db, tmp_path)
    other = repos.create_asset(
        db, project_id=ids["project_id"], type="video", display_name="b", source_path="/tmp/b.mp4"
    )
    other_run = repos.create_analysis_run(db, asset_id=other["id"], pipeline_version="1", config={})
    other_seg = repos.insert_segment_with_words(
        db,
        asset_id=other["id"],
        run_id=other_run["id"],
        speaker_id=None,
        segment={
            "start_sample": 0,
            "end_sample": 48_000,
            "start_frame": 0,
            "end_frame": 30,
            "text": "Other",
            "confidence": 0.9,
        },
        words=[],
    )

    response = client.post(
        f"/assets/{ids['asset_id']}/transcript:realign",
        json={"segment_ids": [other_seg]},
        headers=_H,
    )

    assert response.status_code == 422


def test_transcript_realign_marks_segment_failed_when_audio_is_missing(tmp_path: Path) -> None:
    client, db = _app(tmp_path)
    ids = _seed_sequence_with_transcript(db, tmp_path)

    accepted = client.post(
        f"/assets/{ids['asset_id']}/transcript:realign",
        json={"segment_ids": [ids["segment_id"]], "language": "en"},
        headers=_H,
    )
    assert accepted.status_code == 202, accepted.text

    app = cast(Any, client.app)
    assert app.state.runner.run_once() is True

    blocks = client.get(f"/sequences/{ids['seq_id']}/transcript", headers=_H).json()
    assert blocks[0]["alignment_status"] == "failed"
    assert "audio_mono16k" in blocks[0]["alignment_error"]
