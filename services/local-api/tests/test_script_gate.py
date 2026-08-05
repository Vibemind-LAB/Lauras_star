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
from laura.short_creator.board_models import BoardMeta, Script, ScriptLine, content_hash
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
    assert board.meta().script_approved_script_hash is None

    board.set_script_approved("2026-08-05T10:00:00Z", "hash-abc")

    assert board.meta().script_approved_utc == "2026-08-05T10:00:00Z"
    assert board.meta().script_approved_script_hash == "hash-abc"
    assert board.meta().script_gate is True  # untouched by the approval write


def test_clear_script_approval_clears_both_the_timestamp_and_the_hash(tmp_path: Path) -> None:
    board = _board(tmp_path, "a1", script_gate=True)
    board.set_script_approved("2026-08-05T10:00:00Z", "hash-abc")

    board.clear_script_approval()

    assert board.meta().script_approved_utc is None
    assert board.meta().script_approved_script_hash is None


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
    """Once approved (with a hash that matches the CURRENT script), the gate reason is gone —
    the tool falls through to its NEXT prereq (no storyline yet), never the gate."""
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id, script_gate=True)
    script = _script()
    board.save("script", script)
    board.set_script_approved("2026-08-05T10:00:00Z", content_hash(script))
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


def test_synthesize_script_voice_refuses_when_script_changed_after_approval(
    tmp_path: Path,
) -> None:
    """Review finding: approval used to be a bare timestamp, so a post-approval script edit
    (a team rewrite via budget/capacity feedback) left the gate reading "approved" for text the
    user never actually signed off on. The stamped hash no longer matching the CURRENT script
    must refuse with a DISTINCT reason from the never-approved case."""
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id, script_gate=True)
    original = _script()
    board.save("script", original)
    board.set_script_approved("2026-08-05T10:00:00Z", content_hash(original))
    # The team rewrites the script AFTER approval — same board, new content, hash now differs.
    rewritten = Script(
        language="German",
        lines=[ScriptLine(chapter=1, scene_number=1, text="Ganz anderer Text jetzt.")],
    )
    board.save("script", rewritten)
    specs = _specs(db, board, asset_id)

    out = specs["synthesize_script_voice"].func()

    assert out["ok"] is False
    assert "script gate" in out["reason"]
    assert "changed after approval" in out["reason"]
    assert "re-approve" in out["reason"]


def test_synthesize_script_voice_proceeds_after_reapproving_the_changed_script(
    tmp_path: Path,
) -> None:
    """The stale gate lifts again once the user re-approves the NEW content — falls through to
    the next prereq (no storyline), same as the happy path."""
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id, script_gate=True)
    original = _script()
    board.save("script", original)
    board.set_script_approved("2026-08-05T10:00:00Z", content_hash(original))
    rewritten = Script(
        language="German",
        lines=[ScriptLine(chapter=1, scene_number=1, text="Ganz anderer Text jetzt.")],
    )
    board.save("script", rewritten)
    # Re-approve the NEW content.
    board.set_script_approved("2026-08-05T10:10:00Z", content_hash(rewritten))
    specs = _specs(db, board, asset_id)

    out = specs["synthesize_script_voice"].func()

    assert out["ok"] is False
    assert "script gate" not in out["reason"]
    assert "storyline" in out["reason"]


def test_synthesize_script_voice_refuses_when_approved_but_script_missing(tmp_path: Path) -> None:
    """Defensive: an approved gate whose script has since vanished from the board entirely
    (no plausible write path today, but the hash comparison must not silently pass a ``None``
    current hash) refuses with the stale reason rather than crashing or falling through."""
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id, script_gate=True)
    board.set_script_approved("2026-08-05T10:00:00Z", "some-hash")
    specs = _specs(db, board, asset_id)

    out = specs["synthesize_script_voice"].func()

    assert out["ok"] is False
    assert "script gate" in out["reason"]
    assert "changed after approval" in out["reason"]


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
    script = _script()
    board.save("script", script)
    board.set_script_approved("2026-08-05T10:00:00Z", content_hash(script))

    status = board.status()

    assert status["script_gate"] == {"enabled": True, "approved": True, "pending": False}
    assert "script_lines" not in status


