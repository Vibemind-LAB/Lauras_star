"""Chat executor: a validated router decision -> conversation messages + machinery calls
(spec 2026-08-03-chat-first).

``execute_decision`` must never raise — every failure becomes an assistant ``text`` message.
The Task-2 service functions are monkeypatched by NAME on ``laura.chat.executor`` (imported
into the module so patching works), so these tests never touch a real agent team or a real
production board.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from laura.auth import Principal
from laura.chat.executor import execute_decision, execute_import_approval
from laura.chat.router import RouterDecision
from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase

_NOW = "2026-08-03T10:00:00Z"
_NOW2 = "2026-08-03T10:05:00Z"


def _setup(tmp_path: Path) -> tuple[Database, Settings]:
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db: Database = SqliteDatabase(settings.db_path)
    db.migrate()
    return db, settings


def _conversation(db: Database, *, project_id: str | None = None) -> str:
    repos.create_conversation(db, conversation_id="c1", created_utc=_NOW)
    if project_id is not None:
        repos.set_conversation_project(db, "c1", project_id)
    return "c1"


def _project(db: Database, tmp_path: Path, *, name: str = "Drive-Test") -> dict[str, Any]:
    return repos.create_project(
        db, name=name, rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(tmp_path / f"ws-{name}"),
    )


def _decision(tool: str, args: dict[str, Any]) -> RouterDecision:
    return {"tool": tool, "args": args, "fallback": False}


def _conversation_row(db: Database, conversation_id: str) -> dict[str, Any]:
    row = repos.get_conversation(db, conversation_id)
    assert row is not None
    return row


def _seed_transcript(
    db: Database,
    project_id: str,
    *,
    display_name: str = "Clip 1",
    texts: list[str] | None = None,
    audio_sample_rate: int | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """One asset + one analysis run + N transcript segments, 48000 samples / 30 frames apart —
    seed sequence lifted from tests/test_semantic_sync.py's ``_seed_one_segment`` (the source
    of truth for the transcript_segments column names). Returns ``(asset, segment_ids)`` in
    the same order ``repos.get_transcript`` hands them back (ordered by ``start_sample``)."""
    texts = texts if texts is not None else ["hallo welt", "zweiter satz"]
    asset = repos.create_asset(
        db, project_id=project_id, type="video", display_name=display_name,
        source_path=f"/media/{display_name}.mp4",
    )
    if audio_sample_rate is not None:
        repos.update_asset_probe(
            db, asset["id"], type="video", duration_frames=None, rate_num=30, rate_den=1,
            audio_sample_rate=audio_sample_rate, start_timecode=None, width=None, height=None,
            codec_video=None, codec_audio=None, is_vfr=False, sha256=None,
        )
        updated = repos.get_asset(db, asset["id"])
        assert updated is not None
        asset = updated
    run = repos.create_analysis_run(db, asset_id=asset["id"], pipeline_version="test", config={})
    segment_ids = []
    for i, text in enumerate(texts):
        seg_id = repos.insert_segment_with_words(
            db, asset_id=asset["id"], run_id=run["id"], speaker_id=None,
            segment={
                "start_sample": i * 48_000, "end_sample": (i + 1) * 48_000,
                "start_frame": i * 30, "end_frame": (i + 1) * 30,
                "text": text, "confidence": 1.0,
            },
            words=[],
        )
        segment_ids.append(seg_id)
    return asset, segment_ids


def _seed_action(
    db: Database, conversation_id: str, *, session_id: str, job_id: str = "job-x",
    created_utc: str = _NOW,
) -> None:
    repos.append_conversation_message(
        db, message_id=f"m-action-{session_id}", conversation_id=conversation_id,
        role="assistant", kind="action",
        content={
            "tool": "start_short", "args": {}, "outcome": "running",
            "refs": {"session_id": session_id, "job_id": job_id},
        },
        created_utc=created_utc,
    )


# --- reply --------------------------------------------------------------------------------


def test_reply_appends_assistant_text(tmp_path: Path) -> None:
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("reply", {"text": "Hallo!"}), now_utc=_NOW,
    )

    assert len(messages) == 1
    assert messages[0]["role"] == "assistant" and messages[0]["kind"] == "text"
    assert messages[0]["content"] == {"text": "Hallo!"}
    stored = repos.list_conversation_messages(db, conversation_id)
    assert len(stored) == 1 and stored[0]["content"]["text"] == "Hallo!"
    assert _conversation_row(db, conversation_id)["updated_at"] == _NOW


# --- create_project -------------------------------------------------------------------------


def test_create_project_mirrors_projects_create_and_activates(tmp_path: Path) -> None:
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("create_project", {"name": "Drive-Test"}), now_utc=_NOW,
    )

    assert len(messages) == 1
    text = messages[0]["content"]["text"]
    assert text == "Projekt ‚Drive-Test' angelegt und aktiviert."

    conversation = _conversation_row(db, conversation_id)
    pid = conversation["active_project_id"]
    assert pid is not None
    project = repos.get_project(db, pid)
    assert project is not None
    assert project["name"] == "Drive-Test"
    assert project["sequence_rate_num"] == 30
    assert project["sequence_rate_den"] == 1
    assert bool(project["drop_frame"]) is False
    assert Path(project["workspace_root"]).is_dir()
    assert Path(project["workspace_root"]) == settings.workspace_root / f"project-{pid}"
    assert project["org_id"] is None, "no principal -> no org, matching a desktop/local caller"

    # Without a request principal (every pre-existing caller), the endpoint's audit trail is
    # still written — using the SAME implicit local-owner identity auth/deps.resolve_principal
    # falls back to, not a chat-invented actor.
    audit_events = repos.list_audit_events(db)
    audit_event = next(e for e in audit_events if e["action"] == "project.create")
    assert audit_event["entity_id"] == pid
    assert audit_event["principal_kind"] == "local"
    assert audit_event["org_id"] is None


def test_create_project_with_principal_sets_org_and_writes_audit(tmp_path: Path) -> None:
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)
    principal = Principal(kind="key", role="owner", user_id="user-1", org_id="org-1")

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("create_project", {"name": "Drive-Test"}), now_utc=_NOW,
        principal=principal,
    )

    pid = _conversation_row(db, conversation_id)["active_project_id"]
    assert pid is not None
    project = repos.get_project(db, pid)
    assert project is not None and project["org_id"] == "org-1"
    assert messages[0]["content"]["text"] == "Projekt ‚Drive-Test' angelegt und aktiviert."

    audit_events = repos.list_audit_events(db)
    audit_event = next(e for e in audit_events if e["action"] == "project.create")
    assert audit_event["entity_id"] == pid
    assert audit_event["principal_kind"] == "key"
    assert audit_event["principal_id"] == "user-1"
    assert audit_event["org_id"] == "org-1"


# --- switch_project --------------------------------------------------------------------------


def test_switch_project_resolves_exact_name_case_insensitive(tmp_path: Path) -> None:
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)
    project = _project(db, tmp_path, name="Drive-Test")

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("switch_project", {"ref": "drive-test"}), now_utc=_NOW,
    )

    assert _conversation_row(db, conversation_id)["active_project_id"] == project["id"]
    assert "Drive-Test" in messages[0]["content"]["text"]


def test_switch_project_resolves_id_prefix(tmp_path: Path) -> None:
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)
    project = _project(db, tmp_path, name="Other")

    ref = str(project["id"])[:6]
    execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("switch_project", {"ref": ref}), now_utc=_NOW,
    )

    assert _conversation_row(db, conversation_id)["active_project_id"] == project["id"]


def test_switch_project_ambiguous_or_missing_asks_and_leaves_active_untouched(
    tmp_path: Path,
) -> None:
    db, settings = _setup(tmp_path)
    project = _project(db, tmp_path, name="Anchor")
    conversation_id = _conversation(db, project_id=project["id"])
    _project(db, tmp_path, name="Foo")
    _project(db, tmp_path, name="Foo")  # two "Foo" projects -> ambiguous

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("switch_project", {"ref": "Foo"}), now_utc=_NOW,
    )
    assert _conversation_row(db, conversation_id)["active_project_id"] == project["id"]
    assert messages[0]["kind"] == "text"
    assert "Foo" in messages[0]["content"]["text"]

    messages2 = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("switch_project", {"ref": "nope-at-all"}), now_utc=_NOW2,
    )
    assert _conversation_row(db, conversation_id)["active_project_id"] == project["id"]
    assert messages2[0]["kind"] == "text"


# --- propose_import --------------------------------------------------------------------------


def test_propose_import_without_active_project_asks(tmp_path: Path) -> None:
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("propose_import", {"urls": ["https://x/a.mp4"]}), now_utc=_NOW,
    )

    assert messages[0]["kind"] == "text"
    assert "kein Projekt" in messages[0]["content"]["text"]


def test_propose_import_appends_pending_approval_card(tmp_path: Path) -> None:
    db, settings = _setup(tmp_path)
    project = _project(db, tmp_path)
    conversation_id = _conversation(db, project_id=project["id"])

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision(
            "propose_import", {"urls": ["https://x/a.mp4", "https://x/b.mp4"]}
        ),
        now_utc=_NOW,
    )

    assert len(messages) == 1
    card = messages[0]
    assert card["role"] == "assistant" and card["kind"] == "approval_request"
    assert card["content"]["status"] == "pending"
    assert card["content"]["action_type"] == "import_urls"
    assert card["content"]["payload"] == {
        "urls": ["https://x/a.mp4", "https://x/b.mp4"], "project_id": project["id"],
    }
    assert card["content"]["decided_at"] is None
    assert card["content"]["result"] is None


# --- start_short -----------------------------------------------------------------------------


def _fake_auto_short(
    db: Any, project_id: str, *, topic: str, target_seconds: int, format: str, language: str,
) -> dict[str, Any]:
    return {
        "session_id": "sess-1",
        "job_id": "job-1",
        "asset_id": "asset-1",
        "scene_numbers": [1, 2],
        "rationale": f"scout rationale for {topic}",
        "fallback": False,
        "ranking": [],
        "warnings": ["config warning"],
    }


def _fake_auto_short_fails(*_: Any, **__: Any) -> dict[str, Any]:
    raise HTTPException(422, detail={"reason": "no material found for topic", "skipped": []})


def test_start_short_without_active_project_asks(tmp_path: Path) -> None:
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("start_short", {"topic": "Katzen"}), now_utc=_NOW,
    )
    assert messages[0]["kind"] == "text"
    assert "kein Projekt" in messages[0]["content"]["text"]


def test_start_short_success_appends_text_and_running_action(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    monkeypatch.setattr("laura.chat.executor.run_project_auto_short", _fake_auto_short)
    db, settings = _setup(tmp_path)
    project = _project(db, tmp_path)
    conversation_id = _conversation(db, project_id=project["id"])

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("start_short", {"topic": "Katzen"}), now_utc=_NOW,
    )

    assert len(messages) == 2
    text_msg, action_msg = messages
    assert text_msg["kind"] == "text"
    assert "scout rationale for Katzen" in text_msg["content"]["text"]
    assert "config warning" in text_msg["content"]["text"]

    assert action_msg["kind"] == "action"
    assert action_msg["content"]["tool"] == "start_short"
    assert action_msg["content"]["outcome"] == "running"
    assert action_msg["content"]["refs"] == {"session_id": "sess-1", "job_id": "job-1"}


def test_start_short_http_exception_becomes_honest_text(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    monkeypatch.setattr("laura.chat.executor.run_project_auto_short", _fake_auto_short_fails)
    db, settings = _setup(tmp_path)
    project = _project(db, tmp_path)
    conversation_id = _conversation(db, project_id=project["id"])

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("start_short", {"topic": "Katzen"}), now_utc=_NOW,
    )

    assert len(messages) == 1
    assert messages[0]["kind"] == "text"
    assert messages[0]["content"]["text"] == "no material found for topic"


# --- start_overview --------------------------------------------------------------------------


def _fake_auto_overview(
    db: Any, project_id: str, *, topic: str, target_seconds: int, language: str,
) -> dict[str, Any]:
    return {
        "sequence_id": "seq-1",
        "source_timeline_id": "tl-1",
        "clips": [],
        "rationale": f"overview rationale for {topic}",
        "fallback": False,
        "ranking": [],
        "warnings": [],
        "export_id": "exp-1",
        "job_id": "job-2",
    }


def test_start_overview_without_active_project_asks(tmp_path: Path) -> None:
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("start_overview", {"topic": "Katzen"}), now_utc=_NOW,
    )
    assert messages[0]["kind"] == "text"
    assert "kein Projekt" in messages[0]["content"]["text"]


def test_start_overview_success_appends_text_and_action_with_refs(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    monkeypatch.setattr("laura.chat.executor.run_project_auto_overview", _fake_auto_overview)
    db, settings = _setup(tmp_path)
    project = _project(db, tmp_path)
    conversation_id = _conversation(db, project_id=project["id"])

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("start_overview", {"topic": "Katzen"}), now_utc=_NOW,
    )

    assert len(messages) == 2
    text_msg, action_msg = messages
    assert "overview rationale for Katzen" in text_msg["content"]["text"]
    assert action_msg["content"]["tool"] == "start_overview"
    assert action_msg["content"]["outcome"] == "running"
    assert action_msg["content"]["refs"] == {
        "export_id": "exp-1", "job_id": "job-2", "sequence_id": "seq-1",
    }


def test_start_overview_http_exception_becomes_honest_text(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    def _fails(*_: Any, **__: Any) -> dict[str, Any]:
        raise HTTPException(422, detail={"reason": "no usable windows for topic"})

    monkeypatch.setattr("laura.chat.executor.run_project_auto_overview", _fails)
    db, settings = _setup(tmp_path)
    project = _project(db, tmp_path)
    conversation_id = _conversation(db, project_id=project["id"])

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("start_overview", {"topic": "Katzen"}), now_utc=_NOW,
    )
    assert messages[0]["content"]["text"] == "no usable windows for topic"


# --- follow_up -------------------------------------------------------------------------------


def test_follow_up_resolves_exact_session_id_and_appends_running_action(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    def _fake_follow_up(db: Any, session_id: str, text: str) -> dict[str, Any]:
        assert session_id == "sess-1"
        return {"session_id": session_id, "job_id": "job-9", "warnings": []}

    monkeypatch.setattr("laura.chat.executor.run_production_follow_up", _fake_follow_up)
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)
    _seed_action(db, conversation_id, session_id="sess-1")

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("follow_up", {"session_ref": "sess-1", "text": "mach lauter"}),
        now_utc=_NOW,
    )

    assert len(messages) == 1
    assert messages[0]["kind"] == "action"
    assert messages[0]["content"]["refs"] == {"session_id": "sess-1", "job_id": "job-9"}
    assert messages[0]["content"]["outcome"] == "running"


@pytest.mark.parametrize("placeholder", ["last", "latest", "Last", ""])
def test_follow_up_placeholder_ref_falls_back_to_newest_action(
    tmp_path: Path, monkeypatch: Any, placeholder: str,
) -> None:
    """Only the literal placeholders ("last"/"latest", or an empty ref) may fall back to the
    newest action — an explicit ref never does (see the unmatched-ref test below)."""
    calls: list[str] = []

    def _fake_follow_up(db: Any, session_id: str, text: str) -> dict[str, Any]:
        calls.append(session_id)
        return {"session_id": session_id, "job_id": "job-9", "warnings": []}

    monkeypatch.setattr("laura.chat.executor.run_production_follow_up", _fake_follow_up)
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)
    _seed_action(db, conversation_id, session_id="sess-1", created_utc=_NOW)
    _seed_action(db, conversation_id, session_id="sess-2", created_utc=_NOW2)

    execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("follow_up", {"session_ref": placeholder, "text": "mach lauter"}),
        now_utc=_NOW2,
    )

    assert calls == ["sess-2"], "a placeholder ref resolves to the newest action"


def test_follow_up_explicit_unmatched_ref_asks_instead_of_executing(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """An explicit ref the thread never saw (e.g. hallucinated by the router) must trigger
    the Rückfrage, never silently redirect the mutation to the newest session (spec
    2026-08-03-chat-first: "kann er nicht auflösen → Rückfrage")."""
    calls: list[str] = []

    def _fake_follow_up(db: Any, session_id: str, text: str) -> dict[str, Any]:
        calls.append(session_id)
        return {"session_id": session_id, "job_id": "job-9", "warnings": []}

    monkeypatch.setattr("laura.chat.executor.run_production_follow_up", _fake_follow_up)
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)
    _seed_action(db, conversation_id, session_id="sess-1", created_utc=_NOW)
    _seed_action(db, conversation_id, session_id="sess-2", created_utc=_NOW2)

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision(
            "follow_up", {"session_ref": "sess-hallucinated", "text": "mach lauter"}
        ),
        now_utc=_NOW2,
    )

    assert calls == [], "an unmatched explicit ref must never execute"
    assert messages[0]["kind"] == "text"
    assert "Session" in messages[0]["content"]["text"]


def test_follow_up_exact_session_id_match_wins_over_a_newer_action(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """An exact ``session_id`` match must win even when a NEWER, non-matching action exists —
    resolution is "exact match first, newest as fallback", never "newest first"."""
    calls: list[str] = []

    def _fake_follow_up(db: Any, session_id: str, text: str) -> dict[str, Any]:
        calls.append(session_id)
        return {"session_id": session_id, "job_id": "job-9", "warnings": []}

    monkeypatch.setattr("laura.chat.executor.run_production_follow_up", _fake_follow_up)
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)
    _seed_action(db, conversation_id, session_id="s-old", created_utc=_NOW)
    _seed_action(db, conversation_id, session_id="s-new", created_utc=_NOW2)

    execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("follow_up", {"session_ref": "s-old", "text": "mach lauter"}),
        now_utc=_NOW2,
    )

    assert calls == ["s-old"], "the exact match must win, not the newer action"


def test_follow_up_without_any_session_asks(tmp_path: Path) -> None:
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("follow_up", {"session_ref": "last", "text": "mach lauter"}),
        now_utc=_NOW,
    )
    assert messages[0]["kind"] == "text"
    assert "Session" in messages[0]["content"]["text"]


def test_follow_up_http_exception_becomes_honest_text(tmp_path: Path, monkeypatch: Any) -> None:
    def _fails(db: Any, session_id: str, text: str) -> dict[str, Any]:
        raise HTTPException(404, "session not found")

    monkeypatch.setattr("laura.chat.executor.run_production_follow_up", _fails)
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)
    _seed_action(db, conversation_id, session_id="sess-1")

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("follow_up", {"session_ref": "sess-1", "text": "x"}), now_utc=_NOW,
    )
    assert messages[0]["kind"] == "text"
    assert messages[0]["content"]["text"] == "session not found"


# --- revert ----------------------------------------------------------------------------------


def test_revert_success_confirms_version(tmp_path: Path, monkeypatch: Any) -> None:
    calls: list[tuple[str, str, int]] = []

    def _fake_revert(db: Any, session_id: str, artifact: str, version: int) -> dict[str, Any]:
        calls.append((session_id, artifact, version))
        return {"ok": True}

    monkeypatch.setattr("laura.chat.executor.run_production_revert", _fake_revert)
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)
    _seed_action(db, conversation_id, session_id="sess-1")

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision(
            "revert", {"session_ref": "sess-1", "artifact": "cutlist", "version": 2}
        ),
        now_utc=_NOW,
    )

    assert calls == [("sess-1", "cutlist", 2)]
    assert len(messages) == 1
    assert messages[0]["kind"] == "text"
    assert messages[0]["content"]["text"] == "zurückgedreht auf v2"


def test_revert_conflict_becomes_honest_text(tmp_path: Path, monkeypatch: Any) -> None:
    def _fails(db: Any, session_id: str, artifact: str, version: int) -> dict[str, Any]:
        raise HTTPException(409, "run in progress — revert would race the team")

    monkeypatch.setattr("laura.chat.executor.run_production_revert", _fails)
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)
    _seed_action(db, conversation_id, session_id="sess-1")

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision(
            "revert", {"session_ref": "sess-1", "artifact": "cutlist", "version": 1}
        ),
        now_utc=_NOW,
    )
    assert messages[0]["content"]["text"] == "run in progress — revert would race the team"


def test_revert_explicit_unmatched_ref_asks_instead_of_executing(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """Same Rückfrage rule as follow_up: a revert aimed at a ref the thread never saw must
    ask back, not rewind the newest session."""
    calls: list[tuple[str, str, int]] = []

    def _fake_revert(db: Any, session_id: str, artifact: str, version: int) -> dict[str, Any]:
        calls.append((session_id, artifact, version))
        return {"ok": True}

    monkeypatch.setattr("laura.chat.executor.run_production_revert", _fake_revert)
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)
    _seed_action(db, conversation_id, session_id="sess-1", created_utc=_NOW)
    _seed_action(db, conversation_id, session_id="sess-2", created_utc=_NOW2)

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision(
            "revert", {"session_ref": "sess-hallucinated", "artifact": "cutlist", "version": 1}
        ),
        now_utc=_NOW2,
    )

    assert calls == [], "an unmatched explicit ref must never execute"
    assert messages[0]["kind"] == "text"
    assert "Session" in messages[0]["content"]["text"]


def test_revert_without_any_session_asks(tmp_path: Path) -> None:
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision(
            "revert", {"session_ref": "last", "artifact": "cutlist", "version": 1}
        ),
        now_utc=_NOW,
    )
    assert messages[0]["kind"] == "text"
    assert "Session" in messages[0]["content"]["text"]


# --- review_transcript ------------------------------------------------------------------------


def test_review_transcript_returns_card_with_segments_and_total(tmp_path: Path) -> None:
    db, settings = _setup(tmp_path)
    project = _project(db, tmp_path)
    conversation_id = _conversation(db, project_id=project["id"])
    asset, _seg_ids = _seed_transcript(db, project["id"], display_name="Clip 1")

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("review_transcript", {"asset_ref": "Clip 1"}), now_utc=_NOW,
    )

    assert len(messages) == 1
    card = messages[0]
    assert card["kind"] == "action"
    assert card["content"]["tool"] == "review_transcript"
    assert card["content"]["refs"] == {"asset_id": asset["id"]}
    assert card["content"]["outcome"] == "done"
    payload = card["content"]["payload"]
    assert payload["confirmed_at"] is None
    assert payload["total"] == 2
    assert payload["segments"][0] == {
        "index": 1, "id": _seg_ids[0], "start_s": 0.0, "text": "hallo welt",
    }
    # audio_sample_rate is NULL on this asset (no probe run) -> falls back to 16000.
    assert payload["segments"][1]["start_s"] == 3.0  # 48000 samples / 16000 Hz


def test_review_transcript_uses_real_audio_sample_rate_when_probed(tmp_path: Path) -> None:
    db, settings = _setup(tmp_path)
    project = _project(db, tmp_path)
    conversation_id = _conversation(db, project_id=project["id"])
    _seed_transcript(db, project["id"], display_name="Clip 1", audio_sample_rate=48_000)

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("review_transcript", {"asset_ref": "Clip 1"}), now_utc=_NOW,
    )

    payload = messages[0]["content"]["payload"]
    assert payload["segments"][1]["start_s"] == 1.0  # 48000 samples / 48000 Hz


def test_review_transcript_without_active_project_asks(tmp_path: Path) -> None:
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("review_transcript", {"asset_ref": "Clip 1"}), now_utc=_NOW,
    )
    assert messages[0]["kind"] == "text"
    assert "kein Projekt" in messages[0]["content"]["text"]


def test_review_transcript_unknown_asset_ref_asks_and_lists_names(tmp_path: Path) -> None:
    db, settings = _setup(tmp_path)
    project = _project(db, tmp_path)
    conversation_id = _conversation(db, project_id=project["id"])
    _seed_transcript(db, project["id"], display_name="Clip 1")
    _seed_transcript(db, project["id"], display_name="Clip 2")

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("review_transcript", {"asset_ref": "Clip 9"}), now_utc=_NOW,
    )

    assert len(messages) == 1
    assert messages[0]["kind"] == "text"
    text = messages[0]["content"]["text"]
    assert "Clip 1" in text and "Clip 2" in text


def test_review_transcript_no_assets_in_project_asks_without_listing(tmp_path: Path) -> None:
    db, settings = _setup(tmp_path)
    project = _project(db, tmp_path)
    conversation_id = _conversation(db, project_id=project["id"])

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("review_transcript", {"asset_ref": "Clip 1"}), now_utc=_NOW,
    )
    assert messages[0]["kind"] == "text"
    assert "keine Videos" in messages[0]["content"]["text"]


def test_review_transcript_over_limit_caps_segments_without_a_duplicate_text_message(
    tmp_path: Path,
) -> None:
    """Finding 5: the card itself renders "… und N weitere Segmente" from
    payload.total - payload.segments.length (ReviewTranscriptCard in ActionCard.tsx) — a
    second text message here just repeated the same fact, so this must be the ONLY message."""
    db, settings = _setup(tmp_path)
    project = _project(db, tmp_path)
    conversation_id = _conversation(db, project_id=project["id"])
    texts = [f"segment {i}" for i in range(105)]
    _seed_transcript(db, project["id"], display_name="Long Clip", texts=texts)

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("review_transcript", {"asset_ref": "Long Clip"}), now_utc=_NOW,
    )

    assert len(messages) == 1
    card = messages[0]
    assert card["kind"] == "action"
    assert card["content"]["payload"]["total"] == 105
    assert len(card["content"]["payload"]["segments"]) == 100


def test_review_transcript_no_transcript_run_returns_empty_card(tmp_path: Path) -> None:
    """An asset that resolves but has no analysis run at all (no ASR ever ran) must still
    produce a card, never a crash — deliberately treated as segments=[]/total=0 rather than
    a special error text (see task-5-report.md's "no transcript run" design note)."""
    db, settings = _setup(tmp_path)
    project = _project(db, tmp_path)
    conversation_id = _conversation(db, project_id=project["id"])
    repos.create_asset(
        db, project_id=project["id"], type="video", display_name="No Transcript",
        source_path="/media/no-transcript.mp4",
    )

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("review_transcript", {"asset_ref": "No Transcript"}), now_utc=_NOW,
    )

    assert len(messages) == 1
    card = messages[0]
    assert card["kind"] == "action"
    assert card["content"]["payload"]["segments"] == []
    assert card["content"]["payload"]["total"] == 0


