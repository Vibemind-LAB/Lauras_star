"""Tests: cooperative job-cancel helpers + AI handler abort-before-write (Task 7)."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from laura.db import repos
from laura.util import new_id, utcnow_iso

_FFMPEG = os.environ.get("LAURA_FFMPEG", "ffmpeg")


def _insert_job(db: Any, *, kind: str, timeline_id: str, status: str = "queued") -> str:
    jid = new_id()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO jobs (id, queue, kind, payload_json, status, cancel_requested, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (jid, "ai", kind, json.dumps({"timeline_id": timeline_id}), status,
             utcnow_iso(), utcnow_iso()),
        )
    return jid


def test_request_cancel_flags_timeline_jobs(seeded_rough_cut: tuple[Any, str, str]) -> None:
    db, tl, _ = seeded_rough_cut
    jid = _insert_job(db, kind="ai.voiceover", timeline_id=tl, status="queued")
    other = _insert_job(db, kind="ai.voiceover", timeline_id="other-tl", status="queued")
    flagged = repos.request_timeline_jobs_cancel(db, tl)
    assert jid in flagged and other not in flagged


def test_is_job_cancel_requested(seeded_rough_cut: tuple[Any, str, str]) -> None:
    db, tl, _ = seeded_rough_cut
    jid = _insert_job(db, kind="ai.lipsync", timeline_id=tl, status="running")
    assert repos.is_job_cancel_requested(db, jid) is False
    repos.request_timeline_jobs_cancel(db, tl)  # running → cancel_requested=1
    assert repos.is_job_cancel_requested(db, jid) is True


@pytest.mark.skipif(shutil.which(_FFMPEG) is None, reason="ffmpeg not on PATH")
def test_handle_voiceover_aborts_when_cancel_requested(tmp_path: Path) -> None:
    """handle_voiceover must not write any audio clip when cancel_requested=1."""
    from laura.ai.handlers import handle_voiceover
    from laura.config import Settings
    from laura.db.database import SqliteDatabase
    from laura.ingest.ffmpeg import run_ffmpeg
    from laura.jobs import JobContext, enqueue
    from laura.jobs.queues import queue_for

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

    payload: dict[str, Any] = {
        "timeline_id": tl["id"], "text": "hallo welt",
        "seq_in_frame": 10, "seq_out_frame_exclusive": 40,
        "mix_mode": "replace_original", "ducking_percent": 0, "backend": "stub",
    }
    job_id = enqueue(db, queue=queue_for("ai.voiceover"), kind="ai.voiceover", payload=payload)
    ctx = JobContext(job_id=job_id, kind="ai.voiceover",
                     queue=queue_for("ai.voiceover"), payload=payload, db=db)

    # Flag cancel BEFORE calling the handler
    with db.transaction() as conn:
        conn.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (ctx.job_id,))

    before = repos.list_timeline_audio_clips(db, tl["id"])
    result = handle_voiceover(ctx)
    after = repos.list_timeline_audio_clips(db, tl["id"])

    assert result == {"status": "cancelled", "reason": "undo"}
    assert after == before  # no clip was appended
