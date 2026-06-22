"""End-to-end: captions=True flows through the ``export.render`` job to a real
vertical 1080x1920 MP4 via ffmpeg with burned-in ASS captions built from a
real transcript stored in the database.

This is the whole-stack caption proof, analogous to ``test_reel_e2e.py``:
it exercises ``create_export(options={"vertical": True, "captions": True})``
-> queue -> ``handle_render`` -> ``render_clips_mp4`` (with reel + ass filter)
-> real ffmpeg output. Skipped if ffmpeg/ffprobe are unavailable or if the
ffmpeg build lacks libass.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.ingest.ffmpeg import run_ffmpeg
from laura.ingest.handlers import register_ingest_handlers
from laura.jobs import JobRunner, default_registry, enqueue

# ---------------------------------------------------------------------------
# skip guards
# ---------------------------------------------------------------------------

_FFMPEG = os.environ.get("LAURA_FFMPEG", "ffmpeg")


def _have_ffmpeg() -> bool:
    return shutil.which(_FFMPEG) is not None and shutil.which("ffprobe") is not None


def _have_libass() -> bool:
    """Return True when the ffmpeg build includes the ``ass`` filter (libass)."""
    result = subprocess.run(
        [_FFMPEG, "-hide_banner", "-filters"],
        capture_output=True,
        text=True,
    )
    combined = result.stdout + result.stderr
    # Match " ass " with surrounding spaces to avoid matching "bassboost" etc.
    return " ass " in combined


pytestmark = pytest.mark.skipif(
    not _have_ffmpeg(),
    reason="ffmpeg/ffprobe not available on PATH",
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _drain(runner: JobRunner, limit: int = 60) -> int:
    ran = 0
    while runner.run_once():
        ran += 1
        if ran >= limit:
            break
    return ran


def _video_dims(path: str) -> tuple[int, int]:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0", path,
        ],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    width, height = (int(x) for x in out.split(",")[:2])
    return width, height


# ---------------------------------------------------------------------------
# test
# ---------------------------------------------------------------------------


def test_caption_e2e_produces_vertical_mp4_with_captions(tmp_path: Path) -> None:
    """export.render with captions=True burns ASS captions into a 1080x1920 MP4."""
    if not _have_libass():
        pytest.skip("ffmpeg without libass — cannot burn ASS captions")

    # --- 1. Build a ~1 s 320x240 fixture clip at 30 fps -----------------------
    media = tmp_path / "src.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(media),
    ])

    # --- 2. Database + project setup -----------------------------------------
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()

    proot = settings.workspace_root / "project"
    proot.mkdir(parents=True, exist_ok=True)

    project = repos.create_project(
        db, name="caption_e2e", rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(proot),
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video",
        display_name="src.mp4", source_path=str(media),
    )

    # --- 3. Insert a real transcript (analysis_run + segment + words) ---------
    # Mirrors the exact approach in test_captions_source.py / _seed_db.
    # Words span frames [0,30) so they fall inside the clip's source range.
    run = repos.create_analysis_run(
        db, asset_id=asset["id"], pipeline_version="e2e_test", config={},
    )
    repos.insert_segment_with_words(
        db,
        asset_id=asset["id"],
        run_id=run["id"],
        speaker_id=None,
        segment={
            "start_sample": 0,
            "end_sample": 48000,
            "start_frame": 0,
            "end_frame": 29,
            "text": "Hallo Welt jetzt",
            "confidence": 0.99,
        },
        words=[
            {
                "idx": 0,
                "start_sample": 0,
                "end_sample": 16000,
                "start_frame": 0,
                "end_frame": 10,
                "text": "Hallo",
                "confidence": 0.99,
                "is_punctuation": False,
            },
            {
                "idx": 1,
                "start_sample": 16000,
                "end_sample": 32000,
                "start_frame": 10,
                "end_frame": 20,
                "text": "Welt",
                "confidence": 0.99,
                "is_punctuation": False,
            },
            {
                "idx": 2,
                "start_sample": 32000,
                "end_sample": 48000,
                "start_frame": 20,
                "end_frame": 29,
                "text": "jetzt",
                "confidence": 0.99,
                "is_punctuation": False,
            },
        ],
    )

    # --- 4. Timeline with one clip covering src [0, 30) -> seq [0, 30) --------
    tl = repos.create_timeline(
        db, project_id=project["id"], name="cut", kind="rough_cut",
    )
    repos.add_timeline_clip(
        db, timeline_id=tl["id"], asset_id=asset["id"],
        src_in_frame=0, src_out_frame_exclusive=30,
        seq_in_frame=0, seq_out_frame_exclusive=30,
    )

    # --- 5. Create export with captions=True + vertical=True ------------------
    exp = repos.create_export(
        db, project_id=project["id"], timeline_id=tl["id"], format="mp4",
        options={"vertical": True, "captions": True},
    )

    # --- 6. Register handlers, enqueue, drain ---------------------------------
    registry = default_registry()
    register_ingest_handlers(registry)
    from laura.render.handlers import register_render_handlers
    register_render_handlers(registry)

    runner = JobRunner(db, registry)
    enqueue(
        db, queue="export", kind="export.render",
        payload={"export_id": exp["id"]},
        idempotency_key=f"render:{exp['id']}",
    )
    _drain(runner)

    # --- 7. Assertions --------------------------------------------------------
    done = repos.get_export(db, exp["id"])
    assert done is not None
    assert done["status"] == "ready", done

    out_path = done["path"]
    assert out_path is not None
    assert Path(out_path).exists(), f"output file missing: {out_path}"
    assert done["size_bytes"] > 0, "output file is empty"

    # Vertical 9:16 reel dimensions.
    assert _video_dims(out_path) == (1080, 1920), (
        f"expected 1080x1920, got {_video_dims(out_path)}"
    )

    # Options survived the create_export -> get_export round-trip.
    assert done["options"] == {"vertical": True, "captions": True}

    # Temp ASS files must not leak next to the output.
    out_dir = Path(out_path).parent
    leftover_ass = list(out_dir.glob("*.reel_*.ass"))
    assert leftover_ass == [], f"reel ASS files leaked: {leftover_ass}"