# --- correct_transcript ------------------------------------------------------------------------


def test_correct_transcript_updates_segment_and_reindexes(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    calls: list[tuple[str, list[str]]] = []

    def _fake_reindex(db: Any, asset_id: str, segment_ids: list[str]) -> int:
        calls.append((asset_id, list(segment_ids)))
        return len(segment_ids)

    monkeypatch.setattr("laura.chat.executor.reindex_segments", _fake_reindex)
    db, settings = _setup(tmp_path)
    project = _project(db, tmp_path)
    conversation_id = _conversation(db, project_id=project["id"])
    asset, seg_ids = _seed_transcript(db, project["id"], display_name="Clip 1")

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision(
            "correct_transcript",
            {"asset_ref": "Clip 1", "corrections": [{"segment_index": 1, "text": "Karpathy"}]},
        ),
        now_utc=_NOW,
    )

    assert calls == [(asset["id"], [seg_ids[0]])]
    seg = repos.get_segment(db, seg_ids[0])
    assert seg is not None and seg["text"] == "Karpathy"

    assert len(messages) == 2
    assert messages[0]["kind"] == "text"
    assert messages[0]["content"]["text"] == "1 Segment korrigiert."
    assert messages[1]["kind"] == "action"
    assert messages[1]["content"]["tool"] == "review_transcript"
    assert messages[1]["content"]["payload"]["segments"][0]["text"] == "Karpathy"

    audit_events = repos.list_audit_events(db)
    audit_event = next(e for e in audit_events if e["action"] == "transcript.update")
    assert audit_event["entity_id"] == seg_ids[0]
    assert audit_event["principal_kind"] == "local"  # no principal -> system_principal()


