"""Gate B — script-approval checkpoint (Task 7 of the Transkript-Gates arc).

Covers: ``BoardMeta``'s two new fields (old ``meta.json`` files load unchanged),
``Board.set_script_approved`` (atomic meta-write, same pattern as ``set_status``),
``synthesize_script_voice``'s deterministic refusal while the gate is active and unapproved,
``Board.status()``'s ``script_gate``/``script_lines`` payload, ``run_production`` seeding the
gate onto a FRESH board's meta, and the orchestrator prompt's script-approval paragraph.

Fixtures mirror the neighboring test files' own self-contained conventions: the DB/asset seed is
``tests/test_production_tools_write.py``'s ``_seed_scene``, the board helper is
``tests/test_production_tools_contact_sheet.py``'s ``_board`` (no ffmpeg needed here, so this
file carries no ffmpeg skip), and the ``run_production`` fixture is
``tests/test_production_orchestrator.py``'s ``_seed_scene`` + fake-``ExecuteFn`` pattern.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from laura.chat.executor import execute_decision
from laura.chat.router import RouterDecision
from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.short_creator import orchestrator, production_orchestrator, providers
from laura.short_creator.board import Board
from laura.short_creator.board_models import BoardMeta, Script, ScriptLine
from laura.short_creator.production_tools import build_production_tool_specs

FPS = 30
SCENE_FRAMES = 150  # 150 frames @ 30fps = 5.0s


def _seed_scene(tmp_path: Path) -> tuple[Database, str]:
    """Project + asset + succeeded analysis run w/ transcript + a ONE-scene rough cut.

    Returns ``(db, asset_id)``. Mirrors ``test_production_tools_write.py``'s ``_seed_scene``.
    """
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
    run = repos.create_analysis_run(db, asset_id=asset["id"], pipeline_version="t", config={})
    repos.start_analysis_run(db, run["id"])
    repos.insert_segment_with_words(
        db,
        asset_id=asset["id"],
        run_id=run["id"],
        speaker_id=None,
        segment={
            "start_sample": 0,
            "end_sample": 96_000,
            "start_frame": 0,
            "end_frame": SCENE_FRAMES,
            "text": "hallo welt schauen wir uns das dashboard an",
            "confidence": 1.0,
        },
        words=[],
    )
    repos.finish_analysis_run(db, run["id"], status="succeeded", diagnostics={})
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
        src_out_frame_exclusive=SCENE_FRAMES,
        seq_in_frame=0,
        seq_out_frame_exclusive=SCENE_FRAMES,
        lane=0,
        role="base",
    )
    repos.replace_scenes(db, project["id"], timeline["id"], [(0, SCENE_FRAMES)])
    return db, str(asset["id"])


def _board(tmp_path: Path, asset_id: str, *, script_gate: bool = False) -> Board:
    meta = BoardMeta(
        session_id="s1",
        asset_id=asset_id,
        created_utc="2026-08-04T00:00:00Z",
        task="overview short",
        target_seconds=20.0,
        script_gate=script_gate,
    )
    return Board.create(tmp_path / "board", meta)


def _specs(db: Database, board: Board, asset_id: str) -> dict[str, Any]:
    return {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}


def _script() -> Script:
    return Script(
        language="German",
        lines=[ScriptLine(chapter=1, scene_number=1, text="Hallo Welt, schau mal her.")],
    )


# --- BoardMeta: script_gate + script_approved_utc ------------------------------------------------


def test_board_meta_defaults_gate_off_and_unapproved() -> None:
    meta = BoardMeta(
        session_id="s1", asset_id="a1", created_utc="2026-08-04T00:00:00Z",
        task="t", target_seconds=20.0,
    )
    assert meta.script_gate is False
    assert meta.script_approved_utc is None


def test_board_meta_roundtrips_explicit_gate_fields() -> None:
    meta = BoardMeta(
        session_id="s1", asset_id="a1", created_utc="2026-08-04T00:00:00Z",
        task="t", target_seconds=20.0,
        script_gate=True, script_approved_utc="2026-08-05T10:00:00Z",
    )
    reloaded = BoardMeta.model_validate_json(meta.model_dump_json())
    assert reloaded.script_gate is True
    assert reloaded.script_approved_utc == "2026-08-05T10:00:00Z"


def test_old_meta_json_without_gate_fields_loads_unchanged(tmp_path: Path) -> None:
    """A meta.json written before Gate B existed has neither key at all — pydantic defaults
    must fill both in, not raise (``extra="forbid"`` only rejects UNKNOWN keys present in the
    document; it says nothing about keys the document never had)."""
    root = tmp_path / "board"
    (root / "scene_reviews").mkdir(parents=True)
    (root / "versions").mkdir(parents=True)
    old_meta = {
        "session_id": "s1",
        "asset_id": "a1",
        "created_utc": "2026-07-01T00:00:00Z",
        "task": "t",
        "format": "insta",
        "language": "German",
        "target_seconds": 20.0,
        "status": "active",
    }
    (root / "meta.json").write_text(json.dumps(old_meta), encoding="utf-8")

    board = Board.open(root)
    meta = board.meta()
    assert meta.script_gate is False
    assert meta.script_approved_utc is None


# --- Board.set_script_approved --------------------------------------------------------------------


def test_set_script_approved_writes_atomically_and_updates_meta(tmp_path: Path) -> None:
    board = _board(tmp_path, "a1", script_gate=True)
    assert board.meta().script_approved_utc is None

    board.set_script_approved("2026-08-05T10:00:00Z")

    assert board.meta().script_approved_utc == "2026-08-05T10:00:00Z"
    assert board.meta().script_gate is True  # untouched by the approval write


# --- synthesize_script_voice: deterministic gate -------------------------------------------------


def test_synthesize_script_voice_refuses_while_gate_pending(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id, script_gate=True)
    specs = _specs(db, board, asset_id)

    out = specs["synthesize_script_voice"].func()

    assert out["ok"] is False
    assert "script gate" in out["reason"]
    assert "approve" in out["reason"].lower()


def test_synthesize_script_voice_gate_lifts_after_approval(tmp_path: Path) -> None:
    """Once approved, the gate reason is gone — the tool falls through to its NEXT prereq
    (no storyline yet), never the gate."""
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id, script_gate=True)
    board.set_script_approved("2026-08-05T10:00:00Z")
    specs = _specs(db, board, asset_id)

    out = specs["synthesize_script_voice"].func()

    assert out["ok"] is False
    assert "script gate" not in out["reason"]
    assert "storyline" in out["reason"]


def test_synthesize_script_voice_ignores_a_disabled_gate(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id, script_gate=False)
    specs = _specs(db, board, asset_id)

    out = specs["synthesize_script_voice"].func()

    assert out["ok"] is False
    assert "script gate" not in out["reason"]


# --- Board.status(): script_gate + script_lines -------------------------------------------------


def test_status_script_gate_disabled_by_default(tmp_path: Path) -> None:
    board = _board(tmp_path, "a1")
    status = board.status()
    assert status["script_gate"] == {"enabled": False, "approved": False, "pending": False}
    assert "script_lines" not in status


def test_status_script_gate_pending_with_lines_once_script_exists(tmp_path: Path) -> None:
    board = _board(tmp_path, "a1", script_gate=True)
    board.save("script", _script())

    status = board.status()

    assert status["script_gate"] == {"enabled": True, "approved": False, "pending": True}
    assert status["script_lines"] == [
        {"chapter": 1, "scene_number": 1, "text": "Hallo Welt, schau mal her."}
    ]


def test_status_script_gate_not_pending_before_a_script_exists(tmp_path: Path) -> None:
    """Gate on, nothing to review yet — pending must not fire on a script nobody wrote."""
    board = _board(tmp_path, "a1", script_gate=True)

    status = board.status()

    assert status["script_gate"] == {"enabled": True, "approved": False, "pending": False}
    assert "script_lines" not in status


def test_status_script_gate_not_pending_once_approved(tmp_path: Path) -> None:
    board = _board(tmp_path, "a1", script_gate=True)
    board.save("script", _script())
    board.set_script_approved("2026-08-05T10:00:00Z")

    status = board.status()

    assert status["script_gate"] == {"enabled": True, "approved": True, "pending": False}
    assert "script_lines" not in status


# --- run_production: seeding the gate onto a FRESH board -----------------------------------------


def _ok_execute(
    db: Database,
    config: providers.AgentConfig,
    stage: providers.Stage,
    kind: orchestrator.TeamKind,
    task: str,
) -> orchestrator.StageOutcome:
    return orchestrator.StageOutcome(
        status="ok", weak=False, summary="done", team=kind, stage=stage
    )


def test_run_production_seeds_script_gate_true_on_a_fresh_board(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    config = providers.resolve_from_env({})

    result = production_orchestrator.run_production(
        db, config, asset_id=asset_id, session_id="sess1", task="t", target_seconds=20,
        script_gate=True, execute=_ok_execute,
    )

    assert result["board"]["meta"]["script_gate"] is True
    root = production_orchestrator.board_root_for(db, asset_id, "sess1")
    assert Board.open(root).meta().script_gate is True


def test_run_production_defaults_script_gate_false_on_a_fresh_board(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    config = providers.resolve_from_env({})

    result = production_orchestrator.run_production(
        db, config, asset_id=asset_id, session_id="sess1", task="t", target_seconds=20,
        execute=_ok_execute,
    )

    assert result["board"]["meta"]["script_gate"] is False


# --- orchestrator prompt: script-approval checkpoint paragraph -----------------------------------


def test_build_production_task_names_the_script_approval_checkpoint(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id, script_gate=True)

    task_text = production_orchestrator.build_production_task(
        db, board, asset_id=asset_id, task="overview short", target_seconds=20,
    )

    assert "SCRIPT-APPROVAL CHECKPOINT" in task_text
    assert "approve_script" in task_text
    assert "synthesize_script_voice" in task_text


# --- executor: approve_script rolls the gate back when the follow-up fails to start --------------


def test_approve_script_reverts_the_stamp_when_the_follow_up_run_fails_to_start(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """Review finding: ``set_script_approved`` used to persist BEFORE ``run_production_follow_up``
    was known to succeed. A session race, a config preflight failure, or any bug in that call
    then left the gate permanently open while the user read an error implying nothing had
    happened. The approval still has to land on the board before the follow-up run is enqueued
    (the voice tool reads it at job runtime), so the fix is a compensating rollback via
    ``Board.clear_script_approval()``, not a reorder — this pins exactly the assertion the
    review said was missing: the gate is closed again after the failure, not just the error
    text."""
    session_id = "sess-1"
    db, asset_id = _seed_scene(tmp_path)
    repos.create_production_session(
        db, session_id=session_id, asset_id=asset_id, created_utc="2026-08-04T00:00:00Z",
    )
    root = production_orchestrator.board_root_for(db, asset_id, session_id)
    Board.create(
        root,
        BoardMeta(
            session_id=session_id, asset_id=asset_id, created_utc="2026-08-04T00:00:00Z",
            task="t", target_seconds=20.0, script_gate=True,
        ),
    )
    repos.create_conversation(db, conversation_id="c1", created_utc="2026-08-04T00:00:00Z")
    repos.append_conversation_message(
        db, message_id="m-action", conversation_id="c1", role="assistant", kind="action",
        content={
            "tool": "start_short", "args": {}, "outcome": "running",
            "refs": {"session_id": session_id, "job_id": "job-x"},
        },
        created_utc="2026-08-04T00:00:00Z",
    )

    def _fails(db: Database, session_id: str, text: str) -> dict[str, Any]:
        raise RuntimeError("boom")

    monkeypatch.setattr("laura.chat.executor.run_production_follow_up", _fails)

    decision: RouterDecision = {
        "tool": "approve_script", "args": {"session_ref": session_id}, "fallback": False,
    }
    settings = Settings(workspace_root=tmp_path / "ws-executor", start_runner=False)

    messages = execute_decision(
        db, settings, conversation_id="c1", decision=decision, now_utc="2026-08-05T10:00:00Z",
    )

    assert messages[0]["kind"] == "text"
    assert messages[0]["content"]["text"] == (
        "Da ist beim Ausführen etwas schiefgelaufen — magst du es nochmal versuchen?"
    )
    assert Board.open(root).meta().script_approved_utc is None
