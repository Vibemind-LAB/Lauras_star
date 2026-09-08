"""A finished render reaches object storage -- and a broken bucket costs nothing.

`tests/test_object_storage.py` pins the store in isolation. This drives the real
render job end to end, because the two claims that matter are about the SEAM, not
about the client:

* with storage configured, the export ends up carrying its bucket key;
* with storage failing, the render is still 'ready' with its file on disk.

The second is the reason `publish` swallows exceptions. An hour of rendering must
not be lost because a bucket was full.
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
from laura.ingest.handlers import register_ingest_handlers
from laura.jobs import JobRunner, default_registry, enqueue

pytestmark = pytest.mark.skipif(
    shutil.which(os.environ.get("LAURA_FFMPEG", "ffmpeg")) is None,
    reason="ffmpeg not available on PATH",
)


class _FakeStore:
    """Records what it was asked to upload. Three lines, as the Protocol intends."""

    bucket = "laura"

    def __init__(self) -> None:
        self.aufrufe: list[tuple[str, Path, str]] = []

    def put(self, key: str, path: Path, *, content_type: str) -> str:
        self.aufrufe.append((key, path, content_type))
        return f"{self.bucket}/{key}"


class _BrokenStore(_FakeStore):
    def put(self, key: str, path: Path, *, content_type: str) -> str:
        raise OSError("bucket unreachable")


def _drain(runner: JobRunner, limit: int = 60) -> int:
    ran = 0
    while runner.run_once():
        ran += 1
        if ran >= limit:
            break
    return ran


def _render(tmp_path: Path) -> tuple[SqliteDatabase, dict[str, Any]]:
    media = tmp_path / "a.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(media),
    ])
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    proot = settings.workspace_root / "project"
    proot.mkdir(parents=True, exist_ok=True)
    project = repos.create_project(
        db, name="t", rate_num=30, rate_den=1, drop_frame=False, workspace_root=str(proot)
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a.mp4",
        source_path=str(media),
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="cut", kind="rough_cut")
    repos.add_timeline_clip(
        db, timeline_id=tl["id"], asset_id=asset["id"],
        src_in_frame=0, src_out_frame_exclusive=30,
        seq_in_frame=0, seq_out_frame_exclusive=30,
    )
    export = repos.create_export(
        db, project_id=project["id"], timeline_id=tl["id"], format="mp4"
    )

    registry = default_registry()
    register_ingest_handlers(registry)
    from laura.render.handlers import register_render_handlers

    register_render_handlers(registry)
    runner = JobRunner(db, registry)
    enqueue(
        db, queue="export", kind="export.render",
        payload={"export_id": export["id"]},
        idempotency_key=f"render:{export['id']}",
    )
    _drain(runner)
    fertig = repos.get_export(db, export["id"])
    assert fertig is not None
    return db, fertig


def test_a_finished_render_is_published_and_recorded(
    tmp_path: Path, monkeypatch: Any
) -> None:
    store = _FakeStore()
    monkeypatch.setattr("laura.render.handlers.store_from_env", lambda: store)

    _db, export = _render(tmp_path)

    assert export["status"] == "ready"
    assert Path(export["path"]).exists(), "the local file is never replaced"
    assert export["object_key"] == f"laura/exports/{export['id']}.mp4"
    assert len(store.aufrufe) == 1
    key, pfad, content_type = store.aufrufe[0]
    assert key == f"exports/{export['id']}.mp4"
    assert pfad == Path(export["path"]), "the file on disk is what gets uploaded"
    assert content_type == "video/mp4"


def test_a_failing_upload_leaves_the_render_intact(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr("laura.render.handlers.store_from_env", lambda: _BrokenStore())

    _db, export = _render(tmp_path)

    assert export["status"] == "ready", "an unreachable bucket must not fail the render"
    assert Path(export["path"]).exists()
    assert export["size_bytes"] > 0
    assert export["object_key"] is None, "no key is recorded for an upload that failed"


def test_storage_off_touches_nothing(tmp_path: Path, monkeypatch: Any) -> None:
    """The default for a desktop install: no store, no key, no attempt."""
    monkeypatch.delenv("LAURA_STORAGE_URL", raising=False)
    monkeypatch.delenv("LAURA_STORAGE_KEY", raising=False)

    _db, export = _render(tmp_path)

    assert export["status"] == "ready"
    assert export["object_key"] is None