def test_correct_transcript_with_principal_audits_it(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "laura.chat.executor.reindex_segments", lambda *_a, **_k: 0
    )
    db, settings = _setup(tmp_path)
    project = _project(db, tmp_path)
    conversation_id = _conversation(db, project_id=project["id"])
    _asset, seg_ids = _seed_transcript(db, project["id"], display_name="Clip 1")
    principal = Principal(kind="key", role="owner", user_id="user-1", org_id="org-1")

    execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision(
            "correct_transcript",
            {"asset_ref": "Clip 1", "corrections": [{"segment_index": 1, "text": "x"}]},
        ),
        now_utc=_NOW, principal=principal,
    )

    audit_events = repos.list_audit_events(db)
    audit_event = next(e for e in audit_events if e["action"] == "transcript.update")
    assert audit_event["entity_id"] == seg_ids[0]
    assert audit_event["principal_kind"] == "key"
    assert audit_event["principal_id"] == "user-1"


def test_correct_transcript_multiple_corrections_audits_and_reindexes_each(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    calls: list[tuple[str, list[str]]] = []

    def _fake_reindex(db: Any, asset_id: str, segment_ids: list[str]) -> int:
        calls.append((asset_id, list(segment_ids)))
        return len(segment_ids)

    monkeypatch.setattr("laura.chat.executor.reindex_segments", _fake_reindex)
    db, settings = _setup(tmp_path)
    project = _project(db, tmp_path)
    conversation_id = _conversation(db, project_id=project["id"])
    asset, seg_ids = _seed_transcript(db, project["id"], display_name="Clip 1")

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision(
            "correct_transcript",
            {
                "asset_ref": "Clip 1",
                "corrections": [
                    {"segment_index": 1, "text": "eins"},
                    {"segment_index": 2, "text": "zwei"},
                ],
            },
        ),
        now_utc=_NOW,
    )

    # Each correction re-indexes its OWN segment id (not a batched call for both) — mirrors
    # the HTTP PATCH /transcript/segments/{id} endpoint's own sequencing.
    assert calls == [(asset["id"], [seg_ids[0]]), (asset["id"], [seg_ids[1]])]
    assert messages[0]["content"]["text"] == "2 Segmente korrigiert."
    audit_events = [e for e in repos.list_audit_events(db) if e["action"] == "transcript.update"]
    assert {e["entity_id"] for e in audit_events} == set(seg_ids)


