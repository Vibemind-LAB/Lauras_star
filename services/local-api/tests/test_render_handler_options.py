"""Tests: handle_render forwards reel options (vertical, hook_text, disclosure_text)
to render_clips_mp4. Uses monkeypatching — no ffmpeg required.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.jobs import JobRunner, default_registry, enqueue
from laura.render import handlers as render_handlers


def _build_db(tmp_path: Path) -> tuple[Any, Any, Any, Any]:
    """Create a minimal project + asset + timeline + one clip; return (db, project, tl, asset)."""
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()

    proot = settings.workspace_root / "project"
    proot.mkdir(parents=True, exist_ok=True)

    project = repos.create_project(
        db, name="t", rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(proot),
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video",
        display_name="a.mp4", source_path=str(tmp_path / "a.mp4"),
    )
    tl = repos.create_timeline(
        db, project_id=project["id"], name="cut", kind="rough_cut",
    )
    repos.add_timeline_clip(
        db, timeline_id=tl["id"], asset_id=asset["id"],
        src_in_frame=0, src_out_frame_exclusive=30,
        seq_in_frame=0, seq_out_frame_exclusive=30,
    )
    return db, project, tl, asset


def _run_render_job(db: Any, project: Any, tl: Any, exp: Any) -> None:
    """Enqueue and drain a single export.render job."""
    registry = default_registry()
    render_handlers.register_render_handlers(registry)
    runner = JobRunner(db, registry)
    enqueue(
        db, queue="export", kind="export.render",
        payload={"export_id": exp["id"]},
        idempotency_key=f"render:{exp['id']}",
    )
    for _ in range(20):
        if not runner.run_once():
            break


def test_reel_options_forwarded_to_render(monkeypatch: Any, tmp_path: Path) -> None:
    """Options vertical/hook_text/disclosure_text reach render_clips_mp4."""
    captured: dict[str, Any] = {}

    def fake_render(*args: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(render_handlers, "render_clips_mp4", fake_render)

    db, project, tl, _ = _build_db(tmp_path)
    exp = repos.create_export(
        db,
        project_id=project["id"],
        timeline_id=tl["id"],
        format="mp4",
        options={"vertical": True, "hook_text": "Hook", "disclosure_text": "KI"},
    )

    _run_render_job(db, project, tl, exp)

    assert captured.get("vertical") is True
    assert captured.get("hook_text") == "Hook"
    assert captured.get("disclosure_text") == "KI"


def test_no_options_uses_defaults(monkeypatch: Any, tmp_path: Path) -> None:
    """When export has no options, render_clips_mp4 gets the off defaults."""
    captured: dict[str, Any] = {}

    def fake_render(*args: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(render_handlers, "render_clips_mp4", fake_render)

    db, project, tl, _ = _build_db(tmp_path)
    exp = repos.create_export(
        db,
        project_id=project["id"],
        timeline_id=tl["id"],
        format="mp4",
        options=None,
    )

    _run_render_job(db, project, tl, exp)

    assert captured.get("vertical") is False
    assert captured.get("hook_text") is None
    assert captured.get("disclosure_text") is None


def test_captions_option_builds_ass_and_forwards(monkeypatch: Any, tmp_path: Path) -> None:
    """When options={'captions': True}, the handler calls timeline_caption_words,
    builds an ASS string and passes it to render_clips_mp4 as caption_ass."""
    captured: dict[str, Any] = {}

    def fake_render(*args: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    def fake_caption_words(_db: Any, _timeline_id: str) -> list[tuple[str, int, int]]:
        return [("Hallo", 0, 15), ("Welt", 15, 30)]

    monkeypatch.setattr(render_handlers, "render_clips_mp4", fake_render)
    monkeypatch.setattr(render_handlers, "timeline_caption_words", fake_caption_words)

    db, project, tl, _ = _build_db(tmp_path)
    exp = repos.create_export(
        db,
        project_id=project["id"],
        timeline_id=tl["id"],
        format="mp4",
        options={"captions": True},
    )

    _run_render_job(db, project, tl, exp)

    ass = captured.get("caption_ass")
    assert isinstance(ass, str) and len(ass) > 0
    assert "Dialogue:" in ass


def test_caption_direction_options_reach_ass_builder(monkeypatch: Any, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}
    captured_ass: dict[str, Any] = {}

    def fake_render(*args: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    def fake_caption_words(_db: Any, _timeline_id: str) -> list[tuple[str, int, int]]:
        return [("Hallo", 0, 15)]

    def fake_build_ass(*args: Any, **kwargs: Any) -> str:
        captured_ass["args"] = args
        captured_ass["kwargs"] = kwargs
        return "ASS"

    monkeypatch.setattr(render_handlers, "render_clips_mp4", fake_render)
    monkeypatch.setattr(render_handlers, "timeline_caption_words", fake_caption_words)
    monkeypatch.setattr(render_handlers, "build_ass", fake_build_ass)

    db, project, tl, _ = _build_db(tmp_path)
    exp = repos.create_export(
        db,
        project_id=project["id"],
        timeline_id=tl["id"],
        format="mp4",
        options={
            "captions": True,
            "caption_preset": "shorts",
            "caption_mode": "normal",
            "caption_position": "top",
            "caption_fontsize": 80,
            "caption_safe_margin": 160,
        },
    )

    _run_render_job(db, project, tl, exp)

    assert captured.get("caption_ass") == "ASS"
    assert captured_ass["kwargs"] == {
        "rate_num": project["sequence_rate_num"],
        "rate_den": project["sequence_rate_den"],
        "play_w": 1080,
        "play_h": 1920,
        "fontsize": 80,
        "margin_v": 160,
        "mode": "normal",
        "position": "top",
    }


def test_captions_false_passes_none(monkeypatch: Any, tmp_path: Path) -> None:
    """When captions is falsy, caption_ass must be None and caption_words is not consulted."""
    captured: dict[str, Any] = {}
    words_called: list[bool] = []

    def fake_render(*args: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    def fake_caption_words(_db: Any, _timeline_id: str) -> list[tuple[str, int, int]]:
        words_called.append(True)
        return [("Hallo", 0, 15)]

    monkeypatch.setattr(render_handlers, "render_clips_mp4", fake_render)
    monkeypatch.setattr(render_handlers, "timeline_caption_words", fake_caption_words)

    db, project, tl, _ = _build_db(tmp_path)
    exp = repos.create_export(
        db,
        project_id=project["id"],
        timeline_id=tl["id"],
        format="mp4",
        options={},
    )

    _run_render_job(db, project, tl, exp)

    assert captured.get("caption_ass") is None
    assert words_called == []  # stub must not have been called


def test_timeline_audio_clips_forwarded_to_render(monkeypatch: Any, tmp_path: Path) -> None:
    """Timeline audio-lane clips reach render_clips_mp4 as structured overlays."""
    captured: dict[str, Any] = {}

    def fake_render(*args: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(render_handlers, "render_clips_mp4", fake_render)

    db, project, tl, _ = _build_db(tmp_path)
    audio_asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="audio",
        display_name="vo.wav",
        source_path=str(tmp_path / "vo.wav"),
    )
    repos.add_timeline_audio_clip(
        db,
        timeline_id=tl["id"],
        asset_id=audio_asset["id"],
        seq_in_frame=10,
        seq_out_frame_exclusive=40,
        asset_in_frame=5,
        gain_percent=85,
        fade_in_frames=3,
        fade_out_frames=6,
        label="VO",
    )
    exp = repos.create_export(
        db,
        project_id=project["id"],
        timeline_id=tl["id"],
        format="mp4",
        options=None,
    )

    _run_render_job(db, project, tl, exp)

    overlays = captured.get("audio_overlays")
    assert isinstance(overlays, list)
    assert len(overlays) == 1
    overlay = overlays[0]
    assert overlay.path == tmp_path / "vo.wav"
    assert overlay.seq_in_frame == 10
    assert overlay.seq_out_frame_exclusive == 40
    assert overlay.asset_in_frame == 5
    assert overlay.gain_percent == 85
    assert overlay.fade_in_frames == 3
    assert overlay.fade_out_frames == 6


def test_sequence_transitions_forwarded_to_render(monkeypatch: Any, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def fake_render(*args: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(render_handlers, "render_clips_mp4", fake_render)

    db, project, _tl, asset = _build_db(tmp_path)
    scene_tl_1 = repos.create_timeline(db, project_id=project["id"], name="s1", kind="scene")
    repos.add_timeline_clip(
        db,
        timeline_id=scene_tl_1["id"],
        asset_id=asset["id"],
        src_in_frame=0,
        src_out_frame_exclusive=30,
        seq_in_frame=0,
        seq_out_frame_exclusive=30,
    )
    scene_tl_2 = repos.create_timeline(db, project_id=project["id"], name="s2", kind="scene")
    repos.add_timeline_clip(
        db,
        timeline_id=scene_tl_2["id"],
        asset_id=asset["id"],
        src_in_frame=30,
        src_out_frame_exclusive=60,
        seq_in_frame=0,
        seq_out_frame_exclusive=30,
    )
    rc = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.replace_scenes(db, project["id"], rc["id"], [(0, 30), (30, 60)])
    scene_1, scene_2 = repos.list_scenes(db, rc["id"])
    repos.set_scene_timeline(db, scene_1["id"], scene_tl_1["id"])
    repos.set_scene_timeline(db, scene_2["id"], scene_tl_2["id"])
    seq = repos.get_or_create_project_sequence(db, project["id"])
    repos.replace_sequence_items(db, seq["id"], [scene_1["id"], scene_2["id"]])
    item = repos.list_sequence_items(db, seq["id"])[0]
    repos.update_sequence_item_transition(
        db,
        seq["id"],
        item["id"],
        kind="dip_black",
        duration_frames=12,
    )
    exp = repos.create_export(
        db,
        project_id=project["id"],
        timeline_id=seq["id"],
        format="mp4",
        options=None,
    )

    _run_render_job(db, project, seq, exp)

    transitions = captured.get("video_transitions")
    assert isinstance(transitions, list)
    assert len(transitions) == 1
    transition = transitions[0]
    assert transition.kind == "dip_black"
    assert transition.boundary_frame == 30
    assert transition.duration_frames == 12


def test_export_render_runs_sync_guard_with_timeline_length(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    def fake_render(clips: Any, dest: Path, **_kwargs: Any) -> None:
        captured["clips"] = clips
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"mp4")

    def fake_assert_sync(path: Path, **kwargs: Any) -> object:
        captured["sync_path"] = path
        captured["sync_kwargs"] = kwargs
        return object()

    monkeypatch.setattr(render_handlers, "render_clips_mp4", fake_render)
    monkeypatch.setattr(render_handlers, "assert_or_fix_media_sync", fake_assert_sync)

    db, project, tl, _asset = _build_db(tmp_path)
    exp = repos.create_export(
        db,
        project_id=project["id"],
        timeline_id=tl["id"],
        format="mp4",
        options=None,
    )

    _run_render_job(db, project, tl, exp)

    assert captured["sync_path"] == Path(project["workspace_root"]) / "exports" / f"{exp['id']}.mp4"
    assert captured["sync_kwargs"] == {
        "expected_frames": 30,
        "rate_num": 30,
        "rate_den": 1,
        "require_video": True,
        "fix": True,
    }


def test_export_render_marks_export_failed_when_sync_guard_rejects(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    def fake_render(_clips: Any, dest: Path, **_kwargs: Any) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"mp4")

    def fake_assert_sync(_path: Path, **_kwargs: Any) -> object:
        raise ValueError("video frame drift: expected 30, got 42")

    monkeypatch.setattr(render_handlers, "render_clips_mp4", fake_render)
    monkeypatch.setattr(render_handlers, "assert_or_fix_media_sync", fake_assert_sync)

    db, project, tl, _asset = _build_db(tmp_path)
    exp = repos.create_export(
        db,
        project_id=project["id"],
        timeline_id=tl["id"],
        format="mp4",
        options=None,
    )

    _run_render_job(db, project, tl, exp)

    export = repos.get_export(db, exp["id"])
    assert export is not None
    assert export["status"] == "error"
    assert "video frame drift" in export["error"]
