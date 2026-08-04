"""Chat executor: a validated router decision -> conversation messages + machinery calls
(spec 2026-08-03-chat-first).

``execute_decision`` must never raise — every failure becomes an assistant ``text`` message.
The Task-2 service functions are monkeypatched by NAME on ``laura.chat.executor`` (imported
into the module so patching works), so these tests never touch a real agent team or a real
production board.
"""

from __future__ import annotations

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


def test_follow_up_falls_back_to_newest_action_when_ref_unresolved(
    tmp_path: Path, monkeypatch: Any,
) -> None:
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
        decision=_decision("follow_up", {"session_ref": "last", "text": "mach lauter"}),
        now_utc=_NOW2,
    )

    assert calls == ["sess-2"], "newest action wins when the ref does not exactly match"


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


def _seed_pending_approval(db: Database, conversation_id: str, *, project_id: str) -> str:
    message_id = "approval-1"
    repos.append_conversation_message(
        db, message_id=message_id, conversation_id=conversation_id, role="assistant",
        kind="approval_request",
        content={
            "action_type": "import_urls",
            "payload": {"urls": ["https://x/a", "https://x/b"], "project_id": project_id},
            "status": "pending",
            "decided_at": None,
            "result": None,
        },
        created_utc=_NOW,
    )
    return message_id


def test_execute_import_approval_executes_and_appends(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("laura.chat.executor._enqueue_url_fetch", _fake_enqueue_url_fetch)
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
    db, _settings = _setup(tmp_path)
    project = _project(db, tmp_path)
    conversation_id = _conversation(db, project_id=project["id"])
    message_id = _seed_pending_approval(db, conversation_id, project_id=project["id"])

    execute_import_approval(db, message_id=message_id, now_utc=_NOW)

    with pytest.raises(HTTPException) as excinfo:
        execute_import_approval(db, message_id=message_id, now_utc=_NOW2)
    assert excinfo.value.status_code == 409