def test_correct_transcript_unknown_segment_index_returns_error_with_range_no_writes(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    calls: list[tuple[str, list[str]]] = []

    def _fake_reindex(db: Any, asset_id: str, segment_ids: list[str]) -> int:
        calls.append((asset_id, list(segment_ids)))
        return len(segment_ids)

    monkeypatch.setattr("laura.chat.executor.reindex_segments", _fake_reindex)
    db, settings = _setup(tmp_path)
    project = _project(db, tmp_path)
    conversation_id = _conversation(db, project_id=project["id"])
    _asset, seg_ids = _seed_transcript(db, project["id"], display_name="Clip 1")

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision(
            "correct_transcript",
            {"asset_ref": "Clip 1", "corrections": [{"segment_index": 5, "text": "x"}]},
        ),
        now_utc=_NOW,
    )

    assert len(messages) == 1
    assert messages[0]["kind"] == "text"
    assert "1–2" in messages[0]["content"]["text"]
    assert calls == [], "an out-of-range index must never reach reindex_segments"
    seg = repos.get_segment(db, seg_ids[0])
    assert seg is not None and seg["text"] == "hallo welt", "no correction may apply on failure"
    assert repos.list_audit_events(db) == []


def test_correct_transcript_unknown_asset_ref_asks_no_card(tmp_path: Path) -> None:
    db, settings = _setup(tmp_path)
    project = _project(db, tmp_path)
    conversation_id = _conversation(db, project_id=project["id"])
    _seed_transcript(db, project["id"], display_name="Clip 1")

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision(
            "correct_transcript",
            {"asset_ref": "Nope", "corrections": [{"segment_index": 1, "text": "x"}]},
        ),
        now_utc=_NOW,
    )

    assert len(messages) == 1
    assert messages[0]["kind"] == "text"
    assert "Clip 1" in messages[0]["content"]["text"]


def test_correct_transcript_no_transcript_run_reports_no_segments_no_writes(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """An asset that resolves but has no transcript run at all: total=0, so the range text's
    ``total == 0`` branch fires ("noch keine Segmente") instead of a nonsensical "1–0" range,
    and nothing is written (mirrors the out-of-range-index test above)."""
    calls: list[tuple[str, list[str]]] = []

    def _fake_reindex(db: Any, asset_id: str, segment_ids: list[str]) -> int:
        calls.append((asset_id, list(segment_ids)))
        return len(segment_ids)

    monkeypatch.setattr("laura.chat.executor.reindex_segments", _fake_reindex)
    db, settings = _setup(tmp_path)
    project = _project(db, tmp_path)
    conversation_id = _conversation(db, project_id=project["id"])
    repos.create_asset(
        db, project_id=project["id"], type="video", display_name="No Transcript",
        source_path="/media/no-transcript.mp4",
    )

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision(
            "correct_transcript",
            {"asset_ref": "No Transcript", "corrections": [{"segment_index": 1, "text": "x"}]},
        ),
        now_utc=_NOW,
    )

    assert len(messages) == 1
    assert messages[0]["kind"] == "text"
    assert "keine Segmente" in messages[0]["content"]["text"]
    assert calls == []
    assert repos.list_audit_events(db) == []


def test_correct_transcript_without_active_project_asks(tmp_path: Path) -> None:
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision(
            "correct_transcript",
            {"asset_ref": "Clip 1", "corrections": [{"segment_index": 1, "text": "x"}]},
        ),
        now_utc=_NOW,
    )
    assert messages[0]["kind"] == "text"
    assert "kein Projekt" in messages[0]["content"]["text"]


# --- confirm_transcript ------------------------------------------------------------------------


def test_confirm_transcript_sets_stamp_and_replies_german(tmp_path: Path) -> None:
    db, settings = _setup(tmp_path)
    project = _project(db, tmp_path)
    conversation_id = _conversation(db, project_id=project["id"])
    asset, _seg_ids = _seed_transcript(db, project["id"], display_name="Clip 1")

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("confirm_transcript", {"asset_ref": "Clip 1"}), now_utc=_NOW,
    )

    # Finding 6: confirm re-appends a FRESH review_transcript card so its badge flips after
    # reload — the text reply plus the refreshed card, no leftover remainder text message
    # (Finding 5 dropped that).
    assert len(messages) == 2
    assert messages[0]["kind"] == "text"
    assert messages[0]["content"]["text"] == "Transkript von ‚Clip 1' bestätigt."
    assert messages[1]["kind"] == "action"
    assert messages[1]["content"]["tool"] == "review_transcript"
    assert messages[1]["content"]["payload"]["confirmed_at"] == _NOW
    updated = repos.get_asset(db, asset["id"])
    assert updated is not None and updated["transcript_confirmed_at"] == _NOW

    # Audit parity with the HTTP twin POST /assets/{asset_id}/transcript:confirm
    # (api/analysis.py), which records exactly this action/entity_type/entity_id.
    audit_events = repos.list_audit_events(db)
    assert len(audit_events) == 1
    audit_event = audit_events[0]
    assert audit_event["action"] == "transcript.confirm"
    assert audit_event["entity_type"] == "asset"
    assert audit_event["entity_id"] == asset["id"]
    assert audit_event["principal_kind"] == "local"  # no principal -> system_principal()


def test_confirm_transcript_with_principal_audits_it(tmp_path: Path) -> None:
    db, settings = _setup(tmp_path)
    project = _project(db, tmp_path)
    conversation_id = _conversation(db, project_id=project["id"])
    asset, _seg_ids = _seed_transcript(db, project["id"], display_name="Clip 1")
    principal = Principal(kind="key", role="owner", user_id="user-1", org_id="org-1")

    execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("confirm_transcript", {"asset_ref": "Clip 1"}), now_utc=_NOW,
        principal=principal,
    )

    audit_events = repos.list_audit_events(db)
    assert len(audit_events) == 1
    audit_event = audit_events[0]
    assert audit_event["action"] == "transcript.confirm"
    assert audit_event["entity_id"] == asset["id"]
    assert audit_event["principal_kind"] == "key"
    assert audit_event["principal_id"] == "user-1"


def test_confirm_transcript_unknown_asset_ref_asks_no_card(tmp_path: Path) -> None:
    db, settings = _setup(tmp_path)
    project = _project(db, tmp_path)
    conversation_id = _conversation(db, project_id=project["id"])

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("confirm_transcript", {"asset_ref": "Nope"}), now_utc=_NOW,
    )
    assert len(messages) == 1
    assert messages[0]["kind"] == "text"
    assert "keine Videos" in messages[0]["content"]["text"]


def test_confirm_transcript_without_active_project_asks(tmp_path: Path) -> None:
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("confirm_transcript", {"asset_ref": "Clip 1"}), now_utc=_NOW,
    )
    assert messages[0]["kind"] == "text"
    assert "kein Projekt" in messages[0]["content"]["text"]


# --- approve_script (Gate B script checkpoint, Task 7) -------------------------------------------


def _seed_board(
    db: Database, tmp_path: Path, *, session_id: str, script_gate: bool = True,
    with_script: bool = True,
) -> str:
    """Project (REAL workspace_root under tmp_path) + asset + production session + a board
    already created via ``Board.create`` — mirrors ``test_production_api.py``'s
    ``_seed_session_with_board`` (kept local per this repo's self-contained-test-file
    convention). ``with_script`` (default True) also saves a one-line script onto the board —
    most approve_script tests need one to get past the Finding-2 no-script guard; pass False
    for the tests that specifically exercise that guard. Returns ``asset_id``."""
    from laura.short_creator.board import Board
    from laura.short_creator.board_models import BoardMeta, Script, ScriptLine
    from laura.short_creator.production_orchestrator import board_root_for

    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(tmp_path / "ws" / "proj"),
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a", source_path="/tmp/a.mp4",
    )
    repos.create_production_session(
        db, session_id=session_id, asset_id=asset["id"], created_utc=_NOW,
    )
    root = board_root_for(db, asset["id"], session_id)
    meta = BoardMeta(
        session_id=session_id, asset_id=asset["id"], created_utc=_NOW,
        task="t", target_seconds=30.0, script_gate=script_gate,
    )
    board = Board.create(root, meta)
    if with_script:
        board.save(
            "script",
            Script(
                language="German",
                lines=[ScriptLine(chapter=1, scene_number=1, text="Hallo Welt, schau mal her.")],
            ),
        )
    return str(asset["id"])


def test_approve_script_sets_approved_and_starts_resume_run(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """MP4: approve_script enqueues a pure resume (:func:`run_production_resume`), not a
    text follow-up — no ``message`` involved at all, so there is nothing to assert about its
    content anymore (was: ``"freigegeben" in text``)."""
    from laura.short_creator.board import Board
    from laura.short_creator.board_models import Script, content_hash
    from laura.short_creator.production_orchestrator import board_root_for

    def _fake_resume(db: Any, session_id: str) -> dict[str, Any]:
        assert session_id == "sess-1"
        return {"session_id": session_id, "job_id": "job-42", "warnings": []}

    monkeypatch.setattr("laura.chat.executor.run_production_resume", _fake_resume)
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)
    asset_id = _seed_board(db, tmp_path, session_id="sess-1")
    _seed_action(db, conversation_id, session_id="sess-1")

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("approve_script", {"session_ref": "sess-1"}), now_utc=_NOW,
    )

    assert len(messages) == 1
    assert messages[0]["kind"] == "action"
    assert messages[0]["content"]["tool"] == "approve_script"
    assert messages[0]["content"]["refs"] == {"session_id": "sess-1", "job_id": "job-42"}
    assert messages[0]["content"]["outcome"] == "running"

    board = Board.open(board_root_for(db, asset_id, "sess-1"))
    assert board.meta().script_approved_utc == _NOW
    script = board.load("script")
    assert isinstance(script, Script)
    assert board.meta().script_approved_script_hash == content_hash(script)


