"""Conversations API: the endpoint layer where the router, executor, and Task-1 repos all meet
for the first time (spec 2026-08-03-chat-first).

``app.state.chat_runner`` is the injectable test seam ``run_router`` consumes — every test here
sets it to a fake ``Callable[[str], str]`` so no test ever touches a real agent/LLM.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.main import create_app

_TOKEN = "test-token"
_H = {"X-Laura-Token": _TOKEN}


def _app(tmp_path: Path) -> tuple[TestClient, Any, Settings]:
    settings = Settings(workspace_root=tmp_path / "ws", token=_TOKEN, start_runner=False)
    app = create_app(settings)
    return TestClient(app), app.state.db, settings


def _project(db: Any, tmp_path: Path, *, name: str = "Drive-Test") -> dict[str, Any]:
    return repos.create_project(
        db, name=name, rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(tmp_path / f"ws-{name}"),
    )


def _reply_runner(text: str) -> Any:
    return lambda _task: json.dumps({"tool": "reply", "args": {"text": text}})


# --- CRUD --------------------------------------------------------------------------------------


def test_crud_roundtrip(tmp_path: Path) -> None:
    client, _db, _settings = _app(tmp_path)

    created = client.post("/conversations", headers=_H)
    assert created.status_code in (200, 202)
    conversation_id = created.json()["id"]
    assert conversation_id

    listed = client.get("/conversations", headers=_H)
    assert listed.status_code == 200
    assert any(c["id"] == conversation_id for c in listed.json())

    got = client.get(f"/conversations/{conversation_id}", headers=_H)
    assert got.status_code == 200
    body = got.json()
    assert body["id"] == conversation_id
    assert body["title"] == ""
    assert body["active_project_id"] is None
    assert body["messages"] == []

    deleted = client.delete(f"/conversations/{conversation_id}", headers=_H)
    assert deleted.status_code in (200, 204)

    assert client.get(f"/conversations/{conversation_id}", headers=_H).status_code == 404


def test_get_unknown_conversation_404(tmp_path: Path) -> None:
    client, _db, _settings = _app(tmp_path)
    assert client.get("/conversations/nope", headers=_H).status_code == 404


def test_delete_unknown_conversation_404(tmp_path: Path) -> None:
    client, _db, _settings = _app(tmp_path)
    assert client.delete("/conversations/nope", headers=_H).status_code == 404


def test_get_returns_messages_in_seq_order(tmp_path: Path) -> None:
    client, db, _settings = _app(tmp_path)
    created = client.post("/conversations", headers=_H).json()
    conversation_id = created["id"]
    client.app.state.chat_runner = _reply_runner("erste Antwort")  # type: ignore[attr-defined]

    client.post(
        f"/conversations/{conversation_id}/message", json={"text": "hallo"}, headers=_H,
    )
    client.post(
        f"/conversations/{conversation_id}/message", json={"text": "und jetzt?"}, headers=_H,
    )

    got = client.get(f"/conversations/{conversation_id}", headers=_H).json()
    seqs = [m["seq"] for m in got["messages"]]
    assert seqs == sorted(seqs)
    assert len(got["messages"]) == 4  # 2x (user + assistant reply)


# --- message turn --------------------------------------------------------------------------


def test_message_turn_with_reply_runner_sets_title_and_returns_messages(
    tmp_path: Path,
) -> None:
    client, db, _settings = _app(tmp_path)
    conversation_id = client.post("/conversations", headers=_H).json()["id"]
    client.app.state.chat_runner = _reply_runner("Hallo zurück!")  # type: ignore[attr-defined]

    text = "x" * 80  # longer than 60 chars so the title-truncation is actually exercised
    resp = client.post(
        f"/conversations/{conversation_id}/message", json={"text": text}, headers=_H,
    )
    assert resp.status_code == 202
    body = resp.json()
    assert len(body["messages"]) == 2
    user_msg, assistant_msg = body["messages"]
    assert user_msg["role"] == "user" and user_msg["kind"] == "text"
    assert user_msg["content"] == {"text": text}
    assert assistant_msg["role"] == "assistant" and assistant_msg["kind"] == "text"
    assert assistant_msg["content"] == {"text": "Hallo zurück!"}

    conversation = repos.get_conversation(db, conversation_id)
    assert conversation is not None
    assert conversation["title"] == text[:60]


def test_message_turn_does_not_overwrite_existing_title(tmp_path: Path) -> None:
    client, db, _settings = _app(tmp_path)
    conversation_id = client.post("/conversations", headers=_H).json()["id"]
    repos.set_conversation_title(db, conversation_id, "Mein Titel")
    client.app.state.chat_runner = _reply_runner("ok")  # type: ignore[attr-defined]

    client.post(
        f"/conversations/{conversation_id}/message", json={"text": "zweite Nachricht"},
        headers=_H,
    )

    conversation = repos.get_conversation(db, conversation_id)
    assert conversation is not None
    assert conversation["title"] == "Mein Titel"


def test_message_turn_unknown_conversation_404(tmp_path: Path) -> None:
    client, _db, _settings = _app(tmp_path)
    client.app.state.chat_runner = _reply_runner("ok")  # type: ignore[attr-defined]
    resp = client.post(
        "/conversations/nope/message", json={"text": "hallo"}, headers=_H,
    )
    assert resp.status_code == 404


def test_message_turn_rejects_empty_text(tmp_path: Path) -> None:
    client, _db, _settings = _app(tmp_path)
    conversation_id = client.post("/conversations", headers=_H).json()["id"]
    resp = client.post(
        f"/conversations/{conversation_id}/message", json={"text": ""}, headers=_H,
    )
    assert resp.status_code == 422


def test_message_turn_propose_import_returns_pending_approval_card(
    tmp_path: Path,
) -> None:
    client, db, _settings = _app(tmp_path)
    project = _project(db, tmp_path)
    conversation_id = client.post("/conversations", headers=_H).json()["id"]
    repos.set_conversation_project(db, conversation_id, project["id"])

    reply = json.dumps(
        {"tool": "propose_import", "args": {"urls": ["https://x/a.mp4"]}}
    )
    client.app.state.chat_runner = lambda _task: reply  # type: ignore[attr-defined]

    resp = client.post(
        f"/conversations/{conversation_id}/message",
        json={"text": "importier mir https://x/a.mp4"},
        headers=_H,
    )
    assert resp.status_code == 202
    body = resp.json()
    assert len(body["messages"]) == 2
    card = body["messages"][1]
    assert card["kind"] == "approval_request"
    assert card["content"]["status"] == "pending"
    assert card["content"]["payload"]["urls"] == ["https://x/a.mp4"]
    assert card["id"]


# --- approvals -----------------------------------------------------------------------------


def _seed_pending_card(client: TestClient, db: Any, tmp_path: Path) -> tuple[str, str]:
    project = _project(db, tmp_path)
    conversation_id = client.post("/conversations", headers=_H).json()["id"]
    repos.set_conversation_project(db, conversation_id, project["id"])
    reply = json.dumps({"tool": "propose_import", "args": {"urls": ["https://x/a.mp4"]}})
    client.app.state.chat_runner = lambda _task: reply  # type: ignore[attr-defined]
    resp = client.post(
        f"/conversations/{conversation_id}/message",
        json={"text": "importier https://x/a.mp4"},
        headers=_H,
    )
    card = resp.json()["messages"][1]
    return conversation_id, card["id"]


def test_approve_executes_and_appends_action(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        "laura.chat.executor._enqueue_url_fetch",
        lambda db, project_id, url, *, display_name, fmt, cookies_from_browser: ("a1", "j1"),
    )
    client, db, _settings = _app(tmp_path)
    conversation_id, message_id = _seed_pending_card(client, db, tmp_path)

    resp = client.post(
        f"/conversations/{conversation_id}/approvals/{message_id}",
        json={"decision": "approve"},
        headers=_H,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["messages"]) == 2
    card, action = body["messages"]
    assert card["content"]["status"] == "executed"
    assert card["content"]["result"] == {"asset_ids": ["a1"]}
    assert action["kind"] == "action"
    assert action["content"]["tool"] == "import_urls"
    assert action["content"]["refs"] == {"asset_ids": ["a1"], "job_ids": ["j1"]}


def test_reject_flips_status_and_appends_nothing(tmp_path: Path) -> None:
    client, db, _settings = _app(tmp_path)
    conversation_id, message_id = _seed_pending_card(client, db, tmp_path)

    resp = client.post(
        f"/conversations/{conversation_id}/approvals/{message_id}",
        json={"decision": "reject"},
        headers=_H,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["messages"]) == 1
    assert body["messages"][0]["content"]["status"] == "rejected"
    assert body["messages"][0]["content"]["decided_at"] is not None

    thread = repos.list_conversation_messages(db, conversation_id)
    # rejecting flips the SAME card in place — no new message, no "action" card. The thread
    # is [user text, approval_request] from the seeded message turn; reject appends nothing.
    assert [m["kind"] for m in thread] == ["text", "approval_request"]
    assert thread[-1]["content"]["status"] == "rejected"


def test_approve_twice_conflicts(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "laura.chat.executor._enqueue_url_fetch",
        lambda db, project_id, url, *, display_name, fmt, cookies_from_browser: ("a1", "j1"),
    )
    client, db, _settings = _app(tmp_path)
    conversation_id, message_id = _seed_pending_card(client, db, tmp_path)

    first = client.post(
        f"/conversations/{conversation_id}/approvals/{message_id}",
        json={"decision": "approve"},
        headers=_H,
    )
    assert first.status_code == 200

    second = client.post(
        f"/conversations/{conversation_id}/approvals/{message_id}",
        json={"decision": "approve"},
        headers=_H,
    )
    assert second.status_code == 409


def test_reject_after_approve_conflicts(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "laura.chat.executor._enqueue_url_fetch",
        lambda db, project_id, url, *, display_name, fmt, cookies_from_browser: ("a1", "j1"),
    )
    client, db, _settings = _app(tmp_path)
    conversation_id, message_id = _seed_pending_card(client, db, tmp_path)

    approve = client.post(
        f"/conversations/{conversation_id}/approvals/{message_id}",
        json={"decision": "approve"},
        headers=_H,
    )
    assert approve.status_code == 200

    reject = client.post(
        f"/conversations/{conversation_id}/approvals/{message_id}",
        json={"decision": "reject"},
        headers=_H,
    )
    assert reject.status_code == 409


def test_approval_unknown_message_404(tmp_path: Path) -> None:
    client, _db, _settings = _app(tmp_path)
    conversation_id = client.post("/conversations", headers=_H).json()["id"]
    resp = client.post(
        f"/conversations/{conversation_id}/approvals/nope",
        json={"decision": "approve"},
        headers=_H,
    )
    assert resp.status_code == 404


def test_approval_message_from_different_conversation_404(
    tmp_path: Path,
) -> None:
    """Approving a message from conversation A via conversation B's URL must 404."""
    client, db, _settings = _app(tmp_path)

    # Seed a pending approval card in conversation A
    conversation_a_id, message_id_a = _seed_pending_card(client, db, tmp_path)

    # Create an unrelated conversation B
    conversation_b_id = client.post("/conversations", headers=_H).json()["id"]

    # Try to approve message from A via URL for conversation B — should 404
    resp = client.post(
        f"/conversations/{conversation_b_id}/approvals/{message_id_a}",
        json={"decision": "approve"},
        headers=_H,
    )
    assert resp.status_code == 404

    # Verify the message in A is still pending
    thread = repos.list_conversation_messages(db, conversation_a_id)
    assert any(
        m["id"] == message_id_a and m["content"]["status"] == "pending"
        for m in thread
    )


# --- auth ------------------------------------------------------------------------------------


def test_no_token_is_unauthorized(tmp_path: Path) -> None:
    client, _db, _settings = _app(tmp_path)
    assert client.get("/conversations").status_code == 401
    assert client.post("/conversations").status_code == 401
