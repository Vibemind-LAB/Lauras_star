"""production_tools: script voice synthesis (hash cache) + deterministic cutlist builder
(Slice 3, Task 5).

DB fixture mirrors ``tests/test_production_tools_review.py``'s ``_seed_scene`` but with TWO
scenes (each ``SCENE_FRAMES`` long) so the cutlist tests have more than one segment to order.
No analysis run / transcript is seeded — ``build_cutlist`` only needs each scene's SOURCE frame
range (via ``_resolve_scene`` -> ``context._scene_src_ranges``), which degrades to an empty
transcript text without a run; that text is never read by these tools.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.short_creator.board import Board
from laura.short_creator.board_models import (
    BestWindow,
    BoardMeta,
    Chapter,
    Cutlist,
    Roi,
    SceneReview,
    Script,
    ScriptLine,
    Storyline,
    VoiceArtifact,
)
from laura.short_creator.production_tools import (
    ProductionDeps,
    build_production_tool_specs,
    line_starts,
    script_hash,
    script_text,
)

FPS = 30
SCENE_FRAMES = 300  # 300 frames @ 30fps = 10.0s per scene
N_SCENES = 2


def _seed_two_scenes(tmp_path: Path) -> tuple[Database, str]:
    """Project + asset + a TWO-scene rough cut. One lane-0 clip spans both scenes 1:1
    (SOURCE == SEQUENCE, speed 1): scene 1 is source frames [0, SCENE_FRAMES), scene 2 is
    [SCENE_FRAMES, 2*SCENE_FRAMES)."""
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db: Database = SqliteDatabase(settings.db_path)
    db.migrate()
    project = repos.create_project(
        db,
        name="p",
        rate_num=FPS,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(tmp_path / "ws" / "proj"),
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="a.mp4",
        source_path=str(tmp_path / "a.mp4"),
    )
    timeline = repos.create_timeline(
        db,
        project_id=project["id"],
        name="Rough Cut",
        kind="rough_cut",
        created_from=asset["id"],
    )
    repos.add_timeline_clip(
        db,
        timeline_id=timeline["id"],
        asset_id=asset["id"],
        src_in_frame=0,
        src_out_frame_exclusive=SCENE_FRAMES * N_SCENES,
        seq_in_frame=0,
        seq_out_frame_exclusive=SCENE_FRAMES * N_SCENES,
        lane=0,
        role="base",
    )
    repos.replace_scenes(
        db,
        project["id"],
        timeline["id"],
        [(i * SCENE_FRAMES, (i + 1) * SCENE_FRAMES) for i in range(N_SCENES)],
    )
    return db, str(asset["id"])


def _board(tmp_path: Path, asset_id: str) -> Board:
    meta = BoardMeta(
        session_id="s1",
        asset_id=asset_id,
        created_utc="2026-07-13T00:00:00Z",
        task="overview short",
        target_seconds=20.0,
    )
    return Board.create(tmp_path / "board", meta)


def _review(
    board: Board,
    scene_number: int,
    *,
    best_window: BestWindow | None = None,
    roi: Roi | None = None,
) -> None:
    """Write a minimal valid SceneReview straight to the board (build_cutlist reads
    best_window/roi from these, not from the tool's own re-derivation)."""
    board.save_scene_review(
        SceneReview(
            scene_number=scene_number,
            src_start_frame=(scene_number - 1) * SCENE_FRAMES,
            src_end_frame_exclusive=scene_number * SCENE_FRAMES,
            description="d",
            whats_happening="h",
            hook_score=5,
            best_window=best_window
            if best_window is not None
            else BestWindow(offset_s=0.0, duration_s=2.0),
            roi=roi,
        )
    )


def _storyline(*, scene_numbers: list[int] | None = None, target_seconds: float = 4.0) -> Storyline:
    return Storyline(
        red_thread="stop scrolling",
        arc=[
            Chapter(
                chapter=1,
                role="hook",
                message="stop scrolling",
                scene_numbers=scene_numbers if scene_numbers is not None else [1, 2],
                target_seconds=target_seconds,
            )
        ],
    )


def _script() -> Script:
    return Script(
        language="de",
        lines=[
            ScriptLine(chapter=1, scene_number=1, text="Stopp dein Team"),
            ScriptLine(chapter=1, scene_number=2, text="Ein Klick genügt"),
        ],
    )


class _FakeVoiceBackend:
    """Fake VoiceBackend: writes a dummy mp3 + a timings sidecar with caller-supplied word
    times (so the cutlist zoom test can hand-compute expected values), and counts calls."""

    def __init__(
        self, *, words: list[dict[str, Any]] | None = None, ok: bool = True, reason: str = "boom"
    ) -> None:
        self.calls = 0
        self._words = words if words is not None else []
        self._ok = ok
        self._reason = reason

    def synthesize(self, text: str, out_path: Path) -> dict[str, Any]:
        self.calls += 1
        if not self._ok:
            return {"ok": False, "reason": self._reason}
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"id3-fake-mp3")
        timings_path = Path(str(out_path) + ".timings.json")
        timings_path.write_text(json.dumps({"words": self._words}), encoding="utf-8")
        return {"ok": True, "path": str(out_path), "timings_path": str(timings_path)}