def _fill_chain(board: Any) -> None:
    """Save one valid artifact for every step of the chain, so ``resume_point`` == 'done'.
    Mirrors ``test_production_orchestrator.py``'s module-private helper of the same name/purpose
    (kept local per this file's self-contained-test-file convention) — ``_seed_board``'s asset
    has no rough-cut/transcript, so ``_expected_scenes_for`` is ``[]`` here and no scene reviews
    are needed. Re-saves ``script`` AFTER ``storyline`` even when a script already exists on the
    board: ``Board.save`` invalidates everything DOWNSTREAM of the artifact it just wrote
    (module docstring), so saving ``storyline`` alone would archive-and-remove the board's
    existing script out from under a caller who approved it first — callers that need the
    APPROVED script to still be the one on the board after this call must re-approve using the
    hash of whatever ``board.load("script")`` returns AFTER this call, not before."""
    from laura.short_creator.board_models import (
        Chapter,
        ContactSheet,
        ContactSheetTile,
        Cutlist,
        CutSegment,
        QaReport,
        RenderCheck,
        RenderReport,
        Script,
        ScriptLine,
        Storyline,
        VoiceArtifact,
    )

    board.save("storyline", Storyline(red_thread="t", arc=[Chapter(
        chapter=1, role="hook", message="m", scene_numbers=[1], target_seconds=3.0)]))
    board.save("script", Script(
        language="German",
        lines=[ScriptLine(chapter=1, scene_number=1, text="Hallo Welt, schau mal her.")],
    ))
    board.save("voice", VoiceArtifact(script_hash="h", mp3_path="/tmp/v.mp3", voice_s=3.0))
    board.save("cutlist", Cutlist(segments=[CutSegment(
        order=0, scene_number=1, start_frame=0, end_frame_exclusive=90)]))
    board.save("contact_sheet", ContactSheet(png_path="/tmp/s.png", cols=1, rows=1, tiles=[
        ContactSheetTile(order=0, scene_number=1, frame=45, label="0 S1")]))
    board.save("render_report", RenderReport(
        export_id="exp1", video_s=3.0, voice_s=3.0, width=1920, height=1080,
        checks=[RenderCheck(name="export_ready", ok=True)]))
    board.save("qa_report", QaReport(verdict="ship"))


def test_approve_script_double_approve_on_unfinished_board_resumes_again(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """MP4: already-approved + hash current + board INCOMPLETE -> a SECOND 'Script freigeben'
    enqueues another resume (the recovery path after a failed deterministic tail) instead of
    only replying. Replaces the pre-MP4 assumption that any already-approved board was always a
    no-op — that is now scoped to a FINISHED board only (see the complete-board test below)."""
    from laura.short_creator.board import Board
    from laura.short_creator.board_models import Script, content_hash
    from laura.short_creator.production_orchestrator import board_root_for

    calls: list[str] = []

    def _fake_resume(db: Any, session_id: str) -> dict[str, Any]:
        calls.append(session_id)
        return {"session_id": session_id, "job_id": "job-again", "warnings": []}

    monkeypatch.setattr("laura.chat.executor.run_production_resume", _fake_resume)
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)
    asset_id = _seed_board(db, tmp_path, session_id="sess-1")
    board = Board.open(board_root_for(db, asset_id, "sess-1"))
    script = board.load("script")
    assert isinstance(script, Script)
    board.set_script_approved(_NOW, content_hash(script))
    _seed_action(db, conversation_id, session_id="sess-1")

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("approve_script", {"session_ref": "sess-1"}), now_utc=_NOW2,
    )

    assert calls == ["sess-1"], "an incomplete board must resume again, not just reply"
    assert messages[0]["kind"] == "action"
    assert messages[0]["content"]["tool"] == "approve_script"
    assert messages[0]["content"]["refs"] == {"session_id": "sess-1", "job_id": "job-again"}
    # already_current -> the existing (still-current) stamp is left untouched, not re-stamped.
    reloaded = Board.open(board_root_for(db, asset_id, "sess-1"))
    assert reloaded.meta().script_approved_utc == _NOW


def test_approve_script_double_approve_on_complete_board_stays_a_noop(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    from laura.short_creator.board import Board
    from laura.short_creator.board_models import Script, content_hash
    from laura.short_creator.production_orchestrator import board_root_for

    calls: list[str] = []

    def _fake_resume(db: Any, session_id: str) -> dict[str, Any]:
        calls.append(session_id)
        return {"session_id": session_id, "job_id": "job-42", "warnings": []}

    monkeypatch.setattr("laura.chat.executor.run_production_resume", _fake_resume)
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)
    asset_id = _seed_board(db, tmp_path, session_id="sess-1")
    board = Board.open(board_root_for(db, asset_id, "sess-1"))
    _fill_chain(board)  # saves storyline THEN script — see _fill_chain's own docstring on why
    script = board.load("script")
    assert isinstance(script, Script)
    board.set_script_approved(_NOW, content_hash(script))
    _seed_action(db, conversation_id, session_id="sess-1")

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("approve_script", {"session_ref": "sess-1"}), now_utc=_NOW2,
    )

    assert calls == [], "a fully finished board must never start a second run"
    assert messages[0]["kind"] == "text"
    assert messages[0]["content"]["text"] == "Script war schon freigegeben."


def test_approve_script_enqueues_a_pure_resume_without_message(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """The approve job payload must not carry a message key — a message run is a team run
    (MP3's ``deterministic_eligible`` predicate), the whole point of the modular arc. Exercises
    the REAL enqueue (not monkeypatched) so the actual job row's payload can be inspected —
    mirrors ``test_production_api.py``'s ``_autoshort_available`` monkeypatch convention; the
    agent-config preflight passes on its own since the test env sets no ``LAURA_AGENT_*``
    (default: local ollama, always usable)."""
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)
    _seed_board(db, tmp_path, session_id="sess-1")
    _seed_action(db, conversation_id, session_id="sess-1")

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("approve_script", {"session_ref": "sess-1"}), now_utc=_NOW,
    )

    assert messages[0]["kind"] == "action"
    job_id = messages[0]["content"]["refs"]["job_id"]
    job = repos.get_job(db, job_id)
    assert job is not None
    payload = json.loads(job["payload_json"])
    assert "message" not in payload


def test_approve_script_without_a_script_refuses_and_stamps_nothing(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """Finding 2: approving before the team has written any script must refuse outright — no
    approval stamp, no resume run — instead of landing pre-emptively so the gate never
    actually pauses the run."""
    from laura.short_creator.board import Board
    from laura.short_creator.production_orchestrator import board_root_for

    calls: list[str] = []

    def _fake_resume(db: Any, session_id: str) -> dict[str, Any]:
        calls.append(session_id)
        return {"session_id": session_id, "job_id": "job-42", "warnings": []}

    monkeypatch.setattr("laura.chat.executor.run_production_resume", _fake_resume)
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)
    asset_id = _seed_board(db, tmp_path, session_id="sess-1", with_script=False)
    _seed_action(db, conversation_id, session_id="sess-1")

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("approve_script", {"session_ref": "sess-1"}), now_utc=_NOW,
    )

    assert calls == [], "no script on the board -> no resume run must ever start"
    assert len(messages) == 1
    assert messages[0]["kind"] == "text"
    assert messages[0]["content"]["text"] == (
        "Es gibt noch kein Script zum Freigeben — die Produktion hält von selbst am Gate."
    )
    board = Board.open(board_root_for(db, asset_id, "sess-1"))
    assert board.meta().script_approved_utc is None


def test_approve_script_after_a_script_change_is_a_fresh_approval_not_a_no_op(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """Finding 3: an approved board whose script has since changed (a team rewrite via
    budget/capacity feedback) must NOT read as "already approved" — the old stamp no longer
    speaks for the NEW text, so re-approving starts a fresh resume run and stamps the new
    content's hash, exactly like a first-time approval would."""
    from laura.short_creator.board import Board
    from laura.short_creator.board_models import Script, ScriptLine, content_hash
    from laura.short_creator.production_orchestrator import board_root_for

    calls: list[str] = []

    def _fake_resume(db: Any, session_id: str) -> dict[str, Any]:
        calls.append(session_id)
        return {"session_id": session_id, "job_id": "job-99", "warnings": []}

    monkeypatch.setattr("laura.chat.executor.run_production_resume", _fake_resume)
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)
    asset_id = _seed_board(db, tmp_path, session_id="sess-1")
    board = Board.open(board_root_for(db, asset_id, "sess-1"))
    original = board.load("script")
    assert isinstance(original, Script)
    board.set_script_approved(_NOW, content_hash(original))
    rewritten = Script(
        language="German",
        lines=[ScriptLine(chapter=1, scene_number=1, text="Ganz anderer Text jetzt.")],
    )
    board.save("script", rewritten)
    _seed_action(db, conversation_id, session_id="sess-1")

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("approve_script", {"session_ref": "sess-1"}), now_utc=_NOW2,
    )

    assert calls == ["sess-1"], "a changed script must start a fresh resume run"
    assert messages[0]["kind"] == "action"
    assert messages[0]["content"]["tool"] == "approve_script"
    assert messages[0]["content"]["refs"] == {"session_id": "sess-1", "job_id": "job-99"}
    reloaded_meta = Board.open(board_root_for(db, asset_id, "sess-1")).meta()
    assert reloaded_meta.script_approved_utc == _NOW2
    assert reloaded_meta.script_approved_script_hash == content_hash(rewritten)


def test_approve_script_without_any_session_asks(tmp_path: Path) -> None:
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("approve_script", {"session_ref": "sess-1"}), now_utc=_NOW,
    )
    assert messages[0]["kind"] == "text"
    assert "Session" in messages[0]["content"]["text"]


