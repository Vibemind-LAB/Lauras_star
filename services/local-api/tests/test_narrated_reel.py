"""TDD for Task 5 — the narrated-reel collage builder (`ai.narrated_reel`).

Spec: docs/superpowers/specs/2026-08-21-narrated-reel-design.md §6.

Test plan (brief (a)-(e)):
  (a) Endpoint validation: unknown project -> 404; a beat referencing a foreign asset ->
      422 (naming the beat index); empty beats -> 422; success creates a timeline (default
      kind) and enqueues the job.
  (b) Handler run with a natural-length fake backend, 3 beats over 2 fixture assets: clips
      land in order with src_out = src_in + measured + pad, crossfade 8 on clips 0..n-2,
      fade 12 on the last, three VO audio clips (replace_original, ducking 100, fades
      3/4), word-timing sidecars exist.
  (c) render=True chains an export.render job with captions/caption_source=voiceover/
      caption_preset from the request.
  (d) A beat whose measured+pad speech would run past the source asset's end clips to the
      asset end instead, with a warning naming the beat.
  (e) Cancelling between beat 0 and beat 1 aborts cleanly (status "cancelled") with no
      clip/voice-track for beat 1 -- only beat 0's work is committed.
  (f) A mid-build exception (beat 2's synthesis raises after beats 0/1 already
      committed) rolls the WHOLE timeline back to its pre-job state and re-raises --
      zero clips, zero VO clips, zero export.render jobs afterwards.
  (g) Idempotency is reachable: two identical POSTs resolve to the same timeline_id/
      job_id (one timeline, one job in the DB); a POST with a changed beat text creates
      a new one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from laura.ai import handlers as ai_handlers
from laura.ai.voiceover_backend import StubVoiceoverBackend
from laura.db import repos
from laura.db.database import Database
from laura.jobs import JobContext, enqueue
from laura.jobs.queues import queue_for


class _QueuedLengthBackend:
    """A fake voiceover backend that writes a WAV whose length is popped off a fixed
    queue, one entry per ``synthesize`` call -- so each beat in a multi-beat test can be
    given its own deterministic measured length, independent of whatever
    ``duration_frames`` hint the handler passes in (mirrors ``_FixedLengthBackend`` in
    test_voiceover_natural_fit.py, generalised to a per-call sequence)."""

    name = "stub"

    def __init__(self, frames_seq: list[int]) -> None:
        self._frames = list(frames_seq)
        self._calls = 0

    def available(self) -> bool:
        return True

    def synthesize(
        self,
        *,
        text: str,
        out_path: Path,
        duration_frames: int,  # noqa: ARG002 - intentionally ignored
        fps_num: int,
        fps_den: int,
        sample_rate: int,
        language: str | None = None,
        voice_id: str | None = None,
        fit_to_slot: bool = True,
    ) -> None:
        frames = self._frames[self._calls]
        self._calls += 1
        StubVoiceoverBackend().synthesize(
            text=text,
            out_path=out_path,
            duration_frames=frames,
            fps_num=fps_num,
            fps_den=fps_den,
            sample_rate=sample_rate,
            language=language,
            voice_id=voice_id,
            fit_to_slot=fit_to_slot,
        )


def _patch_queued_backend(monkeypatch: pytest.MonkeyPatch, frames_seq: list[int]) -> None:
    # One shared backend instance across all calls -- resolve_voiceover_backend is called
    # fresh per beat, and the queue must advance across beats, not reset each time.
    backend = _QueuedLengthBackend(frames_seq)
    monkeypatch.setattr(
        ai_handlers, "resolve_voiceover_backend", lambda *_args, **_kwargs: backend
    )


def _seed_project(db: Database, tmp_path: Path, *, name: str = "p") -> dict[str, Any]:
    return repos.create_project(
        db,
        name=name,
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(tmp_path / "ws" / name),
    )


def _seed_video_asset(
    db: Database,
    project: dict[str, Any],
    tmp_path: Path,
    *,
    name: str,
    duration_frames: int,
) -> dict[str, Any]:
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name=name,
        source_path=str(tmp_path / f"{name}.mp4"),
    )
    repos.update_asset_probe(
        db,
        asset["id"],
        type="video",
        duration_frames=duration_frames,
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
    fresh = repos.get_asset(db, asset["id"])
    assert fresh is not None
    return fresh


def _beat(
    asset: dict[str, Any], *, text: str, src_in_frame: int = 0, pad_frames: int = 12
) -> dict[str, Any]:
    return {
        "text": text,
        "asset_id": asset["id"],
        "src_in_frame": src_in_frame,
        "pad_frames": pad_frames,
    }


# ---------------------------------------------------------------------------
# (a) endpoint validation
# ---------------------------------------------------------------------------


def test_narrated_reel_404_when_project_unknown(client: TestClient) -> None:
    resp = client.post(
        "/projects/does-not-exist/narrated-reel",
        json={"beats": [{"text": "hi", "asset_id": "whatever", "src_in_frame": 0}]},
    )
    assert resp.status_code == 404


def test_narrated_reel_422_when_beats_empty(client: TestClient, tmp_path: Path) -> None:
    app = cast(Any, client.app)
    db: Database = app.state.db
    project = _seed_project(db, tmp_path)

    resp = client.post(f"/projects/{project['id']}/narrated-reel", json={"beats": []})
    assert resp.status_code == 422


def test_narrated_reel_422_when_beat_asset_belongs_to_another_project(
    client: TestClient, tmp_path: Path
) -> None:
    app = cast(Any, client.app)
    db: Database = app.state.db
    project = _seed_project(db, tmp_path, name="home")
    other_project = _seed_project(db, tmp_path, name="other")
    foreign_asset = _seed_video_asset(
        db, other_project, tmp_path, name="foreign", duration_frames=300
    )

    resp = client.post(
        f"/projects/{project['id']}/narrated-reel",
        json={"beats": [_beat(foreign_asset, text="hi")]},
    )
    assert resp.status_code == 422
    assert "beat 0" in resp.text
    assert "another project" in resp.text


def test_narrated_reel_422_when_asset_not_video(client: TestClient, tmp_path: Path) -> None:
    app = cast(Any, client.app)
    db: Database = app.state.db
    project = _seed_project(db, tmp_path)
    audio_asset = repos.create_asset(
        db, project_id=project["id"], type="audio", display_name="a",
        source_path=str(tmp_path / "a.wav"),
    )
    repos.update_asset_probe(
        db, audio_asset["id"], type="audio", duration_frames=300, rate_num=30, rate_den=1,
        audio_sample_rate=48_000, start_timecode=None, width=None, height=None,
        codec_video=None, codec_audio="pcm_s16le", is_vfr=False, sha256=None,
    )
    fresh_audio_asset = repos.get_asset(db, audio_asset["id"])
    assert fresh_audio_asset is not None
    audio_asset = fresh_audio_asset

    resp = client.post(
        f"/projects/{project['id']}/narrated-reel",
        json={"beats": [_beat(audio_asset, text="hi")]},
    )
    assert resp.status_code == 422
    assert "beat 0" in resp.text


def test_narrated_reel_422_when_src_in_at_or_past_duration(
    client: TestClient, tmp_path: Path
) -> None:
    app = cast(Any, client.app)
    db: Database = app.state.db
    project = _seed_project(db, tmp_path)
    asset = _seed_video_asset(db, project, tmp_path, name="a", duration_frames=100)

    resp = client.post(
        f"/projects/{project['id']}/narrated-reel",
        json={"beats": [_beat(asset, text="hi", src_in_frame=100)]},
    )
    assert resp.status_code == 422
    assert "beat 0" in resp.text


def test_narrated_reel_success_creates_timeline_and_enqueues_job(
    client: TestClient, tmp_path: Path
) -> None:
    app = cast(Any, client.app)
    db: Database = app.state.db
    project = _seed_project(db, tmp_path)
    asset = _seed_video_asset(db, project, tmp_path, name="a", duration_frames=1000)

    resp = client.post(
        f"/projects/{project['id']}/narrated-reel",
        json={"beats": [_beat(asset, text="hello there")], "backend": "stub"},
    )
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert set(data) == {"timeline_id", "job_id"}

    timeline = repos.get_timeline(db, data["timeline_id"])
    assert timeline is not None
    assert timeline["kind"] == "rough_cut"  # default kind, unchanged
    assert timeline["project_id"] == project["id"]

    job = repos.get_job(db, data["job_id"])
    assert job is not None
    assert job["kind"] == "ai.narrated_reel"
    assert job["status"] == "queued"


# ---------------------------------------------------------------------------
# (b) 3-beat collage build over 2 fixture assets
# ---------------------------------------------------------------------------


def test_narrated_reel_handler_builds_three_beat_collage(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = cast(Any, client.app)
    db: Database = app.state.db
    project = _seed_project(db, tmp_path)
    asset_a = _seed_video_asset(db, project, tmp_path, name="a", duration_frames=1000)
    asset_b = _seed_video_asset(db, project, tmp_path, name="b", duration_frames=1000)

    # Frame counts are chosen as multiples of the 30fps rate so the synthesized WAV's
    # duration is a clean number of seconds -- ffprobe's textual (6-decimal) duration
    # then round-trips to the exact frame count with no ceil-rounding slack (see
    # _measure_wav_frames_ceil's docstring on why it rounds UP; a non-clean duration like
    # 50/30s would come back as 51 purely from ffprobe's decimal-string precision).
    _patch_queued_backend(monkeypatch, [30, 60, 90])

    body = {
        "beats": [
            _beat(asset_a, text="one", src_in_frame=0, pad_frames=12),
            _beat(asset_b, text="two", src_in_frame=0, pad_frames=12),
            _beat(asset_a, text="three", src_in_frame=500, pad_frames=12),
        ],
        "crossfade_frames": 8,
        "final_fade_frames": 12,
        "backend": "stub",
        "render": False,
    }
    accepted = client.post(f"/projects/{project['id']}/narrated-reel", json=body)
    assert accepted.status_code == 202, accepted.text
    timeline_id = accepted.json()["timeline_id"]
    job_id = accepted.json()["job_id"]

    assert app.state.runner.run_once() is True
    job = repos.get_job(db, job_id)
    assert job is not None and job["status"] == "succeeded", job
    result = json.loads(job["result_json"])

    assert result["warnings"] == []
    assert result["export_id"] is None
    assert [b["measured_frames"] for b in result["beats"]] == [30, 60, 90]

    clips = repos.list_timeline_clips(db, timeline_id)
    assert len(clips) == 3
    clips.sort(key=lambda c: c["seq_in_frame"])

    assert clips[0]["asset_id"] == asset_a["id"]
    assert clips[0]["src_in_frame"] == 0
    assert clips[0]["src_out_frame_exclusive"] == 42  # 30 + 12
    assert clips[0]["seq_in_frame"] == 0
    assert clips[0]["seq_out_frame_exclusive"] == 42
    assert clips[0]["transition_after_kind"] == "crossfade"
    assert clips[0]["transition_after_frames"] == 8

    assert clips[1]["asset_id"] == asset_b["id"]
    assert clips[1]["src_in_frame"] == 0
    assert clips[1]["src_out_frame_exclusive"] == 72  # 60 + 12
    assert clips[1]["seq_in_frame"] == 42
    assert clips[1]["seq_out_frame_exclusive"] == 114
    assert clips[1]["transition_after_kind"] == "crossfade"
    assert clips[1]["transition_after_frames"] == 8

    assert clips[2]["asset_id"] == asset_a["id"]
    assert clips[2]["src_in_frame"] == 500
    assert clips[2]["src_out_frame_exclusive"] == 602  # 500 + 90 + 12
    assert clips[2]["seq_in_frame"] == 114
    assert clips[2]["seq_out_frame_exclusive"] == 216
    assert clips[2]["transition_after_kind"] == "fade"
    assert clips[2]["transition_after_frames"] == 12

    # beats[i]["clip_id"] must resolve back to the matching clip row.
    clip_ids = {c["id"] for c in clips}
    assert {b["clip_id"] for b in result["beats"]} == clip_ids

    vo_clips = repos.list_timeline_audio_clips(db, timeline_id)
    assert len(vo_clips) == 3
    vo_clips.sort(key=lambda c: c["seq_in_frame"])
    expected_spans = [(0, 42), (42, 114), (114, 216)]
    for idx, (vo, (seq_in, seq_out)) in enumerate(zip(vo_clips, expected_spans, strict=True)):
        assert vo["mix_mode"] == "replace_original"
        assert vo["ducking_percent"] == 100
        assert vo["gain_percent"] == 100
        assert vo["fade_in_frames"] == 3
        assert vo["fade_out_frames"] == 4
        assert vo["label"] == f"reel-beat-{idx}"
        assert vo["seq_in_frame"] == seq_in
        assert vo["seq_out_frame_exclusive"] == seq_out

        vo_asset = repos.get_asset(db, vo["asset_id"])
        assert vo_asset is not None
        sidecar = Path(f"{vo_asset['source_path']}.words.json")
        assert sidecar.exists()


# ---------------------------------------------------------------------------
# (c) render=True chains an export.render job with the caption options
# ---------------------------------------------------------------------------


def test_narrated_reel_render_true_chains_export_render_job(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = cast(Any, client.app)
    db: Database = app.state.db
    project = _seed_project(db, tmp_path)
    asset = _seed_video_asset(db, project, tmp_path, name="a", duration_frames=1000)

    _patch_queued_backend(monkeypatch, [40])

    body = {
        "beats": [_beat(asset, text="one")],
        "backend": "stub",
        "render": True,
        "caption_preset": "tiktok",
    }
    accepted = client.post(f"/projects/{project['id']}/narrated-reel", json=body)
    assert accepted.status_code == 202, accepted.text
    timeline_id = accepted.json()["timeline_id"]

    assert app.state.runner.run_once() is True
    job = repos.get_job(db, accepted.json()["job_id"])
    assert job is not None and job["status"] == "succeeded", job
    result = json.loads(job["result_json"])

    export_id = result["export_id"]
    assert export_id is not None
    export = repos.get_export(db, export_id)
    assert export is not None
    assert export["timeline_id"] == timeline_id
    options = export["options"]
    assert options["captions"] is True
    assert options["caption_source"] == "voiceover"
    assert options["caption_preset"] == "tiktok"

    render_jobs = repos.list_jobs_of_kind(db, "export.render")
    matching = [
        j for j in render_jobs if json.loads(j["payload_json"])["export_id"] == export_id
    ]
    assert len(matching) == 1


# ---------------------------------------------------------------------------
# (d) a beat overrunning its source asset clips to the asset end + warns
# ---------------------------------------------------------------------------


def test_narrated_reel_beat_clips_to_asset_end_with_warning(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = cast(Any, client.app)
    db: Database = app.state.db
    project = _seed_project(db, tmp_path)
    # duration=100, src_in=80 -> remaining=20; measured(15)+pad(12)=27 > 20 -> clamps to 20.
    asset = _seed_video_asset(db, project, tmp_path, name="a", duration_frames=100)

    _patch_queued_backend(monkeypatch, [15])

    body = {
        "beats": [_beat(asset, text="one", src_in_frame=80, pad_frames=12)],
        "backend": "stub",
        "render": False,
    }
    accepted = client.post(f"/projects/{project['id']}/narrated-reel", json=body)
    assert accepted.status_code == 202, accepted.text
    timeline_id = accepted.json()["timeline_id"]

    assert app.state.runner.run_once() is True
    job = repos.get_job(db, accepted.json()["job_id"])
    assert job is not None and job["status"] == "succeeded", job
    result = json.loads(job["result_json"])

    assert result["warnings"] == ["beat 0: clipped to asset end"]

    clips = repos.list_timeline_clips(db, timeline_id)
    assert len(clips) == 1
    assert clips[0]["src_in_frame"] == 80
    assert clips[0]["src_out_frame_exclusive"] == 100  # clamped to the asset end


# ---------------------------------------------------------------------------
# (e) cancel between beat 0 and beat 1 -> clean abort, no half voice track
# ---------------------------------------------------------------------------


def test_narrated_reel_cancel_between_beats_aborts_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from laura.config import Settings
    from laura.db.database import SqliteDatabase

    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()

    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(tmp_path / "ws" / "p"),
    )
    asset_a = _seed_video_asset(db, project, tmp_path, name="a", duration_frames=1000)
    asset_b = _seed_video_asset(db, project, tmp_path, name="b", duration_frames=1000)
    timeline = repos.create_timeline(
        db, project_id=project["id"], name="Narrated Reel", kind="rough_cut"
    )

    _patch_queued_backend(monkeypatch, [40, 60])

    real_synthesize = ai_handlers._synthesize_voiceover_asset
    calls = {"n": 0}

    def _wrapped(ctx: JobContext, **kwargs: Any) -> Any:
        out = real_synthesize(ctx, **kwargs)
        calls["n"] += 1
        if calls["n"] == 1:
            # Simulate a cancel request arriving right after beat 0 finished, before
            # beat 1's cancel-check runs.
            repos.cancel_job(ctx.db, ctx.job_id)
        return out

    monkeypatch.setattr(ai_handlers, "_synthesize_voiceover_asset", _wrapped)

    payload: dict[str, Any] = {
        "timeline_id": timeline["id"],
        "project_id": project["id"],
        "beats": [
            _beat(asset_a, text="one", src_in_frame=0, pad_frames=12),
            _beat(asset_b, text="two", src_in_frame=0, pad_frames=12),
        ],
        "crossfade_frames": 8,
        "final_fade_frames": 12,
        "backend": "stub",
        "voice_id": None,
        "language": None,
        "runtime_id": None,
        "render": True,
        "caption_preset": "wide",
    }
    job_id = enqueue(
        db,
        queue=queue_for("ai.narrated_reel", default="ai"),
        kind="ai.narrated_reel",
        payload=payload,
        max_attempts=1,
    )
    ctx = JobContext(
        job_id=job_id,
        kind="ai.narrated_reel",
        queue=queue_for("ai.narrated_reel", default="ai"),
        payload=payload,
        db=db,
    )

    result = ai_handlers.handle_narrated_reel(ctx)

    assert result["status"] == "cancelled"
    assert result["export_id"] is None
    assert len(result["beats"]) == 1
    assert result["beats"][0]["clip_id"]

    clips = repos.list_timeline_clips(db, timeline["id"])
    assert len(clips) == 1
    assert clips[0]["asset_id"] == asset_a["id"]

    vo_clips = repos.list_timeline_audio_clips(db, timeline["id"])
    assert len(vo_clips) == 1
    assert vo_clips[0]["label"] == "reel-beat-0"

    # No export.render job should have been enqueued -- cancellation happens before the
    # render step is ever reached.
    render_jobs = repos.list_jobs_of_kind(db, "export.render")
    assert render_jobs == []


# ---------------------------------------------------------------------------
# (f) a mid-build exception rolls the whole timeline back to its pre-job state
# ---------------------------------------------------------------------------


def test_narrated_reel_exception_mid_build_rolls_back_to_pre_job_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Spec's Fehlerfälle: "Timeline bleibt im letzten konsistenten Zustand (Checkpoint
    vor dem Job)". Beat 2's synthesis raises after beats 0 and 1 already committed their
    clips -- the handler must undo ALL of it (not just skip beat 2), then re-raise the
    original error so the job still fails."""
    from laura.config import Settings
    from laura.db.database import SqliteDatabase

    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()

    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(tmp_path / "ws" / "p"),
    )
    asset_a = _seed_video_asset(db, project, tmp_path, name="a", duration_frames=1000)
    asset_b = _seed_video_asset(db, project, tmp_path, name="b", duration_frames=1000)
    timeline = repos.create_timeline(
        db, project_id=project["id"], name="Narrated Reel", kind="rough_cut"
    )

    _patch_queued_backend(monkeypatch, [30, 60])

    real_synthesize = ai_handlers._synthesize_voiceover_asset
    calls = {"n": 0}

    def _wrapped(ctx: JobContext, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("synthesis backend exploded on beat 2")
        return real_synthesize(ctx, **kwargs)

    monkeypatch.setattr(ai_handlers, "_synthesize_voiceover_asset", _wrapped)

    payload: dict[str, Any] = {
        "timeline_id": timeline["id"],
        "project_id": project["id"],
        "beats": [
            _beat(asset_a, text="one", src_in_frame=0, pad_frames=12),
            _beat(asset_b, text="two", src_in_frame=0, pad_frames=12),
            _beat(asset_a, text="three", src_in_frame=500, pad_frames=12),
        ],
        "crossfade_frames": 8,
        "final_fade_frames": 12,
        "backend": "stub",
        "voice_id": None,
        "language": None,
        "runtime_id": None,
        "render": True,
        "caption_preset": "wide",
    }
    job_id = enqueue(
        db,
        queue=queue_for("ai.narrated_reel", default="ai"),
        kind="ai.narrated_reel",
        payload=payload,
        max_attempts=1,
    )
    ctx = JobContext(
        job_id=job_id,
        kind="ai.narrated_reel",
        queue=queue_for("ai.narrated_reel", default="ai"),
        payload=payload,
        db=db,
    )

    with pytest.raises(RuntimeError, match="synthesis backend exploded"):
        ai_handlers.handle_narrated_reel(ctx)

    # Beats 0 and 1 DID commit their clips before beat 2 blew up -- the rollback must
    # remove them too, not just skip the failed beat.
    assert repos.list_timeline_clips(db, timeline["id"]) == []
    assert repos.list_timeline_audio_clips(db, timeline["id"]) == []
    assert repos.list_jobs_of_kind(db, "export.render") == []


# ---------------------------------------------------------------------------
# (g) idempotency is reachable: identical requests dedupe BEFORE a timeline is created
# ---------------------------------------------------------------------------


def test_narrated_reel_identical_requests_dedupe_to_one_job(
    client: TestClient, tmp_path: Path
) -> None:
    app = cast(Any, client.app)
    db: Database = app.state.db
    project = _seed_project(db, tmp_path)
    asset = _seed_video_asset(db, project, tmp_path, name="a", duration_frames=1000)

    body = {"beats": [_beat(asset, text="hello there")], "backend": "stub"}

    first = client.post(f"/projects/{project['id']}/narrated-reel", json=body)
    assert first.status_code == 202, first.text
    second = client.post(f"/projects/{project['id']}/narrated-reel", json=body)
    assert second.status_code == 202, second.text

    assert first.json() == second.json()

    assert len(repos.list_timelines(db, project["id"])) == 1
    assert len(repos.list_jobs_of_kind(db, "ai.narrated_reel")) == 1


def test_narrated_reel_changed_beat_text_creates_a_new_job(
    client: TestClient, tmp_path: Path
) -> None:
    app = cast(Any, client.app)
    db: Database = app.state.db
    project = _seed_project(db, tmp_path)
    asset = _seed_video_asset(db, project, tmp_path, name="a", duration_frames=1000)

    first = client.post(
        f"/projects/{project['id']}/narrated-reel",
        json={"beats": [_beat(asset, text="hello there")], "backend": "stub"},
    )
    assert first.status_code == 202, first.text
    second = client.post(
        f"/projects/{project['id']}/narrated-reel",
        json={"beats": [_beat(asset, text="a completely different line")], "backend": "stub"},
    )
    assert second.status_code == 202, second.text

    assert first.json()["job_id"] != second.json()["job_id"]
    assert first.json()["timeline_id"] != second.json()["timeline_id"]

    assert len(repos.list_timelines(db, project["id"])) == 2
    assert len(repos.list_jobs_of_kind(db, "ai.narrated_reel")) == 2
