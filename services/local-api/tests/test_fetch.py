"""End-to-end: ingest.fetch over a flaky server -> resume -> verify -> probe.

Uses REAL ffmpeg/ffprobe. Skipped if ffmpeg is unavailable.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.ingest.ffmpeg import run_ffmpeg
from laura.ingest.handlers import register_ingest_handlers
from laura.jobs import JobRunner, default_registry, enqueue

from ._flaky_http import serve

pytestmark = pytest.mark.skipif(
    shutil.which(os.environ.get("LAURA_FFMPEG", "ffmpeg")) is None,
    reason="ffmpeg not available on PATH",
)


def _drain(runner: JobRunner, limit: int = 60) -> int:
    ran = 0
    while runner.run_once():
        ran += 1
        if ran >= limit:
            break
    return ran


def test_fetch_resumes_then_probes(tmp_path: Path) -> None:
    media = tmp_path / "sample.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(media),
    ])
    content = media.read_bytes()

    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    project_root = settings.workspace_root / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    project = repos.create_project(
        db, name="t", rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(project_root),
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video",
        display_name="sample.mp4", source_path="url:pending", online=False,
    )

    registry = default_registry()
    register_ingest_handlers(registry)
    runner = JobRunner(db, registry)

    with serve(content, cut_after=len(content) // 2) as url:
        enqueue(
            db, queue="ingest.io", kind="ingest.fetch",
            payload={"asset_id": asset["id"], "source_url": url},
            idempotency_key=f"fetch:{asset['id']}", max_attempts=5,
        )
        _drain(runner)

    a = repos.get_asset(db, asset["id"])
    assert a is not None
    assert a["online"] == 1                      # fetch promoted it
    assert Path(a["source_path"]).exists()       # local file now
    assert (a["width"], a["height"]) == (320, 240)  # probe ran afterwards


def test_fetch_corrupt_media_marks_offline(tmp_path: Path) -> None:
    # Bytes that download fine but are NOT valid media -> the container check in
    # verify_decode fails, so the asset must never go online and an integrity
    # record must explain why.
    junk = b"not a real video file" * 5000

    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    project_root = settings.workspace_root / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    project = repos.create_project(
        db, name="t", rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(project_root),
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video",
        display_name="junk.mp4", source_path="url:pending", online=False,
    )

    registry = default_registry()
    register_ingest_handlers(registry)
    runner = JobRunner(db, registry)

    with serve(junk) as url:
        enqueue(
            db, queue="ingest.io", kind="ingest.fetch",
            payload={"asset_id": asset["id"], "source_url": url},
            idempotency_key=f"fetch:{asset['id']}", max_attempts=2,
        )
        _drain(runner)

    a = repos.get_asset(db, asset["id"])
    assert a is not None
    assert a["online"] == 0  # never promoted
    kinds = [f["kind"] for f in repos.list_asset_files(db, asset["id"])]
    assert "integrity" in kinds  # failure was recorded


def test_fetch_torrent_fans_out_one_asset_per_media_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    for name in ("a.mp4", "b.mp4"):
        run_ffmpeg([
            "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=30",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src / name),
        ])
    (src / "readme.nfo").write_text("not media", encoding="utf-8")

    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    project_root = settings.workspace_root / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    project = repos.create_project(
        db, name="t", rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(project_root),
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video",
        display_name="film.torrent", source_path="url:pending", online=False,
    )

    import laura.ingest.handlers as h
    monkeypatch.setattr(h, "aria2_available", lambda: True)
    monkeypatch.setattr(
        h, "aria2_download",
        lambda url, dest_dir, **kw: [src / "a.mp4", src / "b.mp4", src / "readme.nfo"],
    )

    registry = default_registry()
    register_ingest_handlers(registry)
    runner = JobRunner(db, registry)
    enqueue(
        db, queue="ingest.io", kind="ingest.fetch",
        payload={"asset_id": asset["id"], "source_url": "magnet:?xt=urn:btih:deadbeef"},
        idempotency_key=f"fetch:{asset['id']}", max_attempts=2,
    )
    _drain(runner)

    assets = repos.list_assets(db, project["id"], limit=50, offset=0)
    online = [a for a in assets if a["online"] == 1]
    assert len(online) == 2
    assert all((a["width"], a["height"]) == (160, 120) for a in online)