def test_approve_script_http_exception_becomes_honest_text(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    def _fails(db: Any, session_id: str) -> dict[str, Any]:
        raise HTTPException(404, "session not found")

    monkeypatch.setattr("laura.chat.executor.run_production_resume", _fails)
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)
    _seed_board(db, tmp_path, session_id="sess-1")
    _seed_action(db, conversation_id, session_id="sess-1")

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("approve_script", {"session_ref": "sess-1"}), now_utc=_NOW,
    )
    assert messages[0]["kind"] == "text"
    assert messages[0]["content"]["text"] == "session not found"


# --- approve_script busy-run guard (I2, 2026-08-05 final review) -------------------------------
# A double "Script freigeben" while the session's latest production job is still queued/running
# must never enqueue a SECOND concurrent production.run job against the same board files.
# _production_job_busy mirrors api/short_creator.py's run_production_revert guard exactly
# (latest_job_id -> repos.get_job -> status in queued/running).


def _seed_job(db: Database, session_id: str, *, status: str = "queued") -> str:
    """A production.run job attached to *session_id* via latest_job_id, in the given
    status. Mirrors test_production_revert.py's direct-SQL status override — enqueue()
    always inserts 'queued', so a non-'queued' target status is set afterwards."""
    from laura.jobs.runner import enqueue

    job_id = enqueue(db, queue="production", kind="production.run", payload={}, max_attempts=1)
    repos.set_production_session_job(db, session_id, job_id)
    if status != "queued":
        with db.transaction() as conn:
            conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
    return job_id


def test_approve_script_busy_run_replies_and_stamps_nothing_on_fresh_approval(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """I2: a busy latest job on the FRESH-approval path (script exists, never approved yet)
    must reply with the busy text and stamp NOTHING — stamping first would open the gate for
    the still-running job mid-flight even though no new run starts here."""
    from laura.short_creator.board import Board
    from laura.short_creator.production_orchestrator import board_root_for

    calls: list[str] = []

    def _fake_resume(db: Any, session_id: str) -> dict[str, Any]:
        calls.append(session_id)
        return {"session_id": session_id, "job_id": "job-must-not-happen", "warnings": []}

    monkeypatch.setattr("laura.chat.executor.run_production_resume", _fake_resume)
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)
    asset_id = _seed_board(db, tmp_path, session_id="sess-1")
    _seed_job(db, "sess-1", status="queued")
    _seed_action(db, conversation_id, session_id="sess-1")

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("approve_script", {"session_ref": "sess-1"}), now_utc=_NOW,
    )

    assert calls == [], "a busy run must never get a second concurrent resume"
    assert len(messages) == 1
    assert messages[0]["kind"] == "text"
    assert "läuft bereits" in messages[0]["content"]["text"]
    board = Board.open(board_root_for(db, asset_id, "sess-1"))
    assert board.meta().script_approved_utc is None


def test_approve_script_busy_run_on_unfinished_already_current_board_does_not_resume(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """I2: the already-current-but-unfinished recovery path (a normal double-approve while
    the deterministic tail is mid-run) must also refuse a concurrent second resume, leaving
    the existing approval stamp untouched."""
    from laura.short_creator.board import Board
    from laura.short_creator.board_models import Script, content_hash
    from laura.short_creator.production_orchestrator import board_root_for

    calls: list[str] = []

    def _fake_resume(db: Any, session_id: str) -> dict[str, Any]:
        calls.append(session_id)
        return {"session_id": session_id, "job_id": "job-must-not-happen", "warnings": []}

    monkeypatch.setattr("laura.chat.executor.run_production_resume", _fake_resume)
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)
    asset_id = _seed_board(db, tmp_path, session_id="sess-1")
    board = Board.open(board_root_for(db, asset_id, "sess-1"))
    script = board.load("script")
    assert isinstance(script, Script)
    board.set_script_approved(_NOW, content_hash(script))
    _seed_job(db, "sess-1", status="running")
    _seed_action(db, conversation_id, session_id="sess-1")

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("approve_script", {"session_ref": "sess-1"}), now_utc=_NOW2,
    )

    assert calls == [], "a busy run must never get a second concurrent resume"
    assert len(messages) == 1
    assert messages[0]["kind"] == "text"
    assert "läuft bereits" in messages[0]["content"]["text"]
    reloaded = Board.open(board_root_for(db, asset_id, "sess-1"))
    assert reloaded.meta().script_approved_utc == _NOW, "the existing stamp must stay untouched"


def test_approve_script_after_job_succeeded_runs_normally(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """I2 positive control: a TERMINAL latest job (succeeded) must not trip the busy guard —
    only queued/running blocks a new resume."""
    from laura.short_creator.board import Board
    from laura.short_creator.production_orchestrator import board_root_for

    def _fake_resume(db: Any, session_id: str) -> dict[str, Any]:
        return {"session_id": session_id, "job_id": "job-42", "warnings": []}

    monkeypatch.setattr("laura.chat.executor.run_production_resume", _fake_resume)
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)
    asset_id = _seed_board(db, tmp_path, session_id="sess-1")
    _seed_job(db, "sess-1", status="succeeded")
    _seed_action(db, conversation_id, session_id="sess-1")

    messages = execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("approve_script", {"session_ref": "sess-1"}), now_utc=_NOW,
    )

    assert messages[0]["kind"] == "action"
    assert messages[0]["content"]["refs"]["job_id"] == "job-42"
    board = Board.open(board_root_for(db, asset_id, "sess-1"))
    assert board.meta().script_approved_utc == _NOW


# --- discuss (FE2: grounded discuss handler with injectable one-shot runner) -------------------


def _seed_discuss_session(
    db: Database, tmp_path: Path, *, session_id: str = "sess-1",
) -> str:
    """Project (active) + conversation + an asset with a transcript segment mentioning
    'Konfix' + a production session/board carrying a one-line script — everything
    ``_discuss_context`` can ground an answer on. Mirrors ``_seed_board``/``_seed_transcript``'s
    own seeding style (kept local per this file's self-contained-test-file convention).
    Returns the conversation id; the session is referenced via an action card, same as every
    other session-resolving handler's tests in this file."""
    from laura.short_creator.board import Board
    from laura.short_creator.board_models import BoardMeta, Script, ScriptLine
    from laura.short_creator.production_orchestrator import board_root_for

    project = _project(db, tmp_path, name="Discuss-Test")
    conversation_id = _conversation(db, project_id=project["id"])
    asset, _seg_ids = _seed_transcript(
        db, project["id"], display_name="Clip 1",
        texts=["intro satz", "Du kannst Konfix aktuell halten, uns weiter und sofort."],
    )
    repos.create_production_session(
        db, session_id=session_id, asset_id=asset["id"], created_utc=_NOW,
    )
    root = board_root_for(db, asset["id"], session_id)
    meta = BoardMeta(
        session_id=session_id, asset_id=asset["id"], created_utc=_NOW,
        task="t", target_seconds=30.0, script_gate=True,
    )
    board = Board.create(root, meta)
    board.save(
        "script",
        Script(
            language="German",
            lines=[ScriptLine(chapter=1, scene_number=1, text="Hallo Welt, schau mal her.")],
        ),
    )
    _seed_action(db, conversation_id, session_id=session_id)
    return conversation_id


def _run_discuss(
    db: Database, settings: Settings, conversation_id: str, text: str, discuss_runner: Any,
) -> list[dict[str, Any]]:
    return execute_decision(
        db, settings, conversation_id=conversation_id,
        decision=_decision("discuss", {"text": text}), now_utc=_NOW,
        discuss_runner=discuss_runner,
    )


def test_discuss_answers_via_injected_runner_with_grounded_context(tmp_path: Path) -> None:
    db, settings = _setup(tmp_path)
    conversation_id = _seed_discuss_session(db, tmp_path)
    captured: list[str] = []

    def runner(task: str) -> str:
        captured.append(task)
        return (
            "Das ist die rohe Whisper-Transkription.\n"
            "Vorschlag: ersetze in Segment 2 'Konfix' durch 'Configs'\n"
            "Antworte 'ja', dann setze ich das um — oder beschreib es anders."
        )

    messages = _run_discuss(
        db, settings, conversation_id, "warum steht Konfix aktuell im transkript?", runner,
    )

    assert len(messages) == 1
    msg = messages[0]
    assert msg["kind"] == "text" and "Vorschlag:" in msg["content"]["text"]
    task = captured[0]
    assert "Konfix" in task  # the transcript hit made it into the context
    assert "Segment" in task, "a real bigram hit against the seeded segment, not just an echo"
    assert "resume_point" in task or "Status" in task  # board summary present


def test_discuss_matches_segments_by_bigram_and_by_explicit_number() -> None:
    from laura.chat.executor import _matching_segments

    segments = [
        {"index": 86, "text": "Dele herzunehmen, also welche Modelle du haben willst."},
        {"index": 87, "text": "Du kannst Konfix aktuell halten, uns weiter und sofort."},
        {"index": 88, "text": "Was haben wir noch?"},
    ]
    hits = _matching_segments("warum steht Konfix aktuell da drin", segments)
    assert [h["index"] for h in hits] == [87]
    hits = _matching_segments("Segment 88 macht keinen Sinn", segments)
    assert [h["index"] for h in hits] == [88]
    assert _matching_segments("völlig anderes thema", segments) == []


def test_discuss_without_session_still_answers(tmp_path: Path) -> None:
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)

    messages = _run_discuss(
        db, settings, conversation_id, "was kannst du eigentlich?",
        lambda task: "Ich baue Shorts aus deinen Videos.",
    )

    assert len(messages) == 1
    assert messages[0]["content"]["text"].startswith("Ich baue Shorts")


def test_discuss_runner_failure_falls_back_deterministically(tmp_path: Path) -> None:
    db, settings = _setup(tmp_path)
    conversation_id = _seed_discuss_session(db, tmp_path)

    def broken(task: str) -> str:
        raise TimeoutError("model down")

    messages = _run_discuss(db, settings, conversation_id, "hm?", broken)

    assert len(messages) == 1
    assert "nichts Fundiertes" in messages[0]["content"]["text"]


