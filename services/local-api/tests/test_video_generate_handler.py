"""Video generation handler (Axis 2, Slice 1) — register a generated clip as a project asset.

``handle_video_generate`` calls an injected ``VideoGenerateBackend`` to produce a file, then
registers it as a synthetic media asset (``ai_effect="generate_video"``). The real/stub ffmpeg
backend is never exercised here — a fake backend writes a dummy file. Timeline placement is
deliberately NOT done in v1 (a product decision); the asset joins the project's media pool.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.generate.handlers import handle_video_generate
from laura.jobs.runner import JobContext


class _FakeBackend:
    """Records calls and writes a dummy file — no ffmpeg, no model."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def generate(
        self, *, prompt: str, out_path: Path, duration_frames: int, fps_num: int, fps_den: int
    ) -> None:
        self.calls.append({"prompt": prompt, "duration_frames": duration_frames})
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"\x00")


def _mkdb(tmp_path: Path) -> SqliteDatabase:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    return db


def _ctx(db: SqliteDatabase, payload: dict[str, Any]) -> JobContext:
    return JobContext(job_id="j1", kind="generate.video", queue="q", payload=payload, db=db)


def test_generate_registers_synthetic_asset(tmp_path: Path) -> None:
    db = _mkdb(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root=str(tmp_path / "ws")
    )
    backend = _FakeBackend()

    result = handle_video_generate(
        _ctx(db, {"project_id": project["id"], "prompt": "a calm ocean", "duration_frames": 90}),
        backend=backend,
    )

    assert result["ok"] is True
    assert backend.calls == [{"prompt": "a calm ocean", "duration_frames": 90}]
    asset = repos.get_asset(db, result["asset_id"])
    assert asset is not None
    assert asset["type"] == "video"
    assert asset["synthetic"] == 1
    assert asset["ai_effect"] == "generate_video"
    assert Path(asset["source_path"]).exists()


def test_generate_unknown_project_is_error(tmp_path: Path) -> None:
    db = _mkdb(tmp_path)
    result = handle_video_generate(
        _ctx(db, {"project_id": "nope", "prompt": "x", "duration_frames": 30}),
        backend=_FakeBackend(),
    )
    assert result["ok"] is False
    assert result["error"] == "project not found"
