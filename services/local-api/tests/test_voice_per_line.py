"""Per-line synthesis: line cache, constructed track, merged sidecar, honest failure."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.short_creator.board import Board
from laura.short_creator.board_models import (
    BoardMeta,
    Chapter,
    Script,
    ScriptLine,
    Storyline,
    VoiceArtifact,
)
from laura.short_creator.production_tools import (
    ProductionDeps,
    build_production_tool_specs,
)
from laura.short_creator.voice import VoiceBackend
from laura.short_creator.voice_concat import INTER_SCENE_GAP_S

FPS = 30
SCENE_FRAMES = 300  # 300 frames @ 30fps = 10.0s per scene
N_SCENES = 2


def _tone(path: Path, seconds: float) -> None:
    """Same helper as test_voice_concat.py: a real ffmpeg-encoded sine tone so durations are
    genuinely probeable (not a fake byte blob)."""
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-q:a", "9", str(path)],
        check=True, capture_output=True,
    )


class CountingBackend:
    """Fake VoiceBackend: writes a real ffmpeg tone per call so durations are probeable.

    ``voice_id``/``model`` default to "" (matching the real backend's own attribute names) —
    every existing test in this file leaves them unset, so the per-line cache key (I2: text +
    voice identity) folds to the same "" for both, i.e. behaves exactly as before this fix.
    """

    def __init__(
        self,
        seconds_per_call: float = 0.6,
        fail_texts: set[str] | None = None,
        *,
        voice_id: str = "",
        model: str = "",
    ):
        self.calls: list[str] = []
        self.seconds = seconds_per_call
        self.fail_texts = fail_texts or set()
        self.voice_id = voice_id
        self.model = model

    def synthesize(self, text: str, out_path: Path) -> dict[str, Any]:
        self.calls.append(text)
        if text in self.fail_texts:
            return {"ok": False, "reason": "boom"}
        out_path.parent.mkdir(parents=True, exist_ok=True)
        _tone(out_path, self.seconds)  # same helper as test_voice_concat
        timings = Path(str(out_path) + ".timings.json")
        timings.write_text(json.dumps({"words": [
            {"text": text.split()[0], "start_s": 0.0, "end_s": 0.3}]}), encoding="utf-8")
        return {"ok": True, "path": str(out_path), "timings_path": str(timings)}


class RaisingBackend:
    """Fake VoiceBackend whose synthesize() RAISES instead of returning ok:False — exercises
    the per-line loop's own exception boundary (I1b), distinct from the ok:False retry path
    ``test_line_failure_is_named_and_retried_once`` already covers."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def synthesize(self, text: str, out_path: Path) -> dict[str, Any]:
        self.calls.append(text)
        raise ConnectionError("network exploded")


def _seed_two_scenes(tmp_path: Path) -> tuple[Database, str]:
    """Project + asset + a TWO-scene rough cut (mirrors test_production_tools_cutlist.py's
    ``_seed_two_scenes``, reused here so the storyline/script machinery behaves identically)."""
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


def _storyline() -> Storyline:
    return Storyline(
        red_thread="test",
        arc=[
            Chapter(
                chapter=1,
                role="hook",
                message="m",
                scene_numbers=[1],
                target_seconds=5.0,
            ),
            Chapter(
                chapter=2,
                role="payoff_cta",
                message="m2",
                scene_numbers=[2],
                target_seconds=5.0,
            ),
        ],
    )


def _script() -> Script:
    return Script(
        language="de",
        lines=[
            ScriptLine(chapter=1, scene_number=1, text="Stopp dein Team"),
            ScriptLine(chapter=2, scene_number=2, text="Ein Klick genügt"),
        ],
    )


def _setup(tmp_path: Path, backend: VoiceBackend) -> dict[str, Any]:
    db, asset_id = _seed_two_scenes(tmp_path)
    board = _board(tmp_path, asset_id)
    board.save("storyline", _storyline())
    board.save("script", _script())
    deps = ProductionDeps(voice_backend=backend)
    specs = {
        s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id, deps=deps)
    }
    return {"db": db, "board": board, "specs": specs}


def test_per_line_synthesis_builds_segments_and_track(tmp_path: Path) -> None:
    backend = CountingBackend()
    ctx = _setup(tmp_path, backend)
    tools, board = ctx["specs"], ctx["board"]

    result = tools["synthesize_script_voice"].func()

    assert result["ok"] is True and result["lines"] == 2
    voice = board.load("voice")
    assert isinstance(voice, VoiceArtifact)
    assert voice.segments is not None and len(voice.segments) == 2
    assert voice.segments[0].offset_s == pytest.approx(0.0)
    assert voice.segments[1].offset_s == pytest.approx(
        voice.segments[0].duration_s + INTER_SCENE_GAP_S, abs=0.05)
    assert Path(voice.mp3_path).exists()
    assert voice.timings_path is not None and Path(voice.timings_path).exists()


def test_line_cache_skips_unchanged_lines(tmp_path: Path) -> None:
    backend = CountingBackend()
    ctx = _setup(tmp_path, backend)
    tools = ctx["specs"]

    tools["synthesize_script_voice"].func()
    backend.calls.clear()
    # change ONE line's text, keep the other — resynthesis touches only the changed line. The
    # under-budget gate rejects a far-shorter chapter ONCE (per-run acknowledgement); saving
    # the same short text again is the deliberate-shorter-film path and is accepted.
    new_line = [{"scene_number": 2, "text": "neuer text"}]
    tools["save_script_chapter"].func(2, new_line)
    save_result = tools["save_script_chapter"].func(2, new_line)
    assert save_result["ok"] is True, save_result
    tools["synthesize_script_voice"].func()

    assert backend.calls == ["neuer text"]


