"""Integration tests for overlay (replace-role) clip precedence in resolve_clip_rows.

Test 1 (render test, real ffmpeg required):
  - A rough_cut timeline with a GREEN base clip [0,45) and a RED overlay [15,30)
    is rendered via render_clips_mp4.  Color probes at three seq positions verify
    the overlay is applied correctly:
      * before overlay  (seq frame ~10)  → GREEN
      * inside overlay  (seq frame ~22)  → RED
      * after overlay   (seq frame ~38)  → GREEN

Test 2 (regression, no ffmpeg needed):
  - A rough_cut timeline with only base clips (no overlay) → resolve_clip_rows
    returns exactly the same rows as repos.list_timeline_clips.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.editing.otio_sync import resolve_clip_rows
from laura.ingest.ffmpeg import run_ffmpeg
from laura.render.mp4 import render_clips_mp4

_FFMPEG_BIN = os.environ.get("LAURA_FFMPEG", "ffmpeg")
_FFPROBE_BIN = os.environ.get("LAURA_FFPROBE", "ffprobe")

_HAS_FFMPEG = (
    shutil.which(_FFMPEG_BIN) is not None
    and shutil.which(_FFPROBE_BIN) is not None
)

pytestmark_ffmpeg = pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")


def _avg_rgb(path: Path, ss_seconds: float) -> tuple[int, int, int]:
    """Extract a single frame at *ss_seconds* and return its average colour as (R, G, B).

    Uses ffmpeg to scale the frame to 1x1 and emit 3 raw bytes via stdout.
    """
    result = subprocess.run(
        [
            _FFMPEG_BIN,
            "-ss", str(ss_seconds),
            "-i", str(path),
            "-frames:v", "1",
            "-vf", "scale=1:1",
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "-",
        ],
        capture_output=True,
        check=True,
    )
    raw = result.stdout
    # rawvideo output: exactly 3 bytes per pixel (R, G, B)
    assert len(raw) >= 3, f"Expected ≥3 bytes from ffmpeg rawvideo, got {len(raw)}"
    r, g, b = raw[0], raw[1], raw[2]
    return r, g, b


def _make_db(tmp_path: Path) -> tuple[SqliteDatabase, dict[str, Any]]:
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    proot = settings.workspace_root / "project"
    proot.mkdir(parents=True, exist_ok=True)
    project = repos.create_project(
        db,
        name="overlay_test",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(proot),
    )
    return db, project


@pytestmark_ffmpeg
def test_overlay_render_color_probe(tmp_path: Path) -> None:
    """Render a rough_cut with a RED replace-overlay over a GREEN base; probe colours."""
    # Build solid-colour source clips via lavfi
    green_src = tmp_path / "green.mp4"
    red_src = tmp_path / "red.mp4"

    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=green:s=320x240:r=30:d=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(green_src),
    ])
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=red:s=320x240:r=30:d=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(red_src),
    ])

    db, project = _make_db(tmp_path)

    green_asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="green.mp4",
        source_path=str(green_src),
    )
    red_asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="red.mp4",
        source_path=str(red_src),
    )

    # Rough-cut timeline (30 fps, project rate 30/1)
    # Base clip: green, src[0,45) → seq[0,45) (1.5 s)
    # Overlay clip: red, src[0,15) → seq[15,30), lane=1, role='replace'
    tl = repos.create_timeline(
        db, project_id=project["id"], name="ov_tl", kind="rough_cut"
    )
    repos.add_timeline_clip(
        db,
        timeline_id=tl["id"],
        asset_id=green_asset["id"],
        src_in_frame=0,
        src_out_frame_exclusive=45,
        seq_in_frame=0,
        seq_out_frame_exclusive=45,
        lane=0,
        role="base",
    )
    repos.add_timeline_clip(
        db,
        timeline_id=tl["id"],
        asset_id=red_asset["id"],
        src_in_frame=0,
        src_out_frame_exclusive=15,
        seq_in_frame=15,
        seq_out_frame_exclusive=30,
        lane=1,
        role="replace",
    )

    tl_row = repos.get_timeline(db, tl["id"])
    assert tl_row is not None
    rows = resolve_clip_rows(db, tl_row)

    # Map rows to (Path, src_in, src_out_exclusive) for render
    clips = [
        (
            Path(repos.get_asset(db, c["asset_id"])["source_path"]),  # type: ignore[index]
            c["src_in_frame"],
            c["src_out_frame_exclusive"],
        )
        for c in rows
    ]

    out = tmp_path / "overlay_out.mp4"
    render_clips_mp4(clips, out, rate_num=30, rate_den=1)

    assert out.exists() and out.stat().st_size > 0

    # Probe colours at three seq timestamps:
    #   seq frame 10 → t ≈ 0.333s  (before overlay window [15,30))  → GREEN
    #   seq frame 22 → t ≈ 0.733s  (inside overlay window)          → RED
    #   seq frame 38 → t ≈ 1.267s  (after overlay window)           → GREEN

    THRESHOLD = 100  # dominant channel must exceed this; other must be below it

    r10, g10, b10 = _avg_rgb(out, 10 / 30)
    assert g10 > THRESHOLD and r10 < THRESHOLD, (
        f"Frame ~10 expected GREEN, got R={r10} G={g10} B={b10}"
    )

    r22, g22, b22 = _avg_rgb(out, 22 / 30)
    assert r22 > THRESHOLD and g22 < THRESHOLD, (
        f"Frame ~22 expected RED, got R={r22} G={g22} B={b22}"
    )

    r38, g38, b38 = _avg_rgb(out, 38 / 30)
    assert g38 > THRESHOLD and r38 < THRESHOLD, (
        f"Frame ~38 expected GREEN, got R={r38} G={g38} B={b38}"
    )


def test_resolve_clip_rows_no_overlay_regression(tmp_path: Path) -> None:
    """resolve_clip_rows on a base-only timeline returns exactly list_timeline_clips."""
    db, project = _make_db(tmp_path)

    # We don't need a real source file for this unit test — path just needs to be a string.
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="dummy.mp4",
        source_path=str(tmp_path / "dummy.mp4"),
    )

    tl = repos.create_timeline(
        db, project_id=project["id"], name="base_only", kind="rough_cut"
    )
    repos.add_timeline_clip(
        db,
        timeline_id=tl["id"],
        asset_id=asset["id"],
        src_in_frame=0,
        src_out_frame_exclusive=30,
        seq_in_frame=0,
        seq_out_frame_exclusive=30,
        lane=0,
        role="base",
    )
    repos.add_timeline_clip(
        db,
        timeline_id=tl["id"],
        asset_id=asset["id"],
        src_in_frame=30,
        src_out_frame_exclusive=60,
        seq_in_frame=30,
        seq_out_frame_exclusive=60,
        lane=0,
        role="base",
    )

    tl_row = repos.get_timeline(db, tl["id"])
    assert tl_row is not None

    resolved = resolve_clip_rows(db, tl_row)
    expected = repos.list_timeline_clips(db, tl["id"])

    assert resolved == expected
