"""API tests for timelines, exports, preflight, and asset captions."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database
from laura.main import create_app


def _setup(tmp_path: Path) -> tuple[Database, TestClient, str, str]:
    settings = Settings(workspace_root=tmp_path, start_runner=False)
    db = Database(settings.db_path)
    db.migrate()
    client = TestClient(create_app(settings))
    client.__enter__()
    project = client.post(
        "/projects", json={"name": "p", "sequence_rate_num": 30, "sequence_rate_den": 1}
    ).json()
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a.mov", source_path="a.mov"
    )
    repos.update_asset_probe(
        db, asset["id"], type="video", duration_frames=300, rate_num=30, rate_den=1,
        audio_sample_rate=48000, start_timecode=None, width=320, height=240,
        codec_video="h264", codec_audio="aac", is_vfr=False, sha256="x",
    )
    return db, client, project["id"], asset["id"]


def test_timeline_create_export_validate(tmp_path: Path) -> None:
    db, client, project_id, asset_id = _setup(tmp_path)
    try:
        timeline = client.post(
            f"/projects/{project_id}/timelines", json={"name": "Rough", "kind": "rough_cut"}
        ).json()
        tid = timeline["id"]

        repos.add_timeline_clip(
            db, timeline_id=tid, asset_id=asset_id, src_in_frame=0,
            src_out_frame_exclusive=30, seq_in_frame=0, seq_out_frame_exclusive=30,
        )
        repos.add_timeline_clip(
            db, timeline_id=tid, asset_id=asset_id, src_in_frame=100,
            src_out_frame_exclusive=130, seq_in_frame=30, seq_out_frame_exclusive=60,
        )

        got = client.get(f"/timelines/{tid}").json()
        assert len(got["clips"]) == 2

        otio = client.post(f"/timelines/{tid}/exports", json={"format": "otio"})
        assert otio.status_code == 201, otio.text
        body = otio.json()
        assert "OTIO_SCHEMA" in body["content"]
        assert Path(body["output_path"]).exists()

        edl = client.post(f"/timelines/{tid}/exports", json={"format": "edl"}).json()
        assert edl["content"].startswith("TITLE: Rough")

        # captions are not a timeline export
        assert client.post(f"/timelines/{tid}/exports", json={"format": "srt"}).status_code == 400

        validated = client.post(
            "/interop/validate", json={"timeline_id": tid, "format": "edl"}
        ).json()
        assert validated["format"] == "edl"
        assert validated["ok"] is True
    finally:
        client.__exit__(None, None, None)


def test_asset_captions_from_transcript(tmp_path: Path) -> None:
    db, client, _project_id, asset_id = _setup(tmp_path)
    try:
        run = repos.create_analysis_run(
            db, asset_id=asset_id, pipeline_version="1", config={"stages": {}}
        )
        repos.insert_segment_with_words(
            db, asset_id=asset_id, run_id=run["id"], speaker_id=None,
            segment={"start_sample": 48000, "end_sample": 96000, "start_frame": 30,
                     "end_frame": 60, "text": "Hallo Welt", "confidence": 0.9},
            words=[],
        )

        srt = client.get(f"/assets/{asset_id}/captions.srt")
        assert srt.status_code == 200
        assert "00:00:01,000 --> 00:00:02,000" in srt.text
        assert "Hallo Welt" in srt.text

        vtt = client.get(f"/assets/{asset_id}/captions.vtt")
        assert vtt.text.startswith("WEBVTT")
        assert "00:00:01.000 --> 00:00:02.000" in vtt.text
    finally:
        client.__exit__(None, None, None)
