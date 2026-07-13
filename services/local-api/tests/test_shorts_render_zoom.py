"""shorts.render handler: zoom option -> ZoomSpec list -> render_clips_mp4.

Fixture style mirrors ``test_shorts_render_handler.py`` (project + asset in a tmp SQLite DB,
a ``JobContext`` built by hand, ``render_clips_mp4`` monkeypatched so nothing touches real
ffmpeg). These tests exercise the raw-``segments`` render path (options carry ``asset_id`` +
``segments`` directly, no persisted candidates) since that is the path exposed to
``tool_render_segments``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.jobs.runner import JobContext
from laura.render import shorts_render


def _ctx(db: SqliteDatabase, export_id: str) -> JobContext:
    return JobContext(
        job_id="job-test",
        kind="shorts.render",
        queue="export",
        payload={"export_id": export_id},
        db=db,
    )


def _seed(tmp_path: Path, *, with_dims: bool = True) -> tuple[SqliteDatabase, str]:
    """Project (workspace_root, 30 fps) + one video asset, optionally probed to 1920x1080.

    ``source_path`` points at a dummy (never-decoded) file — ``render_clips_mp4`` is
    monkeypatched in every test in this module, so ffmpeg/ffprobe never touch it. Returns
    ``(db, asset_id)``.
    """
    workspace = tmp_path / "ws" / "project"
    workspace.mkdir(parents=True, exist_ok=True)
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()

    project = repos.create_project(
        db,
        name="p",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(workspace),
    )
    source = workspace / "a.mp4"
    source.write_bytes(b"DUMMY")
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="a.mp4",
        source_path=str(source),
    )
    if with_dims:
        repos.update_asset_probe(
            db,
            asset["id"],
            type="video",
            duration_frames=240,
            rate_num=30,
            rate_den=1,
            audio_sample_rate=48000,
            start_timecode=None,
            width=1920,
            height=1080,
            codec_video="h264",
            codec_audio="aac",
            is_vfr=False,
            sha256=None,
        )
    return db, asset["id"]


def _export(db: SqliteDatabase, asset_id: str, options: dict[str, Any]) -> str:
    asset = repos.get_asset(db, asset_id)
    assert asset is not None
    exp = repos.create_export(
        db,
        project_id=asset["project_id"],
        timeline_id=None,
        format="mp4",
        options=options,
    )
    return str(exp["id"])


def _patch_render_capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace render_clips_mp4 with a recorder that captures its kwargs and writes ``dest``."""
    captured: dict[str, Any] = {}

    def _fake(clips: list[Any], dest: Path, **kwargs: Any) -> None:
        captured.update(kwargs)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"FAKE-MP4")

    monkeypatch.setattr(shorts_render, "render_clips_mp4", _fake)
    return captured


def test_zoom_option_becomes_specs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A real ROI on segment 0 + None on segment 1 -> index-aligned ZoomSpec list."""
    db, asset_id = _seed(tmp_path)
    captured = _patch_render_capture(monkeypatch)
    export_id = _export(
        db,
        asset_id,
        {
            "segments": [[0, 120], [120, 240]],
            "asset_id": asset_id,
            "captions": False,
            "zoom": [
                {"roi": {"x": 0.6, "y": 0.1, "w": 0.25, "h": 0.25}, "zoom_start_s": 1.0},
                None,
            ],
        },
    )

    shorts_render.handle_shorts_render(_ctx(db, export_id))

    specs = captured["zoom_specs"]
    assert specs is not None and len(specs) == 2
    assert specs[0] is not None and specs[0].end_win[3] >= int(0.55 * 1080)
    assert specs[1] is None


def test_zoom_length_mismatch_sets_export_error(tmp_path: Path) -> None:
    """zoom shorter than segments -> ValueError, export marked error, renderer never reached."""
    db, asset_id = _seed(tmp_path)
    export_id = _export(
        db,
        asset_id,
        {
            "segments": [[0, 120], [120, 240]],
            "asset_id": asset_id,
            "captions": False,
            "zoom": [None],
        },
    )

    with pytest.raises(ValueError, match="zoom"):
        shorts_render.handle_shorts_render(_ctx(db, export_id))

    errored = repos.get_export(db, export_id)
    assert errored is not None
    assert errored["status"] == "error"


def test_all_none_zoom_collapses_to_no_zoom(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every segment's zoom hint is None -> zoom_specs collapses to plain None."""
    db, asset_id = _seed(tmp_path)
    captured = _patch_render_capture(monkeypatch)
    export_id = _export(
        db,
        asset_id,
        {
            "segments": [[0, 120]],
            "asset_id": asset_id,
            "captions": False,
            "zoom": [None],
        },
    )

    shorts_render.handle_shorts_render(_ctx(db, export_id))

    assert captured["zoom_specs"] is None


def test_missing_asset_dimensions_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asset never probed (width/height NULL) -> zoom_specs=None fallback, no error, even with
    a real ROI in the zoom option."""
    db, asset_id = _seed(tmp_path, with_dims=False)
    captured = _patch_render_capture(monkeypatch)
    export_id = _export(
        db,
        asset_id,
        {
            "segments": [[0, 120]],
            "asset_id": asset_id,
            "captions": False,
            "zoom": [{"roi": {"x": 0.6, "y": 0.1, "w": 0.25, "h": 0.25}, "zoom_start_s": 1.0}],
        },
    )

    shorts_render.handle_shorts_render(_ctx(db, export_id))

    assert captured["zoom_specs"] is None
