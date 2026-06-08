"""Optional TransNetV2 shot detector: availability, lazy-import behaviour, and the
orchestrator's graceful fallback to PySceneDetect ``adaptive`` when it is absent.

The real-inference test is ``importorskip``-guarded and SKIPS unless the ``scene-ml`` extra
(``transnetv2-pytorch``) is installed — exactly like ``test_asr_real.py``. The fallback
tests run with the package ABSENT (the default env) and must stay green.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from laura.analysis.shots import scenedetect_available
from laura.analysis.transnet import transnetv2_available


def test_transnetv2_available_returns_bool_without_raising() -> None:
    result = transnetv2_available()
    # Robust across environments: False without the optional [scene-ml] extra, True with it.
    assert isinstance(result, bool)


def test_detect_shots_transnet_raises_importerror_when_absent() -> None:
    """Selecting ``transnet`` without the extra fails on the lazy import, before ffmpeg —
    so a non-existent path is fine; the ImportError must surface."""
    if transnetv2_available():
        pytest.skip("scene-ml extra installed; lazy import would not raise")
    from laura.analysis.shots import detect_shots

    with pytest.raises(ImportError):
        detect_shots(Path("does-not-exist.mp4"), detector="transnet")


@pytest.mark.skipif(
    not scenedetect_available()
    or shutil.which(os.environ.get("LAURA_FFMPEG", "ffmpeg")) is None
    or transnetv2_available(),
    reason="fallback test needs [scene] + ffmpeg and the scene-ml extra ABSENT",
)
def test_scene_stage_falls_back_to_adaptive_when_transnet_absent(tmp_path: Path) -> None:
    """End-to-end: request ``detector='transnet'`` with the package absent; the run must
    still succeed, produce shots via the adaptive fallback, and record the skip note."""
    from fastapi.testclient import TestClient

    from laura.analysis.handlers import register_analysis_handlers
    from laura.config import Settings
    from laura.db import repos
    from laura.db.database import SqliteDatabase
    from laura.ingest.ffmpeg import run_ffmpeg
    from laura.ingest.handlers import register_ingest_handlers
    from laura.jobs import JobRunner, default_registry, enqueue
    from laura.main import create_app

    clip = tmp_path / "cut.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=30",
        "-f", "lavfi", "-i", "color=c=blue:duration=1:size=320x240:rate=30",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip),
    ])

    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    app = create_app(settings)
    client = TestClient(app)
    client.__enter__()
    try:
        project = client.post(
            "/projects", json={"name": "p", "sequence_rate_num": 30, "sequence_rate_den": 1}
        ).json()
        asset = repos.create_asset(
            db, project_id=project["id"], type="video", display_name="cut.mp4",
            source_path=str(clip),
        )

        reg = default_registry()
        register_ingest_handlers(reg)
        register_analysis_handlers(reg)
        runner = JobRunner(db, reg)
        enqueue(db, queue="ingest.io", kind="ingest.probe",
                payload={"asset_id": asset["id"]}, idempotency_key=f"probe:{asset['id']}")
        while runner.run_once():
            pass

        resp = client.post(
            f"/assets/{asset['id']}/analysis",
            json={"scene": True, "asr": False, "detector": "transnet"},
        )
        assert resp.status_code == 202, resp.text
        run_id = resp.json()["analysis_run_id"]
        while runner.run_once():
            pass

        latest = client.get(f"/assets/{asset['id']}/analysis/latest").json()
        assert latest["status"] == "succeeded"
        scene = latest["diagnostics"]["scene"]
        # Fell back to the always-present adaptive detector...
        assert scene["status"] == "ok"
        assert scene["detector"] == "adaptive"
        # ...and recorded why TransNetV2 was skipped.
        assert "transnet" in scene
        assert scene["transnet"].startswith("skipped:")

        # The adaptive fallback actually produced shots (the hard cut at frame 30).
        shots = repos.list_shots(db, asset["id"], run_id)
        assert len(shots) >= 2
    finally:
        client.__exit__(None, None, None)


@pytest.mark.skipif(
    not scenedetect_available()
    or shutil.which(os.environ.get("LAURA_FFMPEG", "ffmpeg")) is None,
    reason="unit fallback test needs [scene] + ffmpeg for the adaptive path",
)
def test_run_scene_unit_fallback_records_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unit-level guard on the fallback branch in ``_run_scene`` itself: a config asking for
    ``transnet`` degrades to adaptive and records the skip note, independent of the HTTP
    layer. We force the transnet path to raise RuntimeError so the test is deterministic even
    if the ``scene-ml`` extra later gets installed (covers the inference-failed branch too)."""
    from pathlib import Path as _Path

    from laura.analysis import handlers
    from laura.analysis import shots as shots_mod
    from laura.analysis.handlers import _run_scene
    from laura.analysis.types import ShotResult
    from laura.config import Settings
    from laura.db import repos
    from laura.db.database import SqliteDatabase
    from laura.ingest.ffmpeg import run_ffmpeg

    clip = tmp_path / "cut.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=30",
        "-f", "lavfi", "-i", "color=c=blue:duration=1:size=320x240:rate=30",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip),
    ])

    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    project_root = settings.workspace_root / "p"
    project_root.mkdir(parents=True, exist_ok=True)
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(project_root),
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="cut.mp4",
        source_path=str(clip),
    )
    run = repos.create_analysis_run(
        db, asset_id=asset["id"], pipeline_version="1",
        config={"stages": {"scene": True}, "detector": "transnet"},
    )

    real_detect = shots_mod.detect_shots

    def fake_detect(
        video_path: _Path | str,
        *,
        detector: str = "adaptive",
        threshold: float = shots_mod.DEFAULT_THRESHOLD,
    ) -> list[ShotResult]:
        if detector == "transnet":
            raise RuntimeError("TransNetV2 inference unavailable: forced for test")
        return real_detect(video_path, detector=detector, threshold=threshold)

    # _run_scene resolves ``detect_shots`` from its own module namespace.
    monkeypatch.setattr(handlers, "detect_shots", fake_detect)

    asset_row = repos.get_asset(db, asset["id"])
    assert asset_row is not None
    result = _run_scene(db, asset_row, run["id"], {}, {"detector": "transnet"})

    assert result["status"] == "ok"
    assert result["detector"] == "adaptive"
    assert result["transnet"].startswith("skipped:")
    assert result["count"] >= 2