def _save_voice(board: Board, tmp_path: Path, words: list[dict[str, Any]]) -> None:
    """Seed the board's voice artifact directly with a real sidecar file on disk (as
    build_cutlist reads it from ``timings_path``, not from the fake backend)."""
    timings_path = tmp_path / "voice.mp3.timings.json"
    timings_path.write_text(json.dumps({"words": words}), encoding="utf-8")
    board.save(
        "voice",
        VoiceArtifact(
            script_hash="irrelevant-for-cutlist",
            mp3_path=str(tmp_path / "voice.mp3"),
            timings_path=str(timings_path),
        ),
    )


def test_script_text_and_hash_stable() -> None:
    script = Script(
        language="de",
        lines=[
            ScriptLine(chapter=1, scene_number=1, text="Stopp dein Team"),
            ScriptLine(chapter=1, scene_number=2, text="Ein Klick genügt"),
            ScriptLine(chapter=2, scene_number=3, text="Und fertig"),
        ],
    )

    text = script_text(script)

    assert text == "Stopp dein Team Ein Klick genügt Und fertig"

    h1 = script_hash(script)
    h2 = script_hash(script.model_copy(deep=True))
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex digest

    changed = script.model_copy(
        update={"lines": [*script.lines, ScriptLine(chapter=2, scene_number=4, text="!")]}
    )
    assert script_hash(changed) != h1


def test_line_starts_maps_tokens_to_word_times() -> None:
    script = Script(
        language="de",
        lines=[
            ScriptLine(chapter=1, scene_number=1, text="Stopp dein Team"),
            ScriptLine(chapter=1, scene_number=2, text="Ein Klick"),
        ],
    )
    words = [
        {"text": "Stopp", "start_s": 0.0, "end_s": 0.3},
        {"text": "dein", "start_s": 0.4, "end_s": 0.7},
        {"text": "Team", "start_s": 0.8, "end_s": 1.3},
        {"text": "Ein", "start_s": 1.5, "end_s": 1.7},
        {"text": "Klick", "start_s": 1.9, "end_s": 2.3},
    ]

    starts = line_starts(script, words)

    assert starts == {(1, 1): 0.0, (1, 2): 1.5}


def test_synthesize_uses_cache_on_same_hash(tmp_path: Path) -> None:
    db, asset_id = _seed_two_scenes(tmp_path)
    board = _board(tmp_path, asset_id)
    board.save("script", _script())
    backend = _FakeVoiceBackend(
        words=[
            {"text": "Stopp", "start_s": 0.0, "end_s": 0.3},
            {"text": "dein", "start_s": 0.3, "end_s": 0.6},
            {"text": "Team", "start_s": 0.6, "end_s": 1.0},
            {"text": "Ein", "start_s": 1.2, "end_s": 1.4},
            {"text": "Klick", "start_s": 1.4, "end_s": 1.7},
            {"text": "genügt", "start_s": 1.7, "end_s": 2.1},
        ]
    )
    deps = ProductionDeps(voice_backend=backend)
    specs = {
        s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id, deps=deps)
    }

    first = specs["synthesize_script_voice"].func()
    assert first["ok"] is True
    assert first.get("cached") is False
    assert first["voice_s"] == pytest.approx(2.1)
    assert backend.calls == 1

    second = specs["synthesize_script_voice"].func()
    assert second["ok"] is True
    assert second["cached"] is True
    assert backend.calls == 1  # no second synthesis — same script hash

    saved = board.load("voice")
    assert isinstance(saved, VoiceArtifact)
    assert saved.script_hash == script_hash(_script())


def test_synthesize_reports_backend_failure(tmp_path: Path) -> None:
    db, asset_id = _seed_two_scenes(tmp_path)
    board = _board(tmp_path, asset_id)
    board.save("script", _script())
    backend = _FakeVoiceBackend(ok=False, reason="quota exceeded")
    deps = ProductionDeps(voice_backend=backend)
    specs = {
        s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id, deps=deps)
    }

    out = specs["synthesize_script_voice"].func()

    assert out == {"ok": False, "reason": "quota exceeded"}
    assert board.load("voice") is None


