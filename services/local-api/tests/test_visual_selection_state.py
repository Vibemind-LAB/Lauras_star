"""Source-media identity contracts for resumable visual selection."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.short_creator.visual_selection_state import (
    SourceMediaSnapshot,
    SourceMediaStaleError,
    capture_source_media_snapshot,
    validate_source_media_snapshot,
)


def _asset(tmp_path: Path, *, with_sha: bool = True) -> tuple[SqliteDatabase, str, Path]:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    project = repos.create_project(
        db,
        name="source identity",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(tmp_path / "ws" / "project"),
    )
    source = tmp_path / "drive-source.mp4"
    source.write_bytes(b"ABCD")
    asset = repos.create_asset(
        db,
        project_id=str(project["id"]),
        type="video",
        display_name=source.name,
        source_path=str(source),
    )
    if with_sha:
        repos.update_asset_probe(
            db,
            str(asset["id"]),
            type="video",
            duration_frames=120,
            rate_num=30,
            rate_den=1,
            audio_sample_rate=48_000,
            start_timecode=None,
            width=1920,
            height=1080,
            codec_video="h264",
            codec_audio="aac",
            is_vfr=False,
            sha256=hashlib.sha256(b"ABCD").hexdigest(),
        )
    return db, str(asset["id"]), source


def _capture(
    db: SqliteDatabase,
    asset_id: str,
    *,
    strong: bool,
    rough_cut_hash: str = "1" * 64,
    fps: float = 30.0,
    voice_hash: str = "2" * 64,
    voice_total_frames: int = 900,
    script_hash: str = "3" * 64,
    request_hash: str = "4" * 64,
) -> SourceMediaSnapshot:
    return capture_source_media_snapshot(
        db,
        asset_id=asset_id,
        rough_cut_hash=rough_cut_hash,
        fps=fps,
        voice_hash=voice_hash,
        voice_total_frames=voice_total_frames,
        script_hash=script_hash,
        request_hash=request_hash,
        strong=strong,
    )


def test_source_snapshot_detects_content_change_even_with_same_size_and_mtime(
    tmp_path: Path,
) -> None:
    """Catches a changed Drive file passing when metadata was preserved."""
    db, asset_id, source = _asset(tmp_path)
    original_stat = source.stat()
    expected = _capture(db, asset_id, strong=True)

    source.write_bytes(b"WXYZ")
    os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    quick_only = _capture(db, asset_id, strong=False)
    assert quick_only.quick_hash == expected.quick_hash
    assert quick_only.strong_hash is None
    with pytest.raises(SourceMediaStaleError) as stale:
        validate_source_media_snapshot(
            db,
            asset_id=asset_id,
            rough_cut_hash="1" * 64,
            fps=30.0,
            voice_hash="2" * 64,
            voice_total_frames=900,
            script_hash="3" * 64,
            request_hash="4" * 64,
            expected_quick_hash=expected.quick_hash,
            expected_strong_hash=expected.strong_hash,
            strong=True,
        )
    assert stale.value.reason == "source_content_changed"


def test_source_snapshot_rejects_missing_or_metadata_changed_source(tmp_path: Path) -> None:
    """Catches autosave accepting a missing or visibly replaced Drive file."""
    db, asset_id, source = _asset(tmp_path)
    expected = _capture(db, asset_id, strong=True)
    source.write_bytes(b"ABCDE")

    with pytest.raises(SourceMediaStaleError) as changed:
        validate_source_media_snapshot(
            db,
            asset_id=asset_id,
            rough_cut_hash="1" * 64,
            fps=30.0,
            voice_hash="2" * 64,
            voice_total_frames=900,
            script_hash="3" * 64,
            request_hash="4" * 64,
            expected_quick_hash=expected.quick_hash,
            expected_strong_hash=expected.strong_hash,
            strong=False,
        )
    assert changed.value.reason == "source_metadata_changed"

    source.unlink()
    with pytest.raises(SourceMediaStaleError) as missing:
        _capture(db, asset_id, strong=False)
    assert missing.value.reason == "source_missing"


def test_source_snapshot_binds_rough_cut_fps_voice_script_and_request(tmp_path: Path) -> None:
    """Catches timeline or narration drift retaining the old quick fingerprint."""
    db, asset_id, _ = _asset(tmp_path)
    baseline = _capture(db, asset_id, strong=False)
    variants = [
        _capture(db, asset_id, strong=False, rough_cut_hash="9" * 64),
        _capture(db, asset_id, strong=False, fps=29.97),
        _capture(db, asset_id, strong=False, voice_hash="8" * 64),
        _capture(db, asset_id, strong=False, voice_total_frames=899),
        _capture(db, asset_id, strong=False, script_hash="7" * 64),
        _capture(db, asset_id, strong=False, request_hash="6" * 64),
    ]

    for changed in variants:
        assert changed.quick_hash != baseline.quick_hash


def test_source_snapshot_sets_missing_ingest_sha_once(tmp_path: Path) -> None:
    """Catches legacy assets lacking a stable strong identity for later confirmation."""
    db, asset_id, _ = _asset(tmp_path, with_sha=False)

    snapshot = _capture(db, asset_id, strong=True)

    asset = repos.get_asset(db, asset_id)
    assert asset is not None
    assert asset["sha256"] == hashlib.sha256(b"ABCD").hexdigest()
    assert snapshot.strong_hash is not None