def test_discuss_empty_runner_reply_falls_back(tmp_path: Path) -> None:
    db, settings = _setup(tmp_path)
    conversation_id = _seed_discuss_session(db, tmp_path)

    messages = _run_discuss(db, settings, conversation_id, "hm?", lambda t: "   ")

    assert len(messages) == 1
    assert "nichts Fundiertes" in messages[0]["content"]["text"]


# --- defensive: unknown tool + never-raises ---------------------------------------------------


def test_unknown_tool_is_defensive_text_not_a_crash(tmp_path: Path) -> None:
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)
    bogus: RouterDecision = {"tool": "explode", "args": {}, "fallback": False}

    messages = execute_decision(
        db, settings, conversation_id=conversation_id, decision=bogus, now_utc=_NOW,
    )
    assert messages[0]["kind"] == "text"
    assert messages[0]["content"]["text"]


def test_missing_required_arg_never_raises_out_of_execute_decision(tmp_path: Path) -> None:
    db, settings = _setup(tmp_path)
    conversation_id = _conversation(db)
    # "name" missing — a decision the router should never hand out, but execute_decision must
    # still never raise.
    broken: RouterDecision = {"tool": "create_project", "args": {}, "fallback": False}

    messages = execute_decision(
        db, settings, conversation_id=conversation_id, decision=broken, now_utc=_NOW,
    )
    assert messages[0]["kind"] == "text"
    assert messages[0]["content"]["text"]
    assert _conversation_row(db, conversation_id)["active_project_id"] is None


# --- execute_import_approval: the approval trio -----------------------------------------------


def _fake_enqueue_url_fetch(
    db: Any, project_id: str, url: str, *, display_name: Any, fmt: Any, cookies_from_browser: Any,
) -> tuple[str, str]:
    return f"asset-{url[-1]}", f"job-{url[-1]}"


def _no_playlist_expansion(source_url: str, cookies_from_browser: Any) -> list[str] | None:
    """Hermetic stand-in for ``_expand_playlist_urls``: with the ``[fetch]`` extra installed,
    the real one would probe the network even for these fake URLs."""
    return None


def _seed_pending_approval(
    db: Database, conversation_id: str, *, project_id: str, urls: list[str] | None = None,
) -> str:
    message_id = "approval-1"
    repos.append_conversation_message(
        db, message_id=message_id, conversation_id=conversation_id, role="assistant",
        kind="approval_request",
        content={
            "action_type": "import_urls",
            "payload": {
                "urls": urls if urls is not None else ["https://x/a", "https://x/b"],
                "project_id": project_id,
            },
            "status": "pending",
            "decided_at": None,
            "result": None,
        },
        created_utc=_NOW,
    )
    return message_id


