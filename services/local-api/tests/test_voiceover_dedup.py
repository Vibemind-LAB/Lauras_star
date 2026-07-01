"""Tests: VO dedup — second replace_original VO on same span replaces the first.

Fix 3 — handle_voiceover removes overlapping same-mix_mode clips before adding a new
one, so editing the same span twice doesn't stack two replace_original clips.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.main import create_app

_TOKEN = "test-token"


def _make_app(tmp_path: Path) -> tuple[Any, Database]:
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False, token=_TOKEN)
    app = create_app(settings)
    db: Database = app.state.db
    return app, db


def _seed(db: Database, tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any], str]:
    ws = tmp_path / "ws" / "project"
    ws.mkdir(parents=True, exist_ok=True)
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root=str(ws),
    )
    timeline = repos.create_timeline(
        db, project_id=project["id"], name="seq", kind="sequence",
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video",
        display_name="source", source_path=str(ws / "source.mp4"),
    )
    run = repos.create_analysis_run(
        db, asset_id=asset["id"], pipeline_version="test", config={},
    )
    segment_id = repos.insert_segment_with_words(
        db,
        asset_id=asset["id"],
        run_id=run["id"],
        speaker_id=None,
        segment={
            "start_sample": 0, "end_sample": 48_000,
            "start_frame": 0, "end_frame": 30,
            "text": "Hallo Laura", "confidence": 1.0,
        },
        words=[],
    )
    return project, timeline, segment_id


def _post_voiceover(
    client: TestClient,
    timeline_id: str,
    *,
    text: str = "Hallo Laura",
    mix_mode: str = "replace_original",
) -> Any:
    return client.post(
        f"/timelines/{timeline_id}/voiceover",
        json={
            "seq_in_frame": 0,
            "seq_out_frame_exclusive": 30,
            "text": text,
            "backend": "stub",
            "mix_mode": mix_mode,
        },
        headers={"X-Laura-Token": _TOKEN},
    )


def test_second_vo_replaces_first_on_same_span(tmp_path: Path) -> None:
    """Two replace_original VOs on the same span → exactly one clip in the DB.

    Uses different text to bypass the idempotency key so both jobs actually run.
    """
    app, db = _make_app(tmp_path)
    client = TestClient(app)
    _, timeline, segment_id = _seed(db, tmp_path)

    # First VO
    r1 = _post_voiceover(client, timeline["id"], text="Hallo Laura")
    assert r1.status_code == 202, r1.text
    assert app.state.runner.run_once() is True

    # Second VO on the same span with different text (different idempotency key)
    r2 = _post_voiceover(client, timeline["id"], text="Tschüss Laura")
    assert r2.status_code == 202, r2.text
    assert app.state.runner.run_once() is True

    clips = repos.list_timeline_audio_clips(db, timeline["id"])
    replace_clips = [c for c in clips if c["mix_mode"] == "replace_original"]
    assert len(replace_clips) == 1, (
        f"Expected 1 replace_original clip after two VOs on same span, got {len(replace_clips)}"
    )


def test_mix_mode_mix_does_not_dedup(tmp_path: Path) -> None:
    """Two mix-mode VOs on the same span both remain (additive music track behaviour)."""
    app, db = _make_app(tmp_path)
    client = TestClient(app)
    _, timeline, segment_id = _seed(db, tmp_path)

    r1 = _post_voiceover(client, timeline["id"], text="Hallo Laura", mix_mode="mix")
    assert r1.status_code == 202, r1.text
    assert app.state.runner.run_once() is True

    r2 = _post_voiceover(client, timeline["id"], text="Tschüss Laura", mix_mode="mix")
    assert r2.status_code == 202, r2.text
    assert app.state.runner.run_once() is True

    clips = repos.list_timeline_audio_clips(db, timeline["id"])
    mix_clips = [c for c in clips if c["mix_mode"] == "mix"]
    assert len(mix_clips) == 2, (
        f"Expected 2 mix clips (additive), got {len(mix_clips)}"
    )


def test_delete_audio_clips_overlapping_repo_helper(tmp_path: Path) -> None:
    """Unit-test the repos helper directly: overlapping clips are removed, others kept."""
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()

    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(tmp_path / "ws" / "project"),
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="t", kind="rough_cut")
    audio_asset = repos.create_asset(
        db, project_id=project["id"], type="audio",
        display_name="a.wav", source_path="/tmp/a.wav",
    )

    # Insert two overlapping clips
    repos.add_timeline_audio_clip(
        db, timeline_id=tl["id"], asset_id=audio_asset["id"],
        seq_in_frame=0, seq_out_frame_exclusive=30, mix_mode="replace_original",
    )
    repos.add_timeline_audio_clip(
        db, timeline_id=tl["id"], asset_id=audio_asset["id"],
        seq_in_frame=10, seq_out_frame_exclusive=40, mix_mode="replace_original",
    )
    # And one non-overlapping clip at [50, 80)
    repos.add_timeline_audio_clip(
        db, timeline_id=tl["id"], asset_id=audio_asset["id"],
        seq_in_frame=50, seq_out_frame_exclusive=80, mix_mode="replace_original",
    )

    # Delete overlapping [0, 30) → removes the first two
    count = repos.delete_timeline_audio_clips_overlapping(
        db, tl["id"], seq_in=0, seq_out_excl=30, mix_mode="replace_original"
    )
    assert count == 2

    remaining = repos.list_timeline_audio_clips(db, tl["id"])
    assert len(remaining) == 1
    assert remaining[0]["seq_in_frame"] == 50
