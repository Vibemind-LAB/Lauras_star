from __future__ import annotations

import json
import wave
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from laura.ai import handlers as ai_handlers
from laura.ai import voiceover_backend as vo_backend
from laura.ai.voiceover_backend import (
    StubVoiceoverBackend,
    WindowsSapiVoiceoverBackend,
    _sapi_voice_select_script,
    list_sapi_voices,
    resolve_voiceover_backend,
)
from laura.db import repos
from laura.db.database import Database


def _seed_sequence_with_segment(
    db: Database,
    workspace: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    project = repos.create_project(
        db,
        name="p",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(workspace),
    )
    timeline = repos.create_timeline(
        db,
        project_id=project["id"],
        name="sequence",
        kind="sequence",
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="source",
        source_path=str(workspace / "source.mp4"),
    )
    run = repos.create_analysis_run(
        db,
        asset_id=asset["id"],
        pipeline_version="test",
        config={},
    )
    segment_id = repos.insert_segment_with_words(
        db,
        asset_id=asset["id"],
        run_id=run["id"],
        speaker_id=None,
        segment={
            "start_sample": 0,
            "end_sample": 48_000,
            "start_frame": 0,
            "end_frame": 30,
            "text": "Hallo Laura",
            "confidence": 1.0,
        },
        words=[
            {
                "idx": 0,
                "start_sample": 0,
                "end_sample": 24_000,
                "start_frame": 0,
                "end_frame": 15,
                "text": "Hallo",
                "confidence": 1.0,
            },
            {
                "idx": 1,
                "start_sample": 24_000,
                "end_sample": 48_000,
                "start_frame": 15,
                "end_frame": 30,
                "text": "Laura",
                "confidence": 1.0,
            },
        ],
    )
    return project, timeline, asset, segment_id


def test_stub_voiceover_backend_writes_exact_duration_wav(tmp_path: Path) -> None:
    out = tmp_path / "voice.wav"
    backend = resolve_voiceover_backend("stub")

    assert isinstance(backend, StubVoiceoverBackend)
    assert backend.available() is True
    backend.synthesize(
        text="Hallo Laura",
        out_path=out,
        duration_frames=45,
        fps_num=30,
        fps_den=1,
        sample_rate=48_000,
        language="de",
    )

    with wave.open(str(out), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getframerate() == 48_000
        assert wav.getnframes() == 72_000


def test_resolve_backend_sapi_and_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAURA_VOICEOVER_BACKEND", raising=False)
    # Explicit names route to the right backend.
    assert isinstance(resolve_voiceover_backend("sapi"), WindowsSapiVoiceoverBackend)
    assert isinstance(resolve_voiceover_backend("stub"), StubVoiceoverBackend)
    # "auto" prefers SAPI when available, else the dependency-free stub.
    monkeypatch.setattr(vo_backend, "_sapi_available", lambda: True)
    assert isinstance(resolve_voiceover_backend("auto"), WindowsSapiVoiceoverBackend)
    monkeypatch.setattr(vo_backend, "_sapi_available", lambda: False)
    assert isinstance(resolve_voiceover_backend("auto"), StubVoiceoverBackend)
    # Default (no name, no env) stays the safe stub.
    assert isinstance(resolve_voiceover_backend(), StubVoiceoverBackend)


def test_sapi_voice_select_script() -> None:
    # Explicit voice -> SelectVoice with a single-quote-escaped name.
    assert "SelectVoice('Microsoft Katja')" in _sapi_voice_select_script("Microsoft Katja", None)
    assert "O''Brien" in _sapi_voice_select_script("O'Brien", None)
    # Language only -> culture-prefix match over installed voices.
    by_lang = _sapi_voice_select_script(None, "de-DE")
    assert "GetInstalledVoices" in by_lang and "de-DE*" in by_lang
    # Explicit voice wins over language; nothing requested -> no selection (system default).
    explicit = _sapi_voice_select_script("Microsoft Stefan", "en-US")
    assert "SelectVoice('Microsoft Stefan')" in explicit
    assert _sapi_voice_select_script(None, None) == ""


@pytest.mark.skipif(
    not WindowsSapiVoiceoverBackend().available(),
    reason="Windows System.Speech (SAPI) not available on this host",
)
def test_list_sapi_voices_returns_installed_voices() -> None:
    voices = list_sapi_voices()
    assert voices, "expected at least one installed SAPI voice"
    assert all({"name", "culture", "gender"} <= set(v) for v in voices)
    assert all(v["name"] for v in voices)


@pytest.mark.skipif(
    not WindowsSapiVoiceoverBackend().available(),
    reason="Windows System.Speech (SAPI) not available on this host",
)
def test_sapi_backend_synthesizes_real_speech_wav(tmp_path: Path) -> None:
    """On a Windows host, the SAPI backend renders real speech and fits the requested slot."""
    out = tmp_path / "sapi.wav"
    WindowsSapiVoiceoverBackend().synthesize(
        text="Hello students, this is a Laura voiceover test.",
        out_path=out,
        duration_frames=90,
        fps_num=30,
        fps_den=1,
        sample_rate=48_000,
    )
    with wave.open(str(out), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getframerate() == 48_000
        assert wav.getnframes() > 0
        # Real speech is broadband, not the stub's constant tone: a chunk near the start should
        # contain meaningful (non-silent) signal.
        frames = wav.readframes(min(wav.getnframes(), 48_000))
    assert any(b != 0 for b in frames)


def test_voiceover_api_enqueues_job_and_places_synthetic_audio_clip(
    client: TestClient,
    tmp_path: Path,
) -> None:
    app = cast(Any, client.app)
    db: Database = app.state.db
    _, timeline, _, segment_id = _seed_sequence_with_segment(db, tmp_path)

    accepted = client.post(
        f"/timelines/{timeline['id']}/voiceover",
        json={
            "segment_id": segment_id,
            "seq_in_frame": 12,
            "seq_out_frame_exclusive": 72,
            "backend": "stub",
        },
    )
    assert accepted.status_code == 202, accepted.text
    job_id = accepted.json()["job_id"]

    assert app.state.runner.run_once() is True
    job = repos.get_job(db, job_id)
    assert job is not None
    assert job["status"] == "succeeded"
    result = json.loads(job["result_json"])

    asset = repos.get_asset(db, result["asset_id"])
    assert asset is not None
    assert asset["type"] == "audio"
    assert asset["synthetic"] == 1
    assert asset["ai_effect"] == "voiceover"
    assert asset["duration_frames"] == 60
    assert asset["audio_sample_rate"] == 48_000
    assert Path(asset["source_path"]).exists()

    clips = repos.list_timeline_audio_clips(db, timeline["id"])
    assert len(clips) == 1
    assert clips[0]["asset_id"] == asset["id"]
    assert clips[0]["seq_in_frame"] == 12
    assert clips[0]["seq_out_frame_exclusive"] == 72
    assert clips[0]["label"] == "Voiceover"


def test_voiceover_job_writes_word_timing_sidecar(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Every successful voiceover synthesis gets a ``<wav>.words.json`` sidecar next to
    it (spec §4). Whisper is force-disabled here (regardless of whether faster-whisper
    happens to be installed in this environment) so this deterministically exercises the
    even-distribution fallback -- the whisper mapping itself is covered by
    test_vo_words.py."""
    from laura.ai import vo_words

    monkeypatch.setattr(vo_words, "_transcribe_words", lambda *_a, **_k: None)

    app = cast(Any, client.app)
    db: Database = app.state.db
    _, timeline, _, segment_id = _seed_sequence_with_segment(db, tmp_path)

    accepted = client.post(
        f"/timelines/{timeline['id']}/voiceover",
        json={
            "segment_id": segment_id,
            "seq_in_frame": 12,
            "seq_out_frame_exclusive": 72,
            "backend": "stub",
        },
    )
    assert accepted.status_code == 202, accepted.text
    assert app.state.runner.run_once() is True
    job = repos.get_job(db, accepted.json()["job_id"])
    assert job is not None and job["status"] == "succeeded", job
    result = json.loads(job["result_json"])

    asset = repos.get_asset(db, result["asset_id"])
    assert asset is not None
    wav_path = Path(asset["source_path"])
    sidecar_path = Path(f"{wav_path}.words.json")
    assert sidecar_path.exists()

    payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert payload["source"] == "even"
    assert [w["text"] for w in payload["words"]] == ["Hallo", "Laura"]
    for word in payload["words"]:
        assert word["end_frame_exclusive"] > word["start_frame"]


def test_voiceover_honors_requested_mix_mode_and_ducking(
    client: TestClient,
    tmp_path: Path,
) -> None:
    """A voice-over carries the caller's mix treatment onto its A2 clip instead of the old
    hardcoded full-volume 'mix' — so the original audio can be ducked or replaced under it."""
    app = cast(Any, client.app)
    db: Database = app.state.db
    _, timeline, _, segment_id = _seed_sequence_with_segment(db, tmp_path)

    accepted = client.post(
        f"/timelines/{timeline['id']}/voiceover",
        json={
            "segment_id": segment_id,
            "seq_in_frame": 12,
            "seq_out_frame_exclusive": 72,
            "backend": "stub",
            "mix_mode": "replace_original",
            "ducking_percent": 20,
        },
    )
    assert accepted.status_code == 202, accepted.text
    assert app.state.runner.run_once() is True

    clips = repos.list_timeline_audio_clips(db, timeline["id"])
    assert len(clips) == 1
    assert clips[0]["mix_mode"] == "replace_original"
    assert clips[0]["ducking_percent"] == 20


def test_voiceover_defaults_to_mix_when_unspecified(
    client: TestClient,
    tmp_path: Path,
) -> None:
    """Omitting the mix treatment keeps the backward-compatible default (mix, no ducking)."""
    app = cast(Any, client.app)
    db: Database = app.state.db
    _, timeline, _, segment_id = _seed_sequence_with_segment(db, tmp_path)

    accepted = client.post(
        f"/timelines/{timeline['id']}/voiceover",
        json={
            "segment_id": segment_id,
            "seq_in_frame": 12,
            "seq_out_frame_exclusive": 72,
            "backend": "stub",
        },
    )
    assert accepted.status_code == 202, accepted.text
    assert app.state.runner.run_once() is True

    clips = repos.list_timeline_audio_clips(db, timeline["id"])
    assert clips[0]["mix_mode"] == "mix"
    assert clips[0]["ducking_percent"] == 100


def test_voiceover_job_writes_provenance_manifest(
    client: TestClient,
    tmp_path: Path,
) -> None:
    app = cast(Any, client.app)
    db: Database = app.state.db
    _, timeline, _, segment_id = _seed_sequence_with_segment(db, tmp_path)

    accepted = client.post(
        f"/timelines/{timeline['id']}/voiceover",
        json={
            "segment_id": segment_id,
            "seq_in_frame": 12,
            "seq_out_frame_exclusive": 72,
            "backend": "stub",
        },
    )
    assert accepted.status_code == 202, accepted.text
    assert app.state.runner.run_once() is True
    job = repos.get_job(db, accepted.json()["job_id"])
    assert job is not None
    result = json.loads(job["result_json"])
    asset = repos.get_asset(db, result["asset_id"])
    assert asset is not None

    manifest_path = Path(f"{asset['source_path']}.laura-provenance.json")
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "laura.ai.provenance.v1"
    assert manifest["asset_id"] == asset["id"]
    assert manifest["project_id"] == timeline["project_id"]
    assert manifest["ai_effect"] == "voiceover"
    assert manifest["synthetic"] is True
    assert manifest["source"]["timeline_id"] == timeline["id"]
    assert manifest["source"]["segment_id"] == segment_id
    assert manifest["source"]["seq_in_frame"] == 12
    assert manifest["source"]["seq_out_frame_exclusive"] == 72


def test_voiceover_job_runs_sync_guard_for_generated_wav(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    def fake_assert_sync(path: Path, **kwargs: Any) -> object:
        captured["path"] = path
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(ai_handlers, "assert_or_fix_media_sync", fake_assert_sync)

    app = cast(Any, client.app)
    db: Database = app.state.db
    _, timeline, _, segment_id = _seed_sequence_with_segment(db, tmp_path)

    accepted = client.post(
        f"/timelines/{timeline['id']}/voiceover",
        json={
            "segment_id": segment_id,
            "seq_in_frame": 12,
            "seq_out_frame_exclusive": 72,
            "backend": "stub",
        },
    )
    assert accepted.status_code == 202, accepted.text

    assert app.state.runner.run_once() is True

    assert captured["path"].name.endswith(".voiceover.wav")
    assert captured["kwargs"] == {
        "expected_frames": 60,
        "rate_num": 30,
        "rate_den": 1,
        "require_audio": True,
        "fix": True,
    }


def test_voiceover_rejects_segment_from_other_project(client: TestClient, tmp_path: Path) -> None:
    app = cast(Any, client.app)
    db: Database = app.state.db
    _, _, _, segment_id = _seed_sequence_with_segment(db, tmp_path / "one")
    other_project = repos.create_project(
        db,
        name="other",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(tmp_path / "two"),
    )
    other_timeline = repos.create_timeline(
        db,
        project_id=other_project["id"],
        name="sequence",
        kind="sequence",
    )

    response = client.post(
        f"/timelines/{other_timeline['id']}/voiceover",
        json={
            "segment_id": segment_id,
            "seq_in_frame": 0,
            "seq_out_frame_exclusive": 30,
        },
    )

    assert response.status_code == 422
    assert "segment does not belong to this timeline project" in response.text


def test_voiceover_asset_exposes_original_file(
    client: TestClient,
    tmp_path: Path,
) -> None:
    """The VO asset must have an `original` asset_file row so the Electron
    laura-media protocol can serve it to the AudioMixer."""
    app = cast(Any, client.app)
    db: Database = app.state.db
    _, timeline, _, segment_id = _seed_sequence_with_segment(db, tmp_path)

    accepted = client.post(
        f"/timelines/{timeline['id']}/voiceover",
        json={
            "segment_id": segment_id,
            "seq_in_frame": 0,
            "seq_out_frame_exclusive": 30,
            "backend": "stub",
        },
    )
    assert accepted.status_code == 202, accepted.text
    assert app.state.runner.run_once() is True

    result = json.loads(repos.get_job(db, accepted.json()["job_id"])["result_json"])  # type: ignore[index]
    files = repos.list_asset_files(db, result["asset_id"])
    kinds = {f["kind"] for f in files}
    assert "original" in kinds, f"expected 'original' in asset_files, got: {kinds}"
    original = next(f for f in files if f["kind"] == "original")
    assert Path(original["path"]).exists(), "original file must exist on disk"
