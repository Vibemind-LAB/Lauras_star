"""match_lines_to_scenes / suggest_scenes_for_script (Task 9, Transkript-Gates).

Deterministic line -> scene matching: after a script (re-)approval the storyline must follow
the TEXT, not the other way round. Reuses ``discovery._segment_hits`` (lexical+semantic, with
the existing fallback) and ``discovery.search_material``'s segment->scene mapping approach —
this module does not invent a parallel one, it restricts the same mapping to one asset per
line and keeps only the best-scoring scene.

DB fixture mirrors ``tests/test_discovery.py``'s ``_seed_asset_with_scenes`` verbatim (the
established seed pattern for project + asset + transcript segments + rough-cut scenes).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.short_creator import discovery, script_match
from laura.short_creator.board import Board
from laura.short_creator.board_models import BoardMeta, Script, ScriptLine
from laura.short_creator.production_tools import build_production_tool_specs

FPS = 30


def _db(tmp_path: Path) -> Database:
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db: Database = SqliteDatabase(settings.db_path)
    db.migrate()
    return db


def _seed_asset_with_scenes(
    db: Database, project_id: str, name: str, *, segments: list[tuple[int, int, str]]
) -> str:
    """Asset + succeeded analysis run with *segments* (start_frame, end_frame, text) + a
    rough-cut timeline with one 1:1 clip over [0, 600) and two scenes [0,300)/[300,600).

    Verbatim copy of tests/test_discovery.py's seed helper — that file is the source of truth
    for how discovery tests seed project+asset+transcript+scenes.
    """
    asset = repos.create_asset(
        db, project_id=project_id, type="video", display_name=name, source_path=f"/tmp/{name}"
    )
    run = repos.create_analysis_run(
        db, asset_id=asset["id"], pipeline_version="t", config={}
    )
    repos.start_analysis_run(db, run["id"])
    for start, end, text in segments:
        repos.insert_segment_with_words(
            db,
            asset_id=asset["id"],
            run_id=run["id"],
            speaker_id=None,
            segment={
                "start_sample": start * 1600,
                "end_sample": end * 1600,
                "start_frame": start,
                "end_frame": end,
                "text": text,
                "confidence": 1.0,
            },
            words=[],
        )
    repos.finish_analysis_run(db, run["id"], status="succeeded", diagnostics={})
    timeline = repos.create_timeline(
        db, project_id=project_id, name="Rough Cut", kind="rough_cut",
        created_from=asset["id"],
    )
    repos.add_timeline_clip(
        db, timeline_id=timeline["id"], asset_id=asset["id"],
        src_in_frame=0, src_out_frame_exclusive=600,
        seq_in_frame=0, seq_out_frame_exclusive=600,
    )
    repos.replace_scenes(db, project_id, timeline["id"], [(0, 300), (300, 600)])
    return str(asset["id"])


def _project(db: Database) -> dict[str, Any]:
    return repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )


# --- match_lines_to_scenes: the pure matcher -------------------------------------------------


def test_matching_line_resolves_to_the_scene_carrying_that_text(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(discovery, "get_index", lambda: None)  # force lexical
    db = _db(tmp_path)
    project = _project(db)
    asset_id = _seed_asset_with_scenes(
        db, project["id"], "a.mp4",
        segments=[
            (10, 60, "Claude Code im Vault laeuft super"),
            (320, 380, "ganz andere Themen hier"),
        ],
    )

    out = script_match.match_lines_to_scenes(
        db, project["id"], asset_id, ["Claude Code im Vault"]
    )

    assert len(out) == 1
    assert out[0]["line_index"] == 0
    assert out[0]["scene_number"] == 1
    assert out[0]["score"] > 0
    assert out[0]["matched_text"] is not None
    assert "Claude Code" in out[0]["matched_text"]


def test_a_nonsense_line_has_no_scene(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(discovery, "get_index", lambda: None)  # force lexical
    db = _db(tmp_path)
    project = _project(db)
    asset_id = _seed_asset_with_scenes(
        db, project["id"], "a.mp4",
        segments=[
            (10, 60, "Claude Code im Vault laeuft super"),
            (320, 380, "ganz andere Themen hier"),
        ],
    )

    out = script_match.match_lines_to_scenes(
        db, project["id"], asset_id, ["quantum chromodynamics zephyr"]
    )

    assert len(out) == 1
    assert out[0]["line_index"] == 0
    assert out[0]["scene_number"] is None
    assert out[0]["score"] == 0.0
    assert out[0]["matched_text"] is None


def test_hits_from_a_different_asset_are_not_used(tmp_path: Path, monkeypatch: Any) -> None:
    """A project can hold several assets; discovery._segment_hits ranges over the whole
    project, so the matcher must filter hits down to the given asset before mapping — a hit
    from a sibling asset must never resolve a scene number here."""
    monkeypatch.setattr(discovery, "get_index", lambda: None)  # force lexical
    db = _db(tmp_path)
    project = _project(db)
    other_asset = _seed_asset_with_scenes(
        db, project["id"], "other.mp4",
        segments=[(10, 60, "Claude Code im Vault laeuft super")],
    )
    asset_id = _seed_asset_with_scenes(
        db, project["id"], "a.mp4",
        segments=[(10, 60, "voellig unrelated content")],
    )

    out = script_match.match_lines_to_scenes(
        db, project["id"], asset_id, ["Claude Code im Vault"]
    )

    assert out[0]["scene_number"] is None
    assert repos.get_asset(db, other_asset) is not None  # sanity: the sibling really exists


def test_multiple_lines_preserve_order_and_index(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(discovery, "get_index", lambda: None)  # force lexical
    db = _db(tmp_path)
    project = _project(db)
    asset_id = _seed_asset_with_scenes(
        db, project["id"], "a.mp4",
        segments=[
            (10, 60, "Claude Code im Vault laeuft super"),
            (320, 380, "ganz andere Themen hier"),
        ],
    )

    out = script_match.match_lines_to_scenes(
        db, project["id"], asset_id,
        ["Claude Code im Vault", "quatsch bla fasel", "ganz andere Themen"],
    )

    assert [row["line_index"] for row in out] == [0, 1, 2]
    assert out[0]["scene_number"] == 1
    assert out[1]["scene_number"] is None
    assert out[2]["scene_number"] == 2


# --- suggest_scenes_for_script: the board-bound tool ------------------------------------------


def _board(tmp_path: Path, asset_id: str) -> Board:
    meta = BoardMeta(
        session_id="s1",
        asset_id=asset_id,
        created_utc="2026-08-04T00:00:00Z",
        task="overview short",
        target_seconds=20.0,
    )
    return Board.create(tmp_path / "board", meta)


def test_suggest_scenes_for_script_without_a_script_reports_the_reason(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    db = _db(tmp_path)
    project = _project(db)
    asset_id = _seed_asset_with_scenes(
        db, project["id"], "a.mp4", segments=[(10, 60, "Claude Code im Vault laeuft super")]
    )
    board = _board(tmp_path, asset_id)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["suggest_scenes_for_script"].func()

    assert out == {"ok": False, "reason": "no script on the board; save_script_chapter first"}


def test_suggest_scenes_for_script_returns_a_suggestion_per_line(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    db = _db(tmp_path)
    project = _project(db)
    asset_id = _seed_asset_with_scenes(
        db, project["id"], "a.mp4",
        segments=[
            (10, 60, "Claude Code im Vault laeuft super"),
            (320, 380, "ganz andere Themen hier"),
        ],
    )
    board = _board(tmp_path, asset_id)
    board.save(
        "script",
        Script(
            language="de",
            lines=[
                ScriptLine(chapter=1, scene_number=1, text="Claude Code im Vault"),
                ScriptLine(chapter=1, scene_number=1, text="quatsch bla fasel"),
            ],
        ),
    )
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["suggest_scenes_for_script"].func()

    assert out["ok"] is True
    suggestions = out["suggestions"]
    assert [row["line_index"] for row in suggestions] == [0, 1]
    assert suggestions[0]["scene_number"] == 1
    assert suggestions[1]["scene_number"] is None
