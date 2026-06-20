"""Integration tests for the ai.reenact job handler (RC3).

Uses real ffmpeg (stub backend).  Skipped if ffmpeg is unavailable.

Test (a) — CONSENT GATE (safety-critical):
    Ensures the handler raises and creates NOTHING when consent is missing
    or the key is absent from the payload.

Test (b) — Stub e2e:
    End-to-end with StubReenactBackend: verifies asset, clip, and file on disk.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from laura.ai import handlers as ai_handlers
from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.editing.otio_sync import resolve_clip_rows
from laura.ingest.ffmpeg import run_ffmpeg
from laura.jobs import JobRunner, default_registry, enqueue

_FFMPEG_BIN = os.environ.get("LAURA_FFMPEG", "ffmpeg")

pytestmark = pytest.mark.skipif(
    shutil.which(_FFMPEG_BIN) is None,
    reason="ffmpeg not available on PATH",
)


# ── helpers ────────────────────────────────────────────────────────────────────

def _drain(runner: JobRunner, limit: int = 60) -> int:
    ran = 0
    while runner.run_once():
        ran += 1
        if ran >= limit:
            break
    return ran


def _make_registry() -> dict:  # type: ignore[type-arg]
    from laura.ai.handlers import register_ai_handlers
    from laura.render.handlers import register_render_handlers
    registry = default_registry()
    register_render_handlers(registry)
    register_ai_handlers(registry)
    return registry


def _setup_scene(
    tmp_path: Path,
) -> tuple[SqliteDatabase, dict, dict, dict]:  # type: ignore[type-arg]
    """Create a DB, project, a 1-second green video asset, a rough_cut timeline,
    and one base clip covering seq[0, 30) at 30 fps.

    Returns (db, project, asset, timeline).
    """
    media = tmp_path / "base.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=green:s=320x240:r=30:d=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(media),
    ])

    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    proot = settings.workspace_root / "project"
    proot.mkdir(parents=True, exist_ok=True)

    db = SqliteDatabase(settings.db_path)
    db.migrate()

    project = repos.create_project(
        db,
        name="reenact_test",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(proot),
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="base.mp4",
        source_path=str(media),
    )
    tl = repos.create_timeline(
        db,
        project_id=project["id"],
        name="cut",
        kind="rough_cut",
    )
    repos.add_timeline_clip(
        db,
        timeline_id=tl["id"],
        asset_id=asset["id"],
        src_in_frame=0,
        src_out_frame_exclusive=30,
        seq_in_frame=0,
        seq_out_frame_exclusive=30,
    )
    return db, project, asset, tl


# ── (a) Consent gate ───────────────────────────────────────────────────────────

def test_reenact_consent_gate_missing_record(tmp_path: Path) -> None:
    """Enqueue ai.reenact with a non-existent consent_id → job fails, nothing created."""
    db, project, asset, tl = _setup_scene(tmp_path)
    registry = _make_registry()
    runner = JobRunner(db, registry)

    assets_before = repos.list_assets(db, project["id"])

    job_id = enqueue(
        db,
        queue="ai",
        kind="ai.reenact",
        payload={
            "timeline_id": tl["id"],
            "seq_in_frame": 0,
            "seq_out_frame_exclusive": 30,
            "portrait_asset_id": asset["id"],
            "consent_id": "does-not-exist",
            "backend": "stub",
        },
        max_attempts=1,
    )
    _drain(runner)

    # Job must have failed
    with db.connection() as conn:
        row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row is not None
    assert row["status"] == "failed", f"Expected failed, got {row['status']}"

    # No new asset must have been created
    assets_after = repos.list_assets(db, project["id"])
    assert len(assets_after) == len(assets_before), (
        "Consent gate: a synthetic asset was created despite missing consent"
    )

    # No replace clip must have been created
    clips_after = repos.list_timeline_clips(db, tl["id"])
    replace_clips = [c for c in clips_after if c.get("role") == "replace"]
    assert not replace_clips, (
        "Consent gate: a replace-overlay clip was created despite missing consent"
    )


def test_reenact_consent_gate_missing_key(tmp_path: Path) -> None:
    """Enqueue ai.reenact with consent_id key absent → job fails, nothing created."""
    db, project, asset, tl = _setup_scene(tmp_path)
    registry = _make_registry()
    runner = JobRunner(db, registry)

    assets_before = repos.list_assets(db, project["id"])

    job_id = enqueue(
        db,
        queue="ai",
        kind="ai.reenact",
        payload={
            "timeline_id": tl["id"],
            "seq_in_frame": 0,
            "seq_out_frame_exclusive": 30,
            "portrait_asset_id": asset["id"],
            # consent_id intentionally omitted
            "backend": "stub",
        },
        max_attempts=1,
    )
    _drain(runner)

    with db.connection() as conn:
        row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row is not None
    assert row["status"] == "failed", f"Expected failed, got {row['status']}"

    assets_after = repos.list_assets(db, project["id"])
    assert len(assets_after) == len(assets_before), (
        "Consent gate (missing key): a synthetic asset was created despite missing consent"
    )

    clips_after = repos.list_timeline_clips(db, tl["id"])
    replace_clips = [c for c in clips_after if c.get("role") == "replace"]
    assert not replace_clips, (
        "Consent gate (missing key): a replace-overlay clip was created despite missing consent"
    )


def test_reenact_consent_gate_revoked(tmp_path: Path) -> None:
    """A REVOKED consent must be refused — withdrawn consent creates nothing."""
    db, project, asset, tl = _setup_scene(tmp_path)
    registry = _make_registry()
    runner = JobRunner(db, registry)

    consent = repos.create_consent_record(
        db, project_id=project["id"], subject_label="Withdrawn", confirmed_by="test",
    )
    assert repos.revoke_consent_record(db, consent["id"]) is True

    assets_before = repos.list_assets(db, project["id"])

    job_id = enqueue(
        db,
        queue="ai",
        kind="ai.reenact",
        payload={
            "timeline_id": tl["id"],
            "seq_in_frame": 0,
            "seq_out_frame_exclusive": 30,
            "portrait_asset_id": asset["id"],
            "consent_id": consent["id"],
            "backend": "stub",
        },
        max_attempts=1,
    )
    _drain(runner)

    with db.connection() as conn:
        row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row is not None
    assert row["status"] == "failed", f"Expected failed, got {row['status']}"

    assert len(repos.list_assets(db, project["id"])) == len(assets_before), (
        "Consent gate: a synthetic asset was created despite REVOKED consent"
    )
    replace_clips = [
        c for c in repos.list_timeline_clips(db, tl["id"]) if c.get("role") == "replace"
    ]
    assert not replace_clips, (
        "Consent gate: a replace-overlay clip was created despite REVOKED consent"
    )


# ── (b) Stub e2e ───────────────────────────────────────────────────────────────

def test_reenact_stub_e2e(tmp_path: Path) -> None:
    """Full pipeline with StubReenactBackend: valid consent → synthetic asset + replace clip."""
    db, project, base_asset, tl = _setup_scene(tmp_path)

    # Portrait asset (reuse the same green video; stub ignores portrait content)
    portrait = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="portrait.mp4",
        source_path=str(tmp_path / "base.mp4"),
    )

    consent = repos.create_consent_record(
        db,
        project_id=project["id"],
        subject_label="Test Subject",
        confirmed_by="test",
    )

    registry = _make_registry()
    runner = JobRunner(db, registry)

    job_id = enqueue(
        db,
        queue="ai",
        kind="ai.reenact",
        payload={
            "timeline_id": tl["id"],
            "seq_in_frame": 5,
            "seq_out_frame_exclusive": 20,
            "portrait_asset_id": portrait["id"],
            "consent_id": consent["id"],
            "backend": "stub",
        },
        max_attempts=1,
    )
    _drain(runner)

    # Job must have succeeded
    with db.connection() as conn:
        row = conn.execute("SELECT status, result_json FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row is not None
    assert row["status"] == "succeeded", f"Expected succeeded, got {row['status']}"

    # A new synthetic asset must exist
    all_assets = repos.list_assets(db, project["id"])
    synthetic = [a for a in all_assets if a.get("synthetic") and a.get("ai_effect") == "reenact"]
    assert len(synthetic) == 1, f"Expected 1 synthetic reenact asset, got {len(synthetic)}"

    synth_asset = synthetic[0]
    assert synth_asset["ai_effect"] == "reenact"

    # The output file must exist on disk
    out_path = Path(synth_asset["source_path"])
    assert out_path.exists(), f"Synthetic asset file not found: {out_path}"
    assert out_path.stat().st_size > 0

    # A replace-overlay clip must cover seq[5, 20)
    clips = repos.list_timeline_clips(db, tl["id"])
    replace_clips = [c for c in clips if c.get("role") == "replace"]
    assert len(replace_clips) == 1, f"Expected 1 replace clip, got {len(replace_clips)}"

    rc = replace_clips[0]
    assert rc["seq_in_frame"] == 5
    assert rc["seq_out_frame_exclusive"] == 20
    assert rc["asset_id"] == synth_asset["id"]
    assert rc["lane"] >= 1

    duration = rc["src_out_frame_exclusive"] - rc["src_in_frame"]
    assert duration == 15, f"Replace clip src duration should be 15 frames, got {duration}"

    # resolve_clip_rows must now yield the synthetic asset over seq[5, 20)
    tl_row = repos.get_timeline(db, tl["id"])
    assert tl_row is not None
    effective = resolve_clip_rows(db, tl_row)

    # Find the segment at seq[5, 20) in the effective rows
    reenact_rows = [
        c for c in effective
        if c["asset_id"] == synth_asset["id"]
    ]
    assert len(reenact_rows) >= 1, (
        "resolve_clip_rows did not yield the synthetic asset over the reenacted range"
    )
    # The synthetic row must cover exactly [5, 20)
    sr = reenact_rows[0]
    assert sr["seq_in_frame"] == 5
    assert sr["seq_out_frame_exclusive"] == 20


def test_reenact_stub_runs_sync_guard_before_registering_asset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    def fake_assert_sync(path: Path, **kwargs: Any) -> object:
        captured["path"] = path
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(ai_handlers, "assert_or_fix_media_sync", fake_assert_sync)

    db, project, base_asset, tl = _setup_scene(tmp_path)
    portrait = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="portrait.mp4",
        source_path=base_asset["source_path"],
    )
    consent = repos.create_consent_record(
        db,
        project_id=project["id"],
        subject_label="Test Subject",
        confirmed_by="test",
    )
    runner = JobRunner(db, _make_registry())

    enqueue(
        db,
        queue="ai",
        kind="ai.reenact",
        payload={
            "timeline_id": tl["id"],
            "seq_in_frame": 5,
            "seq_out_frame_exclusive": 20,
            "portrait_asset_id": portrait["id"],
            "consent_id": consent["id"],
            "backend": "stub",
        },
        max_attempts=1,
    )
    _drain(runner)

    synthetic = [
        asset
        for asset in repos.list_assets(db, project["id"])
        if asset.get("synthetic") and asset.get("ai_effect") == "reenact"
    ]
    assert len(synthetic) == 1
    assert captured["path"] == Path(synthetic[0]["source_path"])
    assert captured["kwargs"] == {
        "expected_frames": 15,
        "rate_num": 30,
        "rate_den": 1,
        "require_video": True,
        "fix": True,
    }


def test_reenact_sync_guard_failure_creates_no_synthetic_asset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_assert_sync(_path: Path, **_kwargs: Any) -> object:
        raise ValueError("video frame drift: expected 15, got 42")

    monkeypatch.setattr(ai_handlers, "assert_or_fix_media_sync", fake_assert_sync)

    db, project, base_asset, tl = _setup_scene(tmp_path)
    portrait = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="portrait.mp4",
        source_path=base_asset["source_path"],
    )
    consent = repos.create_consent_record(
        db,
        project_id=project["id"],
        subject_label="Test Subject",
        confirmed_by="test",
    )
    runner = JobRunner(db, _make_registry())

    job_id = enqueue(
        db,
        queue="ai",
        kind="ai.reenact",
        payload={
            "timeline_id": tl["id"],
            "seq_in_frame": 5,
            "seq_out_frame_exclusive": 20,
            "portrait_asset_id": portrait["id"],
            "consent_id": consent["id"],
            "backend": "stub",
        },
        max_attempts=1,
    )
    _drain(runner)

    with db.connection() as conn:
        row = conn.execute(
            "SELECT status, error_json FROM jobs WHERE id=?",
            (job_id,),
        ).fetchone()
    assert row is not None
    assert row["status"] == "failed"
    assert "video frame drift" in row["error_json"]
    synthetic = [
        asset
        for asset in repos.list_assets(db, project["id"])
        if asset.get("synthetic") and asset.get("ai_effect") == "reenact"
    ]
    assert synthetic == []


def test_reenact_runtime_id_routes_to_runtime_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from laura.ai.reenact_backend import StubReenactBackend

    db, project, base_asset, tl = _setup_scene(tmp_path)
    portrait = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="portrait.mp4",
        source_path=base_asset["source_path"],
    )
    consent = repos.create_consent_record(
        db,
        project_id=project["id"],
        subject_label="Test Subject",
        confirmed_by="test",
    )
    runtime = repos.create_ai_runtime(
        db,
        kind="container",
        effect="reenact",
        display_name="LivePortrait",
        container_image="laura-runtime-liveportrait:local",
        port=9911,
    )
    captured: list[tuple[str | None, str | None]] = []

    def capture_resolver(name: str | None, *, base_url: str | None = None) -> object:
        captured.append((name, base_url))
        return StubReenactBackend()

    monkeypatch.setattr(ai_handlers, "resolve_reenact_backend", capture_resolver)
    runner = JobRunner(db, _make_registry())

    enqueue(
        db,
        queue="ai",
        kind="ai.reenact",
        payload={
            "timeline_id": tl["id"],
            "seq_in_frame": 5,
            "seq_out_frame_exclusive": 20,
            "portrait_asset_id": portrait["id"],
            "consent_id": consent["id"],
            "runtime_id": runtime["id"],
        },
        max_attempts=1,
    )
    _drain(runner)

    assert captured == [("liveportrait", "http://127.0.0.1:9911")]
