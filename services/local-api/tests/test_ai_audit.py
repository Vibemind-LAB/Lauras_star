"""Compliance tests: audit_events are written for every AI job success (D1).

Each test drives its handler through the stub backend and asserts that
exactly one audit_event row with the expected action lands in the DB.

Voiceover uses the pure-Python stub (no ffmpeg needed).
Reenact and lipsync render driving-clip MP4s via ffmpeg — those tests are
skipped when ffmpeg is not on PATH.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from laura.ai import handlers
from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.ingest.ffmpeg import run_ffmpeg
from laura.jobs.runner import JobContext

_FFMPEG_BIN = os.environ.get("LAURA_FFMPEG", "ffmpeg")
_ffmpeg_available = shutil.which(_FFMPEG_BIN) is not None


def _ctx(db: Database, kind: str, payload: dict[str, Any]) -> JobContext:
    return JobContext(job_id="j1", kind=kind, queue="ai", payload=payload, db=db)


# ── voiceover ────────────────────────────────────────────────────────────────

def test_voiceover_success_writes_audit_event(seeded_timeline: tuple[Database, str, str]) -> None:
    db, timeline_id, segment_id = seeded_timeline
    handlers.handle_voiceover(
        _ctx(db, "ai.voiceover", {
            "timeline_id": timeline_id,
            "segment_id": segment_id,
            "text": "hallo welt",
            "backend": "stub",
            "seq_in_frame": 0,
            "seq_out_frame_exclusive": 30,
        })
    )
    events = repos.list_audit_events(db, limit=100)
    vo = [e for e in events if e["action"] == "ai.voiceover"]
    assert len(vo) == 1
    assert vo[0]["entity_type"] == "media_asset"
    assert vo[0]["entity_id"]  # the new VO asset id


# ── reenact ───────────────────────────────────────────────────────────────────

def _seed_reenact_scene(
    tmp_path: Path,
) -> tuple[Database, str, str, str]:
    """Create project + timeline + video clip + consent + portrait asset.

    Returns (db, timeline_id, portrait_asset_id, consent_id).
    """
    media = tmp_path / "base.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=green:s=320x240:r=30:d=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(media),
    ])

    workspace = tmp_path / "ws" / "project"
    workspace.mkdir(parents=True, exist_ok=True)
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()

    project = repos.create_project(
        db,
        name="reenact-audit",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(workspace),
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="cut", kind="rough_cut")
    video_asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="base.mp4",
        source_path=str(media),
    )
    repos.add_timeline_clip(
        db,
        timeline_id=tl["id"],
        asset_id=video_asset["id"],
        src_in_frame=0,
        src_out_frame_exclusive=30,
        seq_in_frame=0,
        seq_out_frame_exclusive=30,
        lane=0,
        role="base",
    )
    portrait_asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="portrait.mp4",
        source_path=str(media),
    )
    consent = repos.create_consent_record(
        db,
        project_id=project["id"],
        subject_label="test-subject",
    )
    return db, tl["id"], portrait_asset["id"], consent["id"]


@pytest.mark.skipif(not _ffmpeg_available, reason="ffmpeg not available on PATH")
def test_reenact_success_writes_audit_event(tmp_path: Path) -> None:
    db, timeline_id, portrait_asset_id, consent_id = _seed_reenact_scene(tmp_path)
    handlers.handle_reenact(
        _ctx(db, "ai.reenact", {
            "timeline_id": timeline_id,
            "portrait_asset_id": portrait_asset_id,
            "consent_id": consent_id,
            "seq_in_frame": 0,
            "seq_out_frame_exclusive": 30,
            "backend": "stub",
        })
    )
    events = repos.list_audit_events(db, limit=100)
    re = [e for e in events if e["action"] == "ai.reenact"]
    assert len(re) == 1
    assert re[0]["entity_type"] == "media_asset"
    assert re[0]["entity_id"]  # the new reenact asset id


# ── lipsync ───────────────────────────────────────────────────────────────────

def _seed_lipsync_scene(
    tmp_path: Path,
) -> tuple[Database, str, str, str]:
    """Create project + timeline + video clip + audio asset + consent.

    Returns (db, timeline_id, audio_asset_id, consent_id).
    """
    video = tmp_path / "base.mp4"
    audio = tmp_path / "voice.wav"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=green:s=320x240:r=30:d=1",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:d=1",
        "-shortest",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(video),
    ])
    run_ffmpeg([
        "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:d=1",
        "-c:a", "pcm_s16le",
        str(audio),
    ])

    workspace = tmp_path / "ws" / "project"
    workspace.mkdir(parents=True, exist_ok=True)
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()

    project = repos.create_project(
        db,
        name="lipsync-audit",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(workspace),
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="cut", kind="rough_cut")
    video_asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="base.mp4",
        source_path=str(video),
    )
    repos.add_timeline_clip(
        db,
        timeline_id=tl["id"],
        asset_id=video_asset["id"],
        src_in_frame=0,
        src_out_frame_exclusive=30,
        seq_in_frame=0,
        seq_out_frame_exclusive=30,
        lane=0,
        role="base",
    )
    audio_asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="audio",
        display_name="voice.wav",
        source_path=str(audio),
    )
    consent = repos.create_consent_record(
        db,
        project_id=project["id"],
        subject_label="test-subject",
    )
    return db, tl["id"], audio_asset["id"], consent["id"]


@pytest.mark.skipif(not _ffmpeg_available, reason="ffmpeg not available on PATH")
def test_lipsync_success_writes_audit_event(tmp_path: Path) -> None:
    db, timeline_id, audio_asset_id, consent_id = _seed_lipsync_scene(tmp_path)
    handlers.handle_lipsync(
        _ctx(db, "ai.lipsync", {
            "timeline_id": timeline_id,
            "audio_asset_id": audio_asset_id,
            "consent_id": consent_id,
            "seq_in_frame": 0,
            "seq_out_frame_exclusive": 30,
            "backend": "stub",
            "license_accepted": True,
        })
    )
    events = repos.list_audit_events(db, limit=100)
    ls = [e for e in events if e["action"] == "ai.lipsync"]
    assert len(ls) == 1
    assert ls[0]["entity_type"] == "media_asset"
    assert ls[0]["entity_id"]  # the new lipsync asset id