def test_status_script_gate_re_pending_when_script_changed_after_approval(tmp_path: Path) -> None:
    """Review finding: approval used to be a bare timestamp — status() kept reporting
    "approved" for a script the team had since rewritten. The stamped hash no longer matching
    the CURRENT script must flip the gate back to pending, with fresh lines for the NEW text."""
    board = _board(tmp_path, "a1", script_gate=True)
    original = _script()
    board.save("script", original)
    board.set_script_approved("2026-08-05T10:00:00Z", content_hash(original))
    rewritten = Script(
        language="German",
        lines=[ScriptLine(chapter=1, scene_number=1, text="Ganz anderer Text jetzt.")],
    )
    board.save("script", rewritten)

    status = board.status()

    assert status["script_gate"] == {"enabled": True, "approved": False, "pending": True}
    assert status["script_lines"] == [
        {"chapter": 1, "scene_number": 1, "text": "Ganz anderer Text jetzt."}
    ]


def test_status_script_gate_approved_again_after_reapproving_the_changed_script(
    tmp_path: Path,
) -> None:
    board = _board(tmp_path, "a1", script_gate=True)
    original = _script()
    board.save("script", original)
    board.set_script_approved("2026-08-05T10:00:00Z", content_hash(original))
    rewritten = Script(
        language="German",
        lines=[ScriptLine(chapter=1, scene_number=1, text="Ganz anderer Text jetzt.")],
    )
    board.save("script", rewritten)
    board.set_script_approved("2026-08-05T10:10:00Z", content_hash(rewritten))

    status = board.status()

    assert status["script_gate"] == {"enabled": True, "approved": True, "pending": False}


def test_status_script_gate_revert_to_a_different_version_re_arms_the_gate(
    tmp_path: Path,
) -> None:
    """``Board.revert("script")`` restoring a DIFFERENT archived version than the one that was
    approved must re-arm the gate too — the stamped hash binds to specific content, not to "a
    script existed once"."""
    board = _board(tmp_path, "a1", script_gate=True)
    v1 = Script(
        language="German", lines=[ScriptLine(chapter=1, scene_number=1, text="Version eins.")],
    )
    board.save("script", v1)  # version 1
    v2 = Script(
        language="German", lines=[ScriptLine(chapter=1, scene_number=1, text="Version zwei.")],
    )
    board.save("script", v2)  # version 2, v1 archived
    board.set_script_approved("2026-08-05T10:00:00Z", content_hash(v2))

    board.revert("script", 1)  # back to v1 — different content than what was approved

    status = board.status()

    assert status["script_gate"]["approved"] is False
    assert status["script_gate"]["pending"] is True


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


# --- executor: approve_script rolls the gate back when the resume run fails to start -------------


def test_approve_script_reverts_the_stamp_when_the_resume_run_fails_to_start(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """Review finding: ``set_script_approved`` used to persist BEFORE the resume run
    (``run_production_resume``, spec 2026-08-05 modular production — was
    ``run_production_follow_up`` before MP4) was known to succeed. A session race, a config
    preflight failure, or any bug in that call then left the gate permanently open while the
    user read an error implying nothing had happened. The approval still has to land on the
    board before the resume run is enqueued (the voice tool reads it at job runtime), so the fix
    is a compensating rollback via ``Board.clear_script_approval()``, not a reorder — this pins
    exactly the assertion the review said was missing: the gate is closed again after the
    failure, not just the error text. This is a FRESH approval (no prior stamp), so the
    rollback's ``not already_current`` guard (MP4) does not change this test's outcome."""
    session_id = "sess-1"
    db, asset_id = _seed_scene(tmp_path)
    repos.create_production_session(
        db, session_id=session_id, asset_id=asset_id, created_utc="2026-08-04T00:00:00Z",
    )
    root = production_orchestrator.board_root_for(db, asset_id, session_id)
    board = Board.create(
        root,
        BoardMeta(
            session_id=session_id, asset_id=asset_id, created_utc="2026-08-04T00:00:00Z",
            task="t", target_seconds=20.0, script_gate=True,
        ),
    )
    # A script must exist for approve_script to get past the no-script guard (Finding 2) and
    # reach the write-then-enqueue sequence this test actually exercises.
    board.save("script", _script())
    repos.create_conversation(db, conversation_id="c1", created_utc="2026-08-04T00:00:00Z")
    repos.append_conversation_message(
        db, message_id="m-action", conversation_id="c1", role="assistant", kind="action",
        content={
            "tool": "start_short", "args": {}, "outcome": "running",
            "refs": {"session_id": session_id, "job_id": "job-x"},
        },
        created_utc="2026-08-04T00:00:00Z",
    )

    def _fails(db: Database, session_id: str) -> dict[str, Any]:
        raise RuntimeError("boom")

    monkeypatch.setattr("laura.chat.executor.run_production_resume", _fails)

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
