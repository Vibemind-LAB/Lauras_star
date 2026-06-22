"""Tests: _maybe_enqueue_lipsync_after_vo restricts probe to the primary asset.

Fix 5 — on a multi-asset rough cut, the probe clip must only include footage from
the asset that the VO span covers (the first overlapping asset), not stitch together
clips from multiple different sources.
"""
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


def _make_video(path: Path) -> None:
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=green:s=320x240:r=30:d=2",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:d=2",
        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    ])


def _setup(tmp_path: Path) -> tuple[SqliteDatabase, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Create project with two assets and a two-clip timeline (one per asset)."""
    ws = tmp_path / "ws"
    (ws / "project").mkdir(parents=True, exist_ok=True)
    video_a = tmp_path / "a.mp4"
    video_b = tmp_path / "b.mp4"
    _make_video(video_a)
    _make_video(video_b)

    db = SqliteDatabase(Settings(workspace_root=ws, start_runner=False).db_path)
    db.migrate()

    project = repos.create_project(
        db, name="P", workspace_root=str(ws / "project"), rate_num=30, rate_den=1, drop_frame=False
    )
    asset_a = repos.create_asset(db, project_id=project["id"], type="video",
                                  display_name="a", source_path=str(video_a))
    asset_b = repos.create_asset(db, project_id=project["id"], type="video",
                                  display_name="b", source_path=str(video_b))
    for asset in (asset_a, asset_b):
        repos.update_asset_probe(db, asset["id"], type="video", duration_frames=60,
                                 rate_num=30, rate_den=1, audio_sample_rate=48000,
                                 start_timecode=None, width=320, height=240,
                                 codec_video="h264", codec_audio="aac", is_vfr=False, sha256=None)

    # Two-clip rough cut: asset_a at seq [0,30), asset_b at seq [30,60)
    tl = repos.create_timeline(db, project_id=project["id"], kind="rough_cut", name="RC")
    repos.add_timeline_clip(db, timeline_id=tl["id"], asset_id=asset_a["id"],
                            src_in_frame=0, src_out_frame_exclusive=30,
                            seq_in_frame=0, seq_out_frame_exclusive=30, lane=0)
    repos.add_timeline_clip(db, timeline_id=tl["id"], asset_id=asset_b["id"],
                            src_in_frame=0, src_out_frame_exclusive=30,
                            seq_in_frame=30, seq_out_frame_exclusive=60, lane=0)

    return db, project, tl, asset_a, asset_b


def test_lipsync_probe_restricted_to_span_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """render_clips_mp4 for the probe must only be called with clips from asset_a (seq 0-30)."""
    from laura.ai import handlers as ai_handlers

    db, project, tl, asset_a, asset_b = _setup(tmp_path)

    captured_clips: list[tuple[Path, int, int]] = []

    def fake_render(clips: list[tuple[Path, int, int]], dest: Path, **kwargs: Any) -> None:
        captured_clips.extend(clips)
        # Create a minimal valid-looking file so the probe doesn't crash
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x00")

    from laura.ai.lipsync_backend import LipsyncProbe, StubLipsyncBackend

    class _FaceBackend(StubLipsyncBackend):
        def probe(self, *, video_path: Path, audio_path: Path) -> LipsyncProbe:  # type: ignore[override]
            return LipsyncProbe(
                face_detected=True, mouth_visible=True, audio_present=True, reason="ok"
            )

    monkeypatch.setattr(ai_handlers, "render_clips_mp4", fake_render)
    monkeypatch.setattr(ai_handlers, "resolve_lipsync_backend", lambda _: _FaceBackend())

    repos.create_consent_record(db, project_id=project["id"], subject_label="Me")

    job_id = enqueue(db, queue=queue_for("ai.voiceover"), kind="ai.voiceover", payload={
        "timeline_id": tl["id"], "text": "hallo welt",
        "seq_in_frame": 0, "seq_out_frame_exclusive": 30,
        "mix_mode": "replace_original", "ducking_percent": 0, "backend": "stub",
    })
    ctx = JobContext(
        job_id=job_id, kind="ai.voiceover",
        queue=queue_for("ai.voiceover"),
        payload={
            "timeline_id": tl["id"], "text": "hallo welt",
            "seq_in_frame": 0, "seq_out_frame_exclusive": 30,
            "mix_mode": "replace_original", "ducking_percent": 0, "backend": "stub",
        },
        db=db,
    )

    result = ai_handlers.handle_voiceover(ctx)
    assert result["asset_id"] is not None

    # render_clips_mp4 was called for the probe — check only asset_a's path appears
    assert len(captured_clips) > 0, "render_clips_mp4 should have been called for the probe"
    clip_paths = {str(p) for p, _, _ in captured_clips}
    assert str(asset_a["source_path"]) in clip_paths, "asset_a should be in probe clips"
    assert str(asset_b["source_path"]) not in clip_paths, (
        f"asset_b must NOT be in probe clips for VO span [0,30); got {clip_paths}"
    )