def test_execute_import_approval_executes_and_appends(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("laura.chat.executor._enqueue_url_fetch", _fake_enqueue_url_fetch)
    monkeypatch.setattr("laura.chat.executor._expand_playlist_urls", _no_playlist_expansion)
    db, _settings = _setup(tmp_path)
    project = _project(db, tmp_path)
    conversation_id = _conversation(db, project_id=project["id"])
    message_id = _seed_pending_approval(db, conversation_id, project_id=project["id"])

    result = execute_import_approval(db, message_id=message_id, now_utc=_NOW2)

    assert len(result) == 2
    card, action = result
    assert card["content"]["status"] == "executed"
    assert card["content"]["decided_at"] == _NOW2
    assert card["content"]["result"] == {"asset_ids": ["asset-a", "asset-b"]}
    assert action["kind"] == "action"
    assert action["content"]["tool"] == "import_urls"
    assert action["content"]["outcome"] == "running"
    assert action["content"]["refs"] == {
        "asset_ids": ["asset-a", "asset-b"], "job_ids": ["job-a", "job-b"],
    }

    stored = repos.get_conversation_message(db, message_id)
    assert stored is not None and stored["content"]["status"] == "executed"
    thread = repos.list_conversation_messages(db, conversation_id)
    assert [m["kind"] for m in thread] == ["approval_request", "action"]


def test_execute_import_approval_expands_playlists_like_the_import_endpoint(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """A playlist/channel URL approved via chat must fan out into one asset + fetch job per
    entry — the same expansion the HTTP import lane runs (assets.import_asset). A URL whose
    expansion returns None keeps importing as a single asset."""
    enqueued_urls: list[str] = []

    def _tracking_enqueue(
        db: Any, project_id: str, url: str, *, display_name: Any, fmt: Any,
        cookies_from_browser: Any,
    ) -> tuple[str, str]:
        enqueued_urls.append(url)
        return f"asset-{len(enqueued_urls)}", f"job-{len(enqueued_urls)}"

    def _fake_expand(source_url: str, cookies_from_browser: Any) -> list[str] | None:
        assert cookies_from_browser is None, "chat approvals carry no browser cookies"
        if source_url == "https://x/list":
            return ["https://x/v1", "https://x/v2"]
        return None

    monkeypatch.setattr("laura.chat.executor._enqueue_url_fetch", _tracking_enqueue)
    monkeypatch.setattr("laura.chat.executor._expand_playlist_urls", _fake_expand)
    db, _settings = _setup(tmp_path)
    project = _project(db, tmp_path)
    conversation_id = _conversation(db, project_id=project["id"])
    message_id = _seed_pending_approval(
        db, conversation_id, project_id=project["id"],
        urls=["https://x/list", "https://x/b"],
    )

    result = execute_import_approval(db, message_id=message_id, now_utc=_NOW2)

    assert enqueued_urls == ["https://x/v1", "https://x/v2", "https://x/b"]
    card, action = result
    assert card["content"]["status"] == "executed"
    assert card["content"]["result"] == {"asset_ids": ["asset-1", "asset-2", "asset-3"]}
    # args records what the user actually approved; refs carry the full fan-out.
    assert action["content"]["args"] == {"urls": ["https://x/list", "https://x/b"]}
    assert action["content"]["refs"] == {
        "asset_ids": ["asset-1", "asset-2", "asset-3"],
        "job_ids": ["job-1", "job-2", "job-3"],
    }


def test_execute_import_approval_unknown_message_404(tmp_path: Path) -> None:
    db, _settings = _setup(tmp_path)

    with pytest.raises(HTTPException) as excinfo:
        execute_import_approval(db, message_id="nope", now_utc=_NOW)
    assert excinfo.value.status_code == 404


def test_execute_import_approval_after_reject_conflicts(tmp_path: Path, monkeypatch: Any) -> None:
    """Reject is a plain repos content flip (Task 6's job) — this pins that once a card is no
    longer "pending", execute_import_approval refuses to execute it."""
    monkeypatch.setattr("laura.chat.executor._enqueue_url_fetch", _fake_enqueue_url_fetch)
    db, _settings = _setup(tmp_path)
    project = _project(db, tmp_path)
    conversation_id = _conversation(db, project_id=project["id"])
    message_id = _seed_pending_approval(db, conversation_id, project_id=project["id"])

    message = repos.get_conversation_message(db, message_id)
    assert message is not None
    repos.update_conversation_message_content(
        db, message_id, {**message["content"], "status": "rejected", "decided_at": _NOW}
    )

    with pytest.raises(HTTPException) as excinfo:
        execute_import_approval(db, message_id=message_id, now_utc=_NOW2)
    assert excinfo.value.status_code == 409

    # Rejecting must not have appended anything beyond the original card.
    thread = repos.list_conversation_messages(db, conversation_id)
    assert [m["kind"] for m in thread] == ["approval_request"]
    assert thread[0]["content"]["status"] == "rejected"


def test_execute_import_approval_deleted_project_409s_and_leaves_card_pending(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """The project can be deleted between the approval card's creation and the user clicking
    "Freigeben" — this must 409 BEFORE flipping the card, leaving it re-decidable, and must
    never reach the enqueue machinery (no FK-failure 500 with an "approved" corpse card)."""
    enqueue_calls: list[str] = []

    def _tracking_enqueue(
        db: Any, project_id: str, url: str, *, display_name: Any, fmt: Any,
        cookies_from_browser: Any,
    ) -> tuple[str, str]:
        enqueue_calls.append(url)
        return _fake_enqueue_url_fetch(
            db, project_id, url, display_name=display_name, fmt=fmt,
            cookies_from_browser=cookies_from_browser,
        )

    monkeypatch.setattr("laura.chat.executor._enqueue_url_fetch", _tracking_enqueue)
    db, _settings = _setup(tmp_path)
    project = _project(db, tmp_path)
    conversation_id = _conversation(db, project_id=project["id"])
    message_id = _seed_pending_approval(db, conversation_id, project_id=project["id"])

    assert repos.delete_project(db, project["id"]) is True

    with pytest.raises(HTTPException) as excinfo:
        execute_import_approval(db, message_id=message_id, now_utc=_NOW2)
    assert excinfo.value.status_code == 409
    assert "reason" in excinfo.value.detail

    stored = repos.get_conversation_message(db, message_id)
    assert stored is not None and stored["content"]["status"] == "pending"
    assert enqueue_calls == [], "a deleted project must never reach the enqueue machinery"

    # No stray "action" message was appended either — the thread is untouched.
    thread = repos.list_conversation_messages(db, conversation_id)
    assert [m["kind"] for m in thread] == ["approval_request"]


def test_execute_import_approval_second_decide_conflicts(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    monkeypatch.setattr("laura.chat.executor._enqueue_url_fetch", _fake_enqueue_url_fetch)
    monkeypatch.setattr("laura.chat.executor._expand_playlist_urls", _no_playlist_expansion)
    db, _settings = _setup(tmp_path)
    project = _project(db, tmp_path)
    conversation_id = _conversation(db, project_id=project["id"])
    message_id = _seed_pending_approval(db, conversation_id, project_id=project["id"])

    execute_import_approval(db, message_id=message_id, now_utc=_NOW)

    with pytest.raises(HTTPException) as excinfo:
        execute_import_approval(db, message_id=message_id, now_utc=_NOW2)
    assert excinfo.value.status_code == 409


# --- Transcript confirmation warnings (Task 6) ---


def test_run_project_auto_short_warns_when_asset_transcript_unconfirmed(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """Warn when auto-short asset has unconfirmed transcript."""
    from laura.api.short_creator import run_project_auto_short
    from laura.short_creator.scout import ScoutDecision

    db, settings = _setup(tmp_path)
    project = _project(db, tmp_path)

    # Create a temporary source file.
    source_file = tmp_path / "test.mp4"
    source_file.write_bytes(b"fake video")

    # Create an asset without transcript_confirmed_at (defaults to NULL).
    asset = repos.create_asset(
        db, project_id=project["id"], display_name="Test Video",
        source_path=str(source_file), type="video",
    )

    # Mock search_material to return this asset in the ranking.
    def _mock_search_material(
        db_param: Any, project_id: str, topic: str,
    ) -> dict[str, Any]:
        return {
            "ranking": [
                {
                    "asset_id": asset["id"],
                    "display_name": asset["display_name"],
                    "score": 1.0,
                    "scene_hits": [
                        {"snippet": "test snippet", "scene_number": 1, "start_frame": 0}
                    ],
                }
            ],
            "skipped": [],
            "source": "lexical",
        }

    def _mock_run_scout(
        db_param: Any, config: Any, *, project_id: str, topic: str, material: Any,
    ) -> ScoutDecision:
        return {
            "asset_id": asset["id"],
            "scene_numbers": [1],
            "rationale": "test rationale",
            "fallback": False,
        }

    monkeypatch.setattr("laura.api.short_creator.search_material", _mock_search_material)
    monkeypatch.setattr("laura.api.short_creator.run_scout", _mock_run_scout)
    monkeypatch.setattr("laura.api.short_creator._require_autoshort", lambda: None)
    monkeypatch.setattr("laura.api.short_creator._require_usable_agent_config", lambda: None)

    result = run_project_auto_short(
        db, project["id"],
        topic="test", target_seconds=60, format="insta", language="German",
    )

    assert "Transkript unbestätigt: Test Video" in result["warnings"]


def test_run_project_auto_short_no_warning_when_transcript_confirmed(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """No warning when auto-short asset has confirmed transcript."""
    from laura.api.short_creator import run_project_auto_short
    from laura.short_creator.scout import ScoutDecision

    db, settings = _setup(tmp_path)
    project = _project(db, tmp_path)

    # Create a temporary source file.
    source_file = tmp_path / "test.mp4"
    source_file.write_bytes(b"fake video")

    # Create an asset and mark transcript as confirmed.
    asset = repos.create_asset(
        db, project_id=project["id"], display_name="Test Video",
        source_path=str(source_file), type="video",
    )
    repos.set_transcript_confirmed_at(db, asset["id"], "2026-08-05T00:00:00Z")

    # Mock search_material to return this asset.
    def _mock_search_material(
        db_param: Any, project_id: str, topic: str,
    ) -> dict[str, Any]:
        return {
            "ranking": [
                {
                    "asset_id": asset["id"],
                    "display_name": asset["display_name"],
                    "score": 1.0,
                    "scene_hits": [
                        {"snippet": "test snippet", "scene_number": 1, "start_frame": 0}
                    ],
                }
            ],
            "skipped": [],
            "source": "lexical",
        }

    def _mock_run_scout(
        db_param: Any, config: Any, *, project_id: str, topic: str, material: Any,
    ) -> ScoutDecision:
        return {
            "asset_id": asset["id"],
            "scene_numbers": [1],
            "rationale": "test rationale",
            "fallback": False,
        }

    monkeypatch.setattr("laura.api.short_creator.search_material", _mock_search_material)
    monkeypatch.setattr("laura.api.short_creator.run_scout", _mock_run_scout)
    monkeypatch.setattr("laura.api.short_creator._require_autoshort", lambda: None)
    monkeypatch.setattr("laura.api.short_creator._require_usable_agent_config", lambda: None)

    result = run_project_auto_short(
        db, project["id"],
        topic="test", target_seconds=60, format="insta", language="German",
    )

    assert not any(
        "Transkript unbestätigt" in w for w in result["warnings"]
    ), "should not warn when transcript is confirmed"


def test_run_project_auto_overview_warns_when_asset_transcript_unconfirmed(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """Warn when auto-overview asset has unconfirmed transcript."""
    from laura.api.short_creator import run_project_auto_overview
    from laura.short_creator.overview_scout import OverviewDecision
    from laura.short_creator.overview_windows import Candidate

    db, settings = _setup(tmp_path)
    project = _project(db, tmp_path)

    # Create an asset without transcript_confirmed_at.
    asset = repos.create_asset(
        db, project_id=project["id"], display_name="Test Video",
        source_path="/tmp/test.mp4", type="video",
    )

    # Mock search_material.
    def _mock_search_material(
        db_param: Any, project_id: str, topic: str,
    ) -> dict[str, Any]:
        return {
            "ranking": [
                {
                    "asset_id": asset["id"],
                    "display_name": asset["display_name"],
                    "score": 1.0,
                    "scene_hits": [
                        {"snippet": "test snippet", "scene_number": 1, "start_frame": 0}
                    ],
                }
            ],
            "skipped": [],
            "source": "lexical",
        }

    def _mock_run_overview_scout(
        config: Any, *, topic: str, candidates: Any, target_seconds: int, fps_by_asset: Any,
    ) -> OverviewDecision:
        # Return a clip from the asset.
        clip = Candidate(
            asset_id=asset["id"],
            display_name=asset["display_name"],
            scene_number=1,
            start_frame=0,
            end_frame_exclusive=100,
            snippet="test snippet",
        )
        return {
            "clips": [clip],
            "rationale": "test rationale",
            "fallback": False,
        }

    def _mock_split_by_source_presence(
        db_param: Any, ranking: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        return ranking, []

    def _mock_build_candidates(
        ranking: list[dict[str, Any]], scene_bounds: Any, fps_by_asset: Any,
    ) -> list[Candidate]:
        # Return a simple candidate for the test.
        return [
            Candidate(
                asset_id=asset["id"],
                display_name=asset["display_name"],
                scene_number=1,
                start_frame=0,
                end_frame_exclusive=100,
                snippet="test snippet",
            )
        ]

    monkeypatch.setattr("laura.api.short_creator.search_material", _mock_search_material)
    monkeypatch.setattr("laura.api.short_creator.run_overview_scout", _mock_run_overview_scout)
    monkeypatch.setattr(
        "laura.api.short_creator._split_by_source_presence", _mock_split_by_source_presence
    )
    monkeypatch.setattr("laura.api.short_creator.build_candidates", _mock_build_candidates)
    monkeypatch.setattr("laura.api.short_creator._require_autoshort", lambda: None)
    monkeypatch.setattr("laura.api.short_creator._require_usable_agent_config", lambda: None)

    result = run_project_auto_overview(
        db, project["id"],
        topic="test", target_seconds=60, language="German",
    )

    assert "Transkript unbestätigt: Test Video" in result["warnings"]


def test_run_project_auto_overview_no_warning_when_transcript_confirmed(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """No warning when auto-overview asset has confirmed transcript."""
    from laura.api.short_creator import run_project_auto_overview
    from laura.short_creator.overview_scout import OverviewDecision
    from laura.short_creator.overview_windows import Candidate

    db, settings = _setup(tmp_path)
    project = _project(db, tmp_path)

    # Create a temporary source file.
    source_file = tmp_path / "test.mp4"
    source_file.write_bytes(b"fake video")

    # Create an asset and mark transcript as confirmed.
    asset = repos.create_asset(
        db, project_id=project["id"], display_name="Test Video",
        source_path=str(source_file), type="video",
    )
    repos.set_transcript_confirmed_at(db, asset["id"], "2026-08-05T00:00:00Z")

    # Mock search_material.
    def _mock_search_material(
        db_param: Any, project_id: str, topic: str,
    ) -> dict[str, Any]:
        return {
            "ranking": [
                {
                    "asset_id": asset["id"],
                    "display_name": asset["display_name"],
                    "score": 1.0,
                    "scene_hits": [
                        {"snippet": "test snippet", "scene_number": 1, "start_frame": 0}
                    ],
                }
            ],
            "skipped": [],
            "source": "lexical",
        }

    def _mock_run_overview_scout(
        config: Any, *, topic: str, candidates: Any, target_seconds: int, fps_by_asset: Any,
    ) -> OverviewDecision:
        # Return a clip from the asset.
        clip = Candidate(
            asset_id=asset["id"],
            display_name=asset["display_name"],
            scene_number=1,
            start_frame=0,
            end_frame_exclusive=100,
            snippet="test snippet",
        )
        return {
            "clips": [clip],
            "rationale": "test rationale",
            "fallback": False,
        }

    def _mock_split_by_source_presence(
        db_param: Any, ranking: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        return ranking, []

    def _mock_build_candidates(
        ranking: list[dict[str, Any]], scene_bounds: Any, fps_by_asset: Any,
    ) -> list[Candidate]:
        # Return a simple candidate for the test.
        return [
            Candidate(
                asset_id=asset["id"],
                display_name=asset["display_name"],
                scene_number=1,
                start_frame=0,
                end_frame_exclusive=100,
                snippet="test snippet",
            )
        ]

    monkeypatch.setattr("laura.api.short_creator.search_material", _mock_search_material)
    monkeypatch.setattr("laura.api.short_creator.run_overview_scout", _mock_run_overview_scout)
    monkeypatch.setattr(
        "laura.api.short_creator._split_by_source_presence", _mock_split_by_source_presence
    )
    monkeypatch.setattr("laura.api.short_creator.build_candidates", _mock_build_candidates)
    monkeypatch.setattr("laura.api.short_creator._require_autoshort", lambda: None)
    monkeypatch.setattr("laura.api.short_creator._require_usable_agent_config", lambda: None)

    result = run_project_auto_overview(
        db, project["id"],
        topic="test", target_seconds=60, language="German",
    )

    assert not any(
        "Transkript unbestätigt" in w for w in result["warnings"]
    ), "should not warn when transcript is confirmed"
