"""handle_render smart reel end: when a duration cap would cut mid-word, the rendered clip list is
snapped back to the latest transcript word boundary so the short finishes on a complete word.
Prefix-preserving, so captions/music stay aligned. Opt out with reel_exact_duration=true.

Monkeypatches render_clips_mp4 + the sync guard — no ffmpeg required (exercises the real
word->budget snap against a synthetic DB, since the live workspace may be empty)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.jobs import JobRunner, default_registry, enqueue
from laura.render import handlers as render_handlers


def _build_db(tmp_path: Path, *, clip_out: int) -> tuple[Any, Any, Any]:
    """project + asset + rough_cut timeline with a single clip [0, clip_out) at 30 fps."""
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    proot = settings.workspace_root / "project"
    proot.mkdir(parents=True, exist_ok=True)

    project = repos.create_project(
        db, name="t", rate_num=30, rate_den=1, drop_frame=False, workspace_root=str(proot)
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="a.mp4",
        source_path=str(tmp_path / "a.mp4"),
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="cut", kind="rough_cut")
    repos.add_timeline_clip(
        db,
        timeline_id=tl["id"],
        asset_id=asset["id"],
        src_in_frame=0,
        src_out_frame_exclusive=clip_out,
        seq_in_frame=0,
        seq_out_frame_exclusive=clip_out,
    )
    return db, project, tl


def _patch_render(monkeypatch: Any) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_render(clips: Any, dest: Path, **_kwargs: Any) -> None:
        captured["clips"] = clips
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"mp4")

    monkeypatch.setattr(render_handlers, "render_clips_mp4", fake_render)
    monkeypatch.setattr(render_handlers, "assert_or_fix_media_sync", lambda *a, **k: object())
    return captured


def _run(db: Any, exp: Any) -> None:
    registry = default_registry()
    render_handlers.register_render_handlers(registry)
    runner = JobRunner(db, registry)
    enqueue(
        db,
        queue="export",
        kind="export.render",
        payload={"export_id": exp["id"]},
        idempotency_key=f"render:{exp['id']}",
    )
    for _ in range(20):
        if not runner.run_once():
            break


def test_smart_end_snaps_cap_to_word_boundary(monkeypatch: Any, tmp_path: Path) -> None:
    captured = _patch_render(monkeypatch)
    # Words end at 30, 142, 160 (seq frames). A 5 s cap at 30 fps = 150 frames lands between 142 and
    # 160 -> the smart end snaps the cut back to 142 so the reel ends on a complete word.
    monkeypatch.setattr(
        render_handlers,
        "timeline_caption_words",
        lambda _db, _tl: [("a", 0, 30), ("b", 120, 142), ("c", 150, 160)],
    )
    db, project, tl = _build_db(tmp_path, clip_out=300)
    exp = repos.create_export(
        db,
        project_id=project["id"],
        timeline_id=tl["id"],
        format="mp4",
        options={"max_duration_seconds": 5},
    )
    _run(db, exp)

    assert captured["clips"] == [(Path(tmp_path / "a.mp4"), 0, 142)]
    export = repos.get_export(db, exp["id"])
    assert export is not None and export["status"] == "ready" and export["error"] is None


def test_reel_exact_duration_opts_out_of_the_snap(monkeypatch: Any, tmp_path: Path) -> None:
    captured = _patch_render(monkeypatch)
    words_called: list[bool] = []

    def fake_words(_db: Any, _tl: str) -> list[tuple[str, int, int]]:
        words_called.append(True)
        return [("a", 0, 30), ("b", 120, 142)]

    monkeypatch.setattr(render_handlers, "timeline_caption_words", fake_words)
    db, project, tl = _build_db(tmp_path, clip_out=300)
    exp = repos.create_export(
        db,
        project_id=project["id"],
        timeline_id=tl["id"],
        format="mp4",
        options={"max_duration_seconds": 5, "reel_exact_duration": True},
    )
    _run(db, exp)

    # Exact-duration: hard cut at 150 frames, and the transcript is never consulted.
    assert captured["clips"] == [(Path(tmp_path / "a.mp4"), 0, 150)]
    assert words_called == []


def test_no_transcript_falls_back_to_plain_tail_trim(monkeypatch: Any, tmp_path: Path) -> None:
    captured = _patch_render(monkeypatch)
    # No transcript words -> snap is a no-op -> identical to the existing hard tail-trim at 150.
    monkeypatch.setattr(render_handlers, "timeline_caption_words", lambda _db, _tl: [])
    db, project, tl = _build_db(tmp_path, clip_out=300)
    exp = repos.create_export(
        db,
        project_id=project["id"],
        timeline_id=tl["id"],
        format="mp4",
        options={"max_duration_seconds": 5},
    )
    _run(db, exp)

    assert captured["clips"] == [(Path(tmp_path / "a.mp4"), 0, 150)]
