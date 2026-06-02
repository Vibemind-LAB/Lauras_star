"""API test: transcript-first timeline operations produce real, persisted cuts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app


def _word(idx: int, sf: int, ef: int, text: str) -> dict[str, Any]:
    return {"idx": idx, "start_sample": sf * 1600, "end_sample": ef * 1600,
            "start_frame": sf, "end_frame": ef, "text": text, "confidence": 0.9,
            "is_punctuation": False}


def test_append_from_words_then_delete(tmp_path: Path) -> None:
    settings = Settings(workspace_root=tmp_path, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    client = TestClient(create_app(settings))
    client.__enter__()
    try:
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
        run = repos.create_analysis_run(
            db, asset_id=asset["id"], pipeline_version="1", config={"stages": {}}
        )
        repos.insert_segment_with_words(
            db, asset_id=asset["id"], run_id=run["id"], speaker_id=None,
            segment={"start_sample": 0, "end_sample": 32000, "start_frame": 0,
                     "end_frame": 20, "text": "Hallo Welt", "confidence": 0.9},
            words=[_word(0, 0, 10, "Hallo"), _word(1, 10, 20, "Welt")],
        )
        words = repos.get_transcript(db, asset["id"], run["id"])[0]["words"]
        w0, w1 = words[0]["id"], words[1]["id"]

        timeline = client.post(
            f"/projects/{project['id']}/timelines", json={"name": "RC", "kind": "rough_cut"}
        ).json()
        tid = timeline["id"]

        # transcript-first: append the word span [Hallo..Welt] -> source frames 0..20
        resp = client.post(
            f"/timelines/{tid}/operations",
            json={"op": "append_from_words", "word_start_id": w0, "word_end_id": w1},
        )
        assert resp.status_code == 200, resp.text
        clips = resp.json()["clips"]
        assert len(clips) == 1
        assert (clips[0]["src_in_frame"], clips[0]["src_out_frame_exclusive"]) == (0, 20)
        assert (clips[0]["seq_in_frame"], clips[0]["seq_out_frame_exclusive"]) == (0, 20)

        # append again -> second clip at seq 20..40
        client.post(
            f"/timelines/{tid}/operations",
            json={"op": "append_from_words", "word_start_id": w0, "word_end_id": w1},
        )
        got = client.get(f"/timelines/{tid}").json()
        assert len(got["clips"]) == 2
        assert got["clips"][1]["seq_in_frame"] == 20

        # delete the first clip (ripple) -> remaining clip shifts to 0
        resp = client.post(
            f"/timelines/{tid}/operations",
            json={"op": "delete", "seq_in_frame": 0, "seq_out_frame_exclusive": 20},
        )
        clips = resp.json()["clips"]
        assert len(clips) == 1
        assert clips[0]["seq_in_frame"] == 0

        # canonical OTIO was regenerated and persisted
        tl = repos.get_timeline(db, tid)
        assert tl is not None and "OTIO_SCHEMA" in tl["otio_json"]
    finally:
        client.__exit__(None, None, None)
