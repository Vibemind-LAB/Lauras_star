"""Shared fixtures: an isolated workspace, a migrated DB, and a TestClient."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # No token, no background runner thread -> deterministic tests.
    return Settings(workspace_root=tmp_path, token=None, start_runner=False)


@pytest.fixture
def db(settings: Settings) -> Database:
    database = SqliteDatabase(settings.db_path)
    database.migrate()
    return database


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def seeded_timeline(tmp_path: Path) -> tuple[Database, str, str]:
    """Create a migrated DB with a project, sequence timeline, video asset, and one segment.

    Returns (db, timeline_id, segment_id).  Intentionally lightweight — uses a
    fake source path so no real media file is required on disk for the voiceover
    stub backend (which writes its own WAV to the synthetic dir).
    """
    workspace = tmp_path / "ws" / "project"
    workspace.mkdir(parents=True, exist_ok=True)

    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    database = SqliteDatabase(settings.db_path)
    database.migrate()

    project = repos.create_project(
        database,
        name="audit-test",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(workspace),
    )
    timeline = repos.create_timeline(
        database,
        project_id=project["id"],
        name="sequence",
        kind="sequence",
    )
    asset = repos.create_asset(
        database,
        project_id=project["id"],
        type="video",
        display_name="source",
        source_path=str(workspace / "source.mp4"),
    )
    run = repos.create_analysis_run(
        database,
        asset_id=asset["id"],
        pipeline_version="test",
        config={},
    )
    segment_id = repos.insert_segment_with_words(
        database,
        asset_id=asset["id"],
        run_id=run["id"],
        speaker_id=None,
        segment={
            "start_sample": 0,
            "end_sample": 48_000,
            "start_frame": 0,
            "end_frame": 30,
            "text": "hallo welt",
            "confidence": 1.0,
        },
        words=[],
    )
    return database, timeline["id"], segment_id


@pytest.fixture
def seeded_rough_cut(tmp_path: Path) -> tuple[Database, str, str]:
    """A migrated DB with a rough-cut timeline carrying one clip, one scene (with music),
    and one audio clip. Returns (db, timeline_id, asset_id). Shared by the undo/redo tests."""
    workspace = tmp_path / "ws" / "project"
    workspace.mkdir(parents=True, exist_ok=True)
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    database = SqliteDatabase(settings.db_path)
    database.migrate()
    project = repos.create_project(
        database,
        name="undo-test",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(workspace),
    )
    timeline = repos.create_timeline(
        database,
        project_id=project["id"],
        name="rough",
        kind="sequence",
    )
    asset = repos.create_asset(
        database,
        project_id=project["id"],
        type="video",
        display_name="src",
        source_path=str(workspace / "source.mp4"),
    )
    repos.add_timeline_clip(
        database,
        timeline_id=timeline["id"],
        asset_id=asset["id"],
        src_in_frame=0,
        src_out_frame_exclusive=30,
        seq_in_frame=0,
        seq_out_frame_exclusive=30,
        lane=0,
        role="base",
    )
    repos.replace_scenes(database, project["id"], timeline["id"], [(0, 30)])
    scene = repos.list_scenes(database, timeline["id"])[0]
    repos.set_scene_music(database, scene["id"], asset["id"], 80)
    repos.add_timeline_audio_clip(
        database,
        timeline_id=timeline["id"],
        asset_id=asset["id"],
        seq_in_frame=0,
        seq_out_frame_exclusive=30,
        label="VO",
    )
    return database, timeline["id"], asset["id"]
