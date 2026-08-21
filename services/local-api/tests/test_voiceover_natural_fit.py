"""TDD for Task 2 — natural-length fit derives the voiceover clip span from speech.

Spec: docs/superpowers/specs/2026-08-21-narrated-reel-design.md §3.

The Stub backend always writes exactly ``duration_frames`` worth of audio regardless of
``fit_to_slot`` (see ``StubVoiceoverBackend.synthesize``), so a plain stub run can never
exercise the measurement path -- the handler always passes the SLOT duration as the
``duration_frames`` hint (even in natural mode), so a stub run would trivially "measure"
back the slot width it was told to write. To actually exercise ffprobe-based measurement,
these tests monkeypatch ``resolve_voiceover_backend`` to return a fake backend that always
writes a WAV of a known, fixed length -- independent of whatever ``duration_frames`` hint
the handler passes in.

Test plan (brief (a)-(d)):
  (a) fit="natural", backend writes 45 frames, pad=12, slot=300 -> clip ends at
      seq_in+57; result reports measured_frames=45.
  (b) speech longer than the slot -> clip ends exactly at the slot end (upper bound).
  (c) fit="slot" (default, omitted) -> unchanged slot behavior (regression sanity;
      the full existing suite in test_voiceover.py/test_voiceover_dedup.py/
      test_voiceover_undo_checkpoint.py is the primary regression net).
  (d) overlap-delete for replace_original/mute_original runs against the EFFECTIVE
      (clamped) span, not the originally requested one.
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


class _FixedLengthBackend:
    """A fake voiceover backend that always writes a WAV of a known, fixed length.

    Ignores whatever ``duration_frames`` hint it is called with -- this is the whole
    point: it lets a test assert that the handler *measured* the real WAV instead of
    trusting the hint it passed in.
    """

    name = "stub"

    def __init__(self, frames: int) -> None:
        self._frames = frames

    def available(self) -> bool:
        return True

    def synthesize(
        self,
        *,
        text: str,
        out_path: Path,
        duration_frames: int,  # noqa: ARG002 - intentionally ignored, see class docstring
        fps_num: int,
        fps_den: int,
        sample_rate: int,
        language: str | None = None,
        voice_id: str | None = None,
        fit_to_slot: bool = True,
    ) -> None:
        StubVoiceoverBackend().synthesize(
            text=text,
            out_path=out_path,
            duration_frames=self._frames,
            fps_num=fps_num,
            fps_den=fps_den,
            sample_rate=sample_rate,
            language=language,
            voice_id=voice_id,
            fit_to_slot=fit_to_slot,
        )


def _patch_fixed_length_backend(monkeypatch: pytest.MonkeyPatch, frames: int) -> None:
    monkeypatch.setattr(
        ai_handlers,
        "resolve_voiceover_backend",
        lambda *_args, **_kwargs: _FixedLengthBackend(frames),
    )


def _seed_project_and_sequence(
    db: Database, tmp_path: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    project = repos.create_project(
        db,
        name="p",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(tmp_path / "ws"),
    )
    timeline = repos.create_timeline(
        db, project_id=project["id"], name="sequence", kind="sequence"
    )
    return project, timeline


def _post_voiceover(client: TestClient, timeline_id: str, **overrides: Any) -> Any:
    body: dict[str, Any] = {
        "text": "Hallo Laura",
        "seq_in_frame": 0,
        "seq_out_frame_exclusive": 300,
        "backend": "stub",
    }
    body.update(overrides)
    return client.post(f"/timelines/{timeline_id}/voiceover", json=body)


# ---------------------------------------------------------------------------
# (a) natural fit: clip span = measured speech + pad, well inside the slot
# ---------------------------------------------------------------------------


def test_natural_fit_derives_span_from_measured_speech(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app = cast(Any, client.app)
    db: Database = app.state.db
    _, timeline = _seed_project_and_sequence(db, tmp_path)
    _patch_fixed_length_backend(monkeypatch, frames=45)

    accepted = _post_voiceover(
        client,
        timeline["id"],
        seq_in_frame=0,
        seq_out_frame_exclusive=300,
        fit="natural",
        pad_frames=12,
    )
    assert accepted.status_code == 202, accepted.text
    assert app.state.runner.run_once() is True

    job = repos.get_job(db, accepted.json()["job_id"])
    assert job is not None and job["status"] == "succeeded", job
    result = json.loads(job["result_json"])

    assert result["measured_frames"] == 45
    assert result["seq_out_frame_exclusive"] == 57  # seq_in(0) + measured(45) + pad(12)

    clips = repos.list_timeline_audio_clips(db, timeline["id"])
    assert len(clips) == 1
    assert clips[0]["seq_in_frame"] == 0
    assert clips[0]["seq_out_frame_exclusive"] == 57

    asset = repos.get_asset(db, result["asset_id"])
    assert asset is not None
    assert asset["duration_frames"] == 45


# ---------------------------------------------------------------------------
# (b) speech longer than the slot: clip clamps to the slot end (upper bound)
# ---------------------------------------------------------------------------


def test_natural_fit_clamps_to_slot_upper_bound(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app = cast(Any, client.app)
    db: Database = app.state.db
    _, timeline = _seed_project_and_sequence(db, tmp_path)
    # 150 measured frames + 12 pad = 162, which overshoots a 100-frame slot.
    _patch_fixed_length_backend(monkeypatch, frames=150)

    accepted = _post_voiceover(
        client,
        timeline["id"],
        seq_in_frame=0,
        seq_out_frame_exclusive=100,
        fit="natural",
        pad_frames=12,
    )
    assert accepted.status_code == 202, accepted.text
    assert app.state.runner.run_once() is True

    job = repos.get_job(db, accepted.json()["job_id"])
    assert job is not None and job["status"] == "succeeded", job
    result = json.loads(job["result_json"])

    assert result["measured_frames"] == 150  # raw measurement, unclamped
    assert result["seq_out_frame_exclusive"] == 100  # clamped to the requested slot end

    clips = repos.list_timeline_audio_clips(db, timeline["id"])
    assert len(clips) == 1
    assert clips[0]["seq_out_frame_exclusive"] == 100


# ---------------------------------------------------------------------------
# (c) fit="slot" (default/omitted) stays byte-identical to today's behavior
# ---------------------------------------------------------------------------


def test_slot_fit_default_is_unchanged(
    client: TestClient,
    tmp_path: Path,
) -> None:
    """No fit given -> defaults to 'slot': the real Stub backend writes exactly the
    requested slot width, and the clip lands exactly on [seq_in, seq_out_requested)."""
    app = cast(Any, client.app)
    db: Database = app.state.db
    _, timeline = _seed_project_and_sequence(db, tmp_path)

    accepted = _post_voiceover(
        client,
        timeline["id"],
        seq_in_frame=10,
        seq_out_frame_exclusive=70,
    )
    assert accepted.status_code == 202, accepted.text
    assert app.state.runner.run_once() is True

    job = repos.get_job(db, accepted.json()["job_id"])
    assert job is not None and job["status"] == "succeeded", job
    result = json.loads(job["result_json"])

    assert result["measured_frames"] == 60  # == requested slot width, not a real probe
    assert result["seq_out_frame_exclusive"] == 70

    clips = repos.list_timeline_audio_clips(db, timeline["id"])
    assert len(clips) == 1
    assert clips[0]["seq_in_frame"] == 10
    assert clips[0]["seq_out_frame_exclusive"] == 70

    asset = repos.get_asset(db, result["asset_id"])
    assert asset is not None
    assert asset["duration_frames"] == 60


# ---------------------------------------------------------------------------
# (d) overlap-delete runs against the EFFECTIVE (clamped) span
# ---------------------------------------------------------------------------


def test_natural_fit_overlap_delete_uses_effective_span(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A pre-existing replace_original clip beyond the EFFECTIVE natural-fit end (but
    still inside the originally requested slot) must survive: overlap-delete must key
    off the clamped span, not the wide requested one."""
    app = cast(Any, client.app)
    db: Database = app.state.db
    _, timeline = _seed_project_and_sequence(db, tmp_path)

    audio_asset = repos.create_asset(
        db,
        project_id=timeline["project_id"],
        type="audio",
        display_name="other.wav",
        source_path=str(tmp_path / "other.wav"),
    )
    survivor = repos.add_timeline_audio_clip(
        db,
        timeline_id=timeline["id"],
        asset_id=audio_asset["id"],
        seq_in_frame=100,
        seq_out_frame_exclusive=150,
        mix_mode="replace_original",
    )

    _patch_fixed_length_backend(monkeypatch, frames=45)
    accepted = _post_voiceover(
        client,
        timeline["id"],
        seq_in_frame=0,
        seq_out_frame_exclusive=300,
        fit="natural",
        pad_frames=12,
        mix_mode="replace_original",
    )
    assert accepted.status_code == 202, accepted.text
    assert app.state.runner.run_once() is True

    job = repos.get_job(db, accepted.json()["job_id"])
    assert job is not None and job["status"] == "succeeded", job
    result = json.loads(job["result_json"])
    assert result["seq_out_frame_exclusive"] == 57  # effective span: [0, 57)

    clips = repos.list_timeline_audio_clips(db, timeline["id"])
    ids = {c["id"] for c in clips}
    assert survivor["id"] in ids, (
        "overlap-delete must use the effective (clamped) span [0, 57), not the "
        "originally requested [0, 300) -- the pre-existing clip at [100, 150) does "
        "not overlap the effective span and must survive"
    )
    replace_clips = [c for c in clips if c["mix_mode"] == "replace_original"]
    assert len(replace_clips) == 2  # the survivor + the new VO clip
