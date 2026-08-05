"""Conversations API: the chat surface's HTTP layer (spec 2026-08-03-chat-first).

This is where every earlier chat-first task meets: Task 1's conversation repos, Task 4's
router, and Task 5's executor. Kept thin on purpose — parse the request, resolve auth, turn an
unknown id into a 404, then delegate the actual turn logic to ``laura.chat.router``/
``laura.chat.executor``.

The reject path is the one exception to "executor owns side effects": rejecting an approval
card is a plain status flip with no machinery behind it, so it is handled inline here rather
than routed through the executor.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from ..auth import Principal, require_permission
from ..chat.executor import execute_decision, execute_import_approval
from ..chat.router import compose_context, run_router
from ..config import Settings
from ..db import repos
from ..db.database import Database
from ..short_creator.providers import resolve_from_env
from ..util import new_id, utcnow_iso

router = APIRouter(tags=["chat"])


class ChatMessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class ApprovalDecisionIn(BaseModel):
    decision: Literal["approve", "reject"]


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def _running_jobs_count(db: Database) -> int:
    """Jobs still in flight — feeds the router context's "Running jobs: N" line.

    No existing repos helper filters jobs by status, so this is a direct, narrowly-scoped
    COUNT query rather than a new general-purpose repos function for one caller.
    """
    with db.connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued', 'running')"
        ).fetchone()
        return int(row["n"])


def _conversation_or_404(db: Database, conversation_id: str) -> dict[str, Any]:
    conversation = repos.get_conversation(db, conversation_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
    return conversation


def _append_user_message(
    db: Database, conversation_id: str, text: str, now_utc: str
) -> dict[str, Any]:
    """Persist the turn's user message. Same shape as ``repos.list_conversation_messages``
    hands back, so the POST response renders identically to a GET reload."""
    message_id = new_id()
    content = {"text": text}
    seq = repos.append_conversation_message(
        db, message_id=message_id, conversation_id=conversation_id, role="user", kind="text",
        content=content, created_utc=now_utc,
    )
    repos.touch_conversation(db, conversation_id, now_utc)
    return {
        "id": message_id,
        "conversation_id": conversation_id,
        "seq": seq,
        "role": "user",
        "kind": "text",
        "content": content,
        "created_at": now_utc,
    }


@router.post("/conversations")
def create_conversation(
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> dict[str, Any]:
    conversation_id = new_id()
    repos.create_conversation(
        _db(request), conversation_id=conversation_id, created_utc=utcnow_iso()
    )
    return {"id": conversation_id}


@router.get("/conversations")
def list_conversations(
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("read"))],
) -> list[dict[str, Any]]:
    return repos.list_conversations(_db(request))


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("read"))],
) -> dict[str, Any]:
    db = _db(request)
    conversation = _conversation_or_404(db, conversation_id)
    messages = repos.list_conversation_messages(db, conversation_id)
    return {
        "id": conversation["id"],
        "title": conversation["title"],
        "active_project_id": conversation["active_project_id"],
        "messages": messages,
    }


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> Response:
    db = _db(request)
    _conversation_or_404(db, conversation_id)
    repos.delete_conversation(db, conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/conversations/{conversation_id}/message", status_code=status.HTTP_202_ACCEPTED)
def post_message(
    conversation_id: str,
    body: ChatMessageIn,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> dict[str, Any]:
    """One chat turn: persist the user's text, route it to exactly one tool call, execute it.

    Returns ``{"messages": [user_msg, *appended]}`` — every message as stored (content
    parsed), so the client renders identically on this response and on a later GET reload.
    """
    db = _db(request)
    settings: Settings = request.app.state.settings
    conversation = _conversation_or_404(db, conversation_id)
    now = utcnow_iso()

    user_message = _append_user_message(db, conversation_id, body.text, now)

    if not str(conversation.get("title") or ""):
        repos.set_conversation_title(db, conversation_id, body.text[:60])

    active_project_id = conversation.get("active_project_id")
    project = repos.get_project(db, active_project_id) if active_project_id else None
    asset_names = (
        [str(a["display_name"]) for a in repos.list_assets(db, active_project_id)]
        if project is not None and active_project_id
        else None
    )
    running_jobs = _running_jobs_count(db)
    messages = repos.list_conversation_messages(db, conversation_id)
    context = compose_context(
        project=project, running_jobs=running_jobs, messages=messages, asset_names=asset_names
    )

    config = resolve_from_env()
    runner = getattr(request.app.state, "chat_runner", None)
    decision = run_router(config, context=context, user_text=body.text, runner=runner)

    appended = execute_decision(
        db, settings, conversation_id=conversation_id, decision=decision, now_utc=now,
        principal=principal,
        discuss_runner=getattr(request.app.state, "discuss_runner", None),
    )
    return {"messages": [user_message, *appended]}


@router.post("/conversations/{conversation_id}/approvals/{message_id}")
def decide_approval(
    conversation_id: str,
    message_id: str,
    body: ApprovalDecisionIn,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> dict[str, Any]:
    """The only enforcement point for an approval card: approve runs the executor's import
    machinery; reject is a plain status flip with no side effects. Both 404 on an unknown
    message and 409 when the card is no longer ``"pending"``."""
    db = _db(request)
    _conversation_or_404(db, conversation_id)
    now = utcnow_iso()

    message = repos.get_conversation_message(db, message_id)
    if message is None or message["conversation_id"] != conversation_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "message not found")

    if body.decision == "approve":
        messages = execute_import_approval(db, message_id=message_id, now_utc=now)
        return {"messages": messages}

    message = repos.get_conversation_message(db, message_id)
    if message is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "message not found")
    content = message["content"]
    if message["kind"] != "approval_request" or content.get("status") != "pending":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"approval already decided: status={content.get('status')!r}",
        )
    updated_content = {**content, "status": "rejected", "decided_at": now}
    repos.update_conversation_message_content(db, message_id, updated_content)
    repos.touch_conversation(db, conversation_id, now)
    return {"messages": [{**message, "content": updated_content}]}