def test_build_cutlist_requires_prereqs(tmp_path: Path) -> None:
    db, asset_id = _seed_two_scenes(tmp_path)
    board = _board(tmp_path, asset_id)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["build_cutlist"].func()
    assert out["ok"] is False
    assert "storyline" in out["reason"]

    board.save("storyline", _storyline())
    out = specs["build_cutlist"].func()
    assert out["ok"] is False
    assert "script" in out["reason"]

    board.save("script", _script())
    out = specs["build_cutlist"].func()
    assert out["ok"] is False
    assert "synthesize" in out["reason"]


def test_build_cutlist_deterministic_segments_and_zoom(tmp_path: Path) -> None:
    db, asset_id = _seed_two_scenes(tmp_path)
    board = _board(tmp_path, asset_id)
    _review(
        board,
        1,
        best_window=BestWindow(offset_s=1.0, duration_s=3.0),
        roi=Roi(x=0.1, y=0.1, w=0.2, h=0.2),
    )
    _review(
        board,
        2,
        best_window=BestWindow(offset_s=0.0, duration_s=3.0),
        roi=Roi(x=0.3, y=0.3, w=0.2, h=0.2),
    )
    board.save("storyline", _storyline(scene_numbers=[1, 2], target_seconds=4.0))
    board.save("script", _script())
    # 6 words for the script's 6 whitespace tokens ("Stopp dein Team" + "Ein Klick genügt");
    # first word of line 1 starts at 0.2s, first word of line 2 starts at 2.5s.
    _save_voice(
        board,
        tmp_path,
        words=[
            {"text": "Stopp", "start_s": 0.2, "end_s": 0.45},
            {"text": "dein", "start_s": 0.5, "end_s": 0.75},
            {"text": "Team", "start_s": 0.9, "end_s": 1.2},
            {"text": "Ein", "start_s": 2.5, "end_s": 2.75},
            {"text": "Klick", "start_s": 2.8, "end_s": 3.0},
            {"text": "genügt", "start_s": 3.1, "end_s": 3.4},
        ],
    )
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["build_cutlist"].func()

    # target_seconds(4.0)/2 scenes = 2.0s budget/scene; both best_windows are 3.0s (>2.0s) so
    # the upper bound is 3.0s -> seg_dur clamps to the 2.0s floor -> 60 frames @ 30fps each.
    assert out == {"ok": True, "segments": 2, "total_seconds": 4.0, "with_zoom": 2}

    cutlist = board.load("cutlist")
    assert isinstance(cutlist, Cutlist)
    seg0, seg1 = cutlist.segments

    assert (seg0.order, seg0.scene_number) == (0, 1)
    # start = src_start(0) + offset_s(1.0)*30fps = 30; end = 30 + 60 frames = 90.
    assert (seg0.start_frame, seg0.end_frame_exclusive) == (30, 90)
    # video_start for the FIRST segment is 0.0 -> zoom = max(0, 0.2 - 0.0 + 0.4) = 0.6.
    assert seg0.zoom_start_s == pytest.approx(0.6)

    assert (seg1.order, seg1.scene_number) == (1, 2)
    # start = src_start(300) + offset_s(0.0)*30fps = 300; end = 300 + 60 frames = 360.
    assert (seg1.start_frame, seg1.end_frame_exclusive) == (300, 360)
    seg1_dauer = (seg0.end_frame_exclusive - seg0.start_frame) / FPS  # segment 1's own duration
    assert seg1_dauer == pytest.approx(2.0)
    # zoom_start_s of segment 2 = line_start - seg1_dauer + transition_lead_s (hand-computed).
    assert seg1.zoom_start_s == pytest.approx(2.5 - seg1_dauer + 0.4)
    assert seg1.zoom_start_s == pytest.approx(0.9)


def test_build_cutlist_clamps_inside_scene(tmp_path: Path) -> None:
    db, asset_id = _seed_two_scenes(tmp_path)
    board = _board(tmp_path, asset_id)
    # best_window sits right at the scene's end (10.0s scene, offset 9.0s + 2.0s segment would
    # run 1.0s past src_end without clamping).
    _review(
        board,
        1,
        best_window=BestWindow(offset_s=9.0, duration_s=3.0),
        roi=None,
    )
    board.save("storyline", _storyline(scene_numbers=[1], target_seconds=2.0))
    board.save("script", _script())
    _save_voice(board, tmp_path, words=[])
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["build_cutlist"].func()

    assert out["ok"] is True
    assert out["segments"] == 1
    cutlist = board.load("cutlist")
    assert isinstance(cutlist, Cutlist)
    seg = cutlist.segments[0]
    # raw start would be 0 + 9.0*30 = 270; 270 + 60 frames = 330 > src_end(300) -> clamped so
    # start_frame + dur_frames == src_end exactly.
    assert seg.start_frame == 240
    assert seg.end_frame_exclusive == 300
    assert seg.end_frame_exclusive <= SCENE_FRAMES
    assert seg.zoom_start_s is None  # roi=None -> no zoom regardless of timing
