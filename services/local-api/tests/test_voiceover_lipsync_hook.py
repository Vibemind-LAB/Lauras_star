"""handle_voiceover success path conditionally enqueues ai.lipsync (spec §5)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.ingest.ffmpeg import run_ffmpeg
from laura.jobs import JobContext, enqueue
from laura.jobs.queues import queue_for

_FFMPEG = os.environ.get("LAURA_FFMPEG", "ffmpeg")
pytestmark = pytest.mark.skipif(shutil.which(_FFMPEG) is None, reason="ffmpeg not on PATH")


def _setup(tmp_path: Path) -> tuple[SqliteDatabase, dict[str, Any], dict[str, Any]]:
    ws = tmp_path / "ws"
    (ws / "project").mkdir(parents=True, exist_ok=True)
    video = tmp_path / "base.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=green:s=320x240:r=30:d=2",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:d=2",
        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video),
    ])
    db = SqliteDatabase(Settings(workspace_root=ws, start_runner=False).db_path)
    db.migrate()
    project = repos.create_project(db, name="P", workspace_root=str(ws / "project"),
                                   rate_num=30, rate_den=1, drop_frame=False)
    asset = repos.create_asset(db, project_id=project["id"], type="video",
                               display_name="base", source_path=str(video))
    repos.update_asset_probe(db, asset["id"], type="video", duration_frames=60,
                             rate_num=30, rate_den=1, audio_sample_rate=48000,
                             start_timecode=None, width=320, height=240,
                             codec_video="h264", codec_audio="aac", is_vfr=False, sha256=None)
    tl = repos.create_timeline(db, project_id=project["id"], kind="rough_cut", name="RC")
    repos.add_timeline_clip(db, timeline_id=tl["id"], asset_id=asset["id"],
                            src_in_frame=0, src_out_frame_exclusive=60,
                            seq_in_frame=0, seq_out_frame_exclusive=60, lane=0)
    return db, project, tl


def _ctx(db: SqliteDatabase, tl_id: str, project_id: str) -> JobContext:
    job_id = enqueue(db, queue=queue_for("ai.voiceover"), kind="ai.voiceover", payload={
        "timeline_id": tl_id, "text": "hallo welt",
        "seq_in_frame": 10, "seq_out_frame_exclusive": 40,
        "mix_mode": "replace_original", "ducking_percent": 0, "backend": "stub",
    })
    return JobContext(job_id=job_id, kind="ai.voiceover",
                      queue=queue_for("ai.voiceover"),
                      payload={"timeline_id": tl_id, "text": "hallo welt",
                               "seq_in_frame": 10, "seq_out_frame_exclusive": 40,
                               "mix_mode": "replace_original", "ducking_percent": 0,
                               "backend": "stub"}, db=db)


def test_face_plus_consent_enqueues_lipsync(tmp_path: Path) -> None:
    from laura.ai.handlers import handle_voiceover

    db, project, tl = _setup(tmp_path)
    repos.create_consent_record(db, project_id=project["id"], subject_label="Me")
    result = handle_voiceover(_ctx(db, tl["id"], project["id"]))
    assert result["lipsync_job_id"] is not None
    queued = repos.get_job(db, str(result["lipsync_job_id"]))
    assert queued is not None and queued["kind"] == "ai.lipsync"


def test_face_but_no_consent_skips_lipsync(tmp_path: Path) -> None:
    from laura.ai.handlers import handle_voiceover

    db, project, tl = _setup(tmp_path)
    result = handle_voiceover(_ctx(db, tl["id"], project["id"]))
    assert result["lipsync_job_id"] is None
    assert result["lipsync_skip_reason"] == "no_consent"


def test_no_face_skips_lipsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When the lipsync backend reports no face, lipsync is silently skipped."""
    from laura.ai import handlers as ai_handlers
    from laura.ai.lipsync_backend import LipsyncProbe, StubLipsyncBackend

    class _NoFaceBackend(StubLipsyncBackend):
        def probe(self, *, video_path: Path, audio_path: Path) -> LipsyncProbe:  # type: ignore[override]
            return LipsyncProbe(face_detected=False, mouth_visible=False, audio_present=True,
                                reason="no face in clip")

    monkeypatch.setattr(ai_handlers, "resolve_lipsync_backend", lambda _: _NoFaceBackend())

    db, project, tl = _setup(tmp_path)
    repos.create_consent_record(db, project_id=project["id"], subject_label="Me")
    result = ai_handlers.handle_voiceover(_ctx(db, tl["id"], project["id"]))
    assert result["lipsync_job_id"] is None
    assert result["lipsync_skip_reason"] == "no_face"


def test_probe_error_vo_still_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A probe/enqueue error must NOT fail the VO — lipsync is best-effort."""
    from laura.ai import handlers as ai_handlers
    from laura.ai.lipsync_backend import StubLipsyncBackend

    class _BrokenBackend(StubLipsyncBackend):
        def probe(self, *, video_path: Path, audio_path: Path) -> object:  # type: ignore[override]
            raise RuntimeError("sidecar exploded")

    monkeypatch.setattr(ai_handlers, "resolve_lipsync_backend", lambda _: _BrokenBackend())

    db, project, tl = _setup(tmp_path)
    repos.create_consent_record(db, project_id=project["id"], subject_label="Me")
    # Must not raise — VO result is still present.
    result = ai_handlers.handle_voiceover(_ctx(db, tl["id"], project["id"]))
    assert result["asset_id"] is not None
    assert result["lipsync_job_id"] is None
    assert result["lipsync_skip_reason"] == "probe_error"