def test_line_failure_is_named_and_retried_once(tmp_path: Path) -> None:
    backend = CountingBackend(fail_texts={"kaputt"})
    db, asset_id = _seed_two_scenes(tmp_path)
    board = _board(tmp_path, asset_id)
    board.save("storyline", _storyline())
    board.save(
        "script",
        Script(
            language="de",
            lines=[
                ScriptLine(chapter=1, scene_number=1, text="Stopp dein Team"),
                ScriptLine(chapter=2, scene_number=2, text="kaputt"),
            ],
        ),
    )
    deps = ProductionDeps(voice_backend=backend)
    specs = {
        s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id, deps=deps)
    }

    result = specs["synthesize_script_voice"].func()

    assert result["ok"] is False
    assert "scene 2" in result["reason"]
    assert "chapter 2" in result["reason"]
    assert backend.calls.count("kaputt") == 2  # exactly one retry
    assert board.load("voice") is None


def test_truncated_cache_is_evicted_and_resynthesized(tmp_path: Path) -> None:
    """I1a: a killed writer (or any partial write) can leave a truncated mp3 sitting AT a
    line's cache path. Without eviction that ONE corrupt file fails the line PERMANENTLY on
    every future run; synthesize_script_voice instead detects it (ffprobe raises RuntimeError),
    evicts it and its sidecar, and re-synthesizes the line once (its one retry)."""
    backend = CountingBackend()
    db, asset_id = _seed_two_scenes(tmp_path)
    board = _board(tmp_path, asset_id)
    board.save("storyline", _storyline())
    board.save("script", _script())
    deps = ProductionDeps(voice_backend=backend)
    specs = {
        s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id, deps=deps)
    }
    asset = repos.get_asset(db, asset_id)
    assert asset is not None
    project = repos.get_project(db, str(asset["project_id"]))
    assert project is not None
    lines_dir = Path(str(project["workspace_root"])) / "voiceovers" / "lines"
    lines_dir.mkdir(parents=True, exist_ok=True)
    # Same cache-key formula as synthesize_script_voice's per-line loop (text|voice_id|model);
    # CountingBackend's default voice_id/model are both "".
    garbage_hash = hashlib.sha256(b"Stopp dein Team||").hexdigest()
    garbage_clip = lines_dir / f"{garbage_hash}.mp3"
    garbage_clip.write_bytes(b"not a real mp3 at all")

    result = specs["synthesize_script_voice"].func()

    assert result["ok"] is True, result
    assert backend.calls.count("Stopp dein Team") == 1  # evicted once, resynthesized once
    assert garbage_clip.read_bytes() != b"not a real mp3 at all"  # real audio now on disk


def test_backend_exception_is_named_scene_and_chapter(tmp_path: Path) -> None:
    """I1b: an exception raised OUT of the backend (not just an ok:False reply) must still name
    its scene and chapter (spec §6's failure contract). Before this fix it bubbled straight to
    synthesize_script_voice's own OUTER exception boundary, which reports only a bare
    str(exc) — no scene, no chapter."""
    backend = RaisingBackend()
    ctx = _setup(tmp_path, backend)
    tools, board = ctx["specs"], ctx["board"]

    result = tools["synthesize_script_voice"].func()

    assert result["ok"] is False
    assert "scene 1" in result["reason"]
    assert "chapter 1" in result["reason"]
    assert board.load("voice") is None


def test_line_hash_includes_voice_identity(tmp_path: Path) -> None:
    """I2 (spec §5.1: hash over line text + voice settings): the SAME text synthesized under
    two DIFFERENT voice_ids must land at two DIFFERENT cache paths — otherwise switching
    ElevenLabs voices would silently keep serving the OLD voice's cached audio for text that
    never changed. Two boards share the SAME underlying project (-> same lines_dir)."""
    db, asset_id = _seed_two_scenes(tmp_path)

    def _build(board_dir: str, backend: CountingBackend) -> dict[str, Any]:
        board = Board.create(
            tmp_path / board_dir,
            BoardMeta(
                session_id=board_dir,
                asset_id=asset_id,
                created_utc="2026-08-06T00:00:00Z",
                task="overview short",
                target_seconds=20.0,
            ),
        )
        board.save("storyline", _storyline())
        board.save("script", _script())
        specs = {
            s.name: s
            for s in build_production_tool_specs(
                db, board, asset_id=asset_id, deps=ProductionDeps(voice_backend=backend)
            )
        }
        return {"board": board, "specs": specs}

    backend_a = CountingBackend(voice_id="voice-a")
    ctx_a = _build("board_a", backend_a)
    result_a = ctx_a["specs"]["synthesize_script_voice"].func()
    assert result_a["ok"] is True, result_a

    backend_b = CountingBackend(voice_id="voice-b")
    ctx_b = _build("board_b", backend_b)
    result_b = ctx_b["specs"]["synthesize_script_voice"].func()
    assert result_b["ok"] is True, result_b

    voice_a = ctx_a["board"].load("voice")
    voice_b = ctx_b["board"].load("voice")
    assert isinstance(voice_a, VoiceArtifact) and isinstance(voice_b, VoiceArtifact)
    assert voice_a.segments is not None and voice_b.segments is not None
    # Same text ("Stopp dein Team"), DIFFERENT voice_id -> different cache paths.
    assert voice_a.segments[0].mp3_path != voice_b.segments[0].mp3_path
    # backend_b actually did the work — it did not silently reuse backend_a's cached clip.
    assert "Stopp dein Team" in backend_b.calls
