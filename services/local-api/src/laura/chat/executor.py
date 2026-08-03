"""Chat executor: maps a validated router decision onto conversation messages plus the
existing machinery (spec 2026-08-03-chat-first).

``execute_decision`` must NEVER raise: every failure it can observe — an ``HTTPException``
bubbling out of a service call, or anything else gone wrong — becomes an assistant ``text``
message in the thread instead, so a chat turn can never 500. ``execute_import_approval`` is
the one exception: it is called ONLY by the approvals endpoint (Task 6), which needs the real
``HTTPException`` to answer with the right status code.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, status

# Documented exception to the private-import rule: the approval flow's only entry point into
# the existing import machinery is this asset-creation + enqueue helper. Mirrors discovery.py's
# own use of context._scene_src_ranges.
from ..api.assets import _enqueue_url_fetch

# Imported BY NAME (not `from ..api import short_creator`) so tests can monkeypatch
# `laura.chat.executor.<name>` without touching the real service functions.
from ..api.short_creator import (
    run_production_follow_up,
    run_production_revert,
    run_project_auto_overview,
    run_project_auto_short,
)
from ..config import Settings
from ..db import repos
from ..db.database import Database
from ..util import new_id
from .router import RouterDecision

logger = logging.getLogger(__name__)

_NO_ACTIVE_PROJECT_TEXT = (
    "Es ist kein Projekt aktiv — sag mir, in welchem Projekt ich arbeiten soll, "
    "oder lass mich eins anlegen."
)
_NO_SESSION_TEXT = (
    "Ich finde keine laufende Produktion, auf die sich das beziehen könnte — "
    "welche Session meinst du?"
)
_UNKNOWN_TOOL_TEXT = "Das kann ich (noch) nicht ausführen."
_EXECUTION_FAILED_TEXT = (
    "Da ist beim Ausführen etwas schiefgelaufen — magst du es nochmal versuchen?"
)

_DEFAULT_SHORT_TARGET_SECONDS = 60
_DEFAULT_OVERVIEW_TARGET_SECONDS = 180
_DEFAULT_FORMAT = "insta"
_DEFAULT_LANGUAGE = "German"


# --- small pure helpers -----------------------------------------------------------------------


def _detail_reason(detail: Any) -> str:
    """The honest-passthrough text for an ``HTTPException.detail``: a dict's ``"reason"`` when
    present, else the detail stringified as-is — never let the raw exception shape leak."""
    if isinstance(detail, dict):
        reason = detail.get("reason")
        if reason is not None:
            return str(reason)
    return str(detail)


def _optional(args: dict[str, Any], key: str, default: Any) -> Any:
    """``args[key]`` when present and not ``None``, else *default* (router validation only
    guarantees TYPE for optional args, not presence)."""
    value = args.get(key)
    return default if value is None else value


def _rationale_text(result: dict[str, Any]) -> str:
    """Scout/overview rationale, plus any config/material warnings appended underneath."""
    lines = [str(result.get("rationale") or "")]
    warnings = result.get("warnings") or []
    if warnings:
        lines.append("Hinweise: " + "; ".join(str(w) for w in warnings))
    return "\n\n".join(line for line in lines if line)


def _resolve_session_id(messages: list[dict[str, Any]], session_ref: str) -> str | None:
    """Exact ``session_id`` match against the thread's ``action`` messages; otherwise the
    NEWEST action carrying one — covers ``session_ref == "last"`` and any ref the thread never
    saw. ``None`` when the thread has no production action at all."""
    action_refs: list[str] = []
    for message in messages:
        if message.get("kind") != "action":
            continue
        refs = (message.get("content") or {}).get("refs") or {}
        session_id = refs.get("session_id")
        if session_id:
            action_refs.append(str(session_id))
    if not action_refs:
        return None
    for session_id in action_refs:
        if session_id == session_ref:
            return session_id
    return action_refs[-1]


# --- message construction ----------------------------------------------------------------------


def _append(
    db: Database,
    conversation_id: str,
    *,
    role: str,
    kind: str,
    content: dict[str, Any],
    now_utc: str,
) -> dict[str, Any]:
    """Append one message and touch the conversation. Returns the appended message (content
    parsed) — the same shape ``repos.list_conversation_messages`` hands back."""
    message_id = new_id()
    seq = repos.append_conversation_message(
        db,
        message_id=message_id,
        conversation_id=conversation_id,
        role=role,
        kind=kind,
        content=content,
        created_utc=now_utc,
    )
    repos.touch_conversation(db, conversation_id, now_utc)
    return {
        "id": message_id,
        "conversation_id": conversation_id,
        "seq": seq,
        "role": role,
        "kind": kind,
        "content": content,
        "created_at": now_utc,
    }


def _append_text(db: Database, conversation_id: str, text: str, now_utc: str) -> dict[str, Any]:
    return _append(
        db, conversation_id, role="assistant", kind="text", content={"text": text}, now_utc=now_utc
    )


# --- per-tool handlers (each returns the appended messages, in order) --------------------------


def _handle_reply(
    db: Database, conversation_id: str, decision: RouterDecision, now_utc: str
) -> list[dict[str, Any]]:
    text = str(decision["args"].get("text") or "")
    return [_append_text(db, conversation_id, text, now_utc)]


def _handle_create_project(
    db: Database, settings: Settings, conversation_id: str, decision: RouterDecision, now_utc: str
) -> list[dict[str, Any]]:
    name = str(decision["args"]["name"])
    pid = new_id()
    project_root = settings.workspace_root / f"project-{pid}"
    project_root.mkdir(parents=True, exist_ok=True)
    repos.create_project(
        db,
        project_id=pid,
        name=name,
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(project_root),
    )
    repos.set_conversation_project(db, conversation_id, pid)
    text = f"Projekt ‚{name}' angelegt und aktiviert."
    return [_append_text(db, conversation_id, text, now_utc)]


def _handle_switch_project(
    db: Database, conversation_id: str, decision: RouterDecision, now_utc: str
) -> list[dict[str, Any]]:
    ref = str(decision["args"]["ref"]).strip()
    ref_lower = ref.lower()
    projects = repos.list_projects(db)
    matches = [
        p
        for p in projects
        if str(p["name"]).strip().lower() == ref_lower
        or str(p["id"]).lower().startswith(ref_lower)
    ]
    if len(matches) == 1:
        project = matches[0]
        repos.set_conversation_project(db, conversation_id, str(project["id"]))
        text = f"Zu Projekt ‚{project['name']}' gewechselt."
    elif not matches:
        text = (
            f"Ich finde kein Projekt zu ‚{ref}' — wie heißt es genau, "
            "oder soll ich eins anlegen?"
        )
    else:
        names = ", ".join(f"‚{p['name']}'" for p in matches)
        text = f"Das ist nicht eindeutig — meinst du {names}?"
    return [_append_text(db, conversation_id, text, now_utc)]


def _handle_propose_import(
    db: Database,
    active_project_id: str | None,
    conversation_id: str,
    decision: RouterDecision,
    now_utc: str,
) -> list[dict[str, Any]]:
    if not active_project_id:
        return [_append_text(db, conversation_id, _NO_ACTIVE_PROJECT_TEXT, now_utc)]
    urls = [str(u) for u in decision["args"]["urls"]]
    content = {
        "action_type": "import_urls",
        "payload": {"urls": urls, "project_id": active_project_id},
        "status": "pending",
        "decided_at": None,
        "result": None,
    }
    return [
        _append(
            db, conversation_id, role="assistant", kind="approval_request", content=content,
            now_utc=now_utc,
        )
    ]


def _handle_start_short(
    db: Database,
    active_project_id: str | None,
    conversation_id: str,
    decision: RouterDecision,
    now_utc: str,
) -> list[dict[str, Any]]:
    if not active_project_id:
        return [_append_text(db, conversation_id, _NO_ACTIVE_PROJECT_TEXT, now_utc)]
    args = decision["args"]
    topic = str(args["topic"])
    target_seconds = int(_optional(args, "target_seconds", _DEFAULT_SHORT_TARGET_SECONDS))
    fmt = _optional(args, "format", _DEFAULT_FORMAT)
    try:
        result = run_project_auto_short(
            db, active_project_id, topic=topic, target_seconds=target_seconds, format=fmt,
            language=_DEFAULT_LANGUAGE,
        )
    except HTTPException as exc:
        return [_append_text(db, conversation_id, _detail_reason(exc.detail), now_utc)]
    text_msg = _append_text(db, conversation_id, _rationale_text(result), now_utc)
    action_msg = _append(
        db, conversation_id, role="assistant", kind="action",
        content={
            "tool": "start_short",
            "args": dict(args),
            "refs": {"session_id": result["session_id"], "job_id": result["job_id"]},
            "outcome": "running",
        },
        now_utc=now_utc,
    )
    return [text_msg, action_msg]


def _handle_start_overview(
    db: Database,
    active_project_id: str | None,
    conversation_id: str,
    decision: RouterDecision,
    now_utc: str,
) -> list[dict[str, Any]]:
    if not active_project_id:
        return [_append_text(db, conversation_id, _NO_ACTIVE_PROJECT_TEXT, now_utc)]
    args = decision["args"]
    topic = str(args["topic"])
    target_seconds = int(_optional(args, "target_seconds", _DEFAULT_OVERVIEW_TARGET_SECONDS))
    try:
        result = run_project_auto_overview(
            db, active_project_id, topic=topic, target_seconds=target_seconds,
            language=_DEFAULT_LANGUAGE,
        )
    except HTTPException as exc:
        return [_append_text(db, conversation_id, _detail_reason(exc.detail), now_utc)]
    text_msg = _append_text(db, conversation_id, _rationale_text(result), now_utc)
    action_msg = _append(
        db, conversation_id, role="assistant", kind="action",
        content={
            "tool": "start_overview",
            "args": dict(args),
            "refs": {
                "export_id": result["export_id"],
                "job_id": result["job_id"],
                "sequence_id": result["sequence_id"],
            },
            "outcome": "running",
        },
        now_utc=now_utc,
    )
    return [text_msg, action_msg]


def _handle_follow_up(
    db: Database,
    conversation_id: str,
    messages: list[dict[str, Any]],
    decision: RouterDecision,
    now_utc: str,
) -> list[dict[str, Any]]:
    args = decision["args"]
    session_ref = str(args["session_ref"])
    text = str(args["text"])
    session_id = _resolve_session_id(messages, session_ref)
    if session_id is None:
        return [_append_text(db, conversation_id, _NO_SESSION_TEXT, now_utc)]
    try:
        result = run_production_follow_up(db, session_id, text)
    except HTTPException as exc:
        return [_append_text(db, conversation_id, _detail_reason(exc.detail), now_utc)]
    action_msg = _append(
        db, conversation_id, role="assistant", kind="action",
        content={
            "tool": "follow_up",
            "args": dict(args),
            "refs": {"session_id": session_id, "job_id": result["job_id"]},
            "outcome": "running",
        },
        now_utc=now_utc,
    )
    return [action_msg]


def _handle_revert(
    db: Database,
    conversation_id: str,
    messages: list[dict[str, Any]],
    decision: RouterDecision,
    now_utc: str,
) -> list[dict[str, Any]]:
    args = decision["args"]
    session_ref = str(args["session_ref"])
    artifact = str(args["artifact"])
    version = int(args["version"])
    session_id = _resolve_session_id(messages, session_ref)
    if session_id is None:
        return [_append_text(db, conversation_id, _NO_SESSION_TEXT, now_utc)]
    try:
        run_production_revert(db, session_id, artifact, version)
    except HTTPException as exc:
        return [_append_text(db, conversation_id, _detail_reason(exc.detail), now_utc)]
    text = f"zurückgedreht auf v{version}"
    return [_append_text(db, conversation_id, text, now_utc)]


# --- entry points --------------------------------------------------------------------------


def execute_decision(
    db: Database,
    settings: Settings,
    *,
    conversation_id: str,
    decision: RouterDecision,
    now_utc: str,
) -> list[dict[str, Any]]:
    """Run one validated router decision, appending the resulting message(s) to the thread.

    Never raises: every failure this function can observe — an ``HTTPException`` from the
    machinery it calls, an unresolved reference, or anything else gone wrong (including a
    programming error) — becomes an assistant ``text`` message instead, so a chat turn can
    never 500 the thread.
    """
    try:
        conversation = repos.get_conversation(db, conversation_id)
        active_project_id = (conversation or {}).get("active_project_id")
        tool = decision["tool"]

        if tool == "reply":
            return _handle_reply(db, conversation_id, decision, now_utc)
        if tool == "create_project":
            return _handle_create_project(db, settings, conversation_id, decision, now_utc)
        if tool == "switch_project":
            return _handle_switch_project(db, conversation_id, decision, now_utc)
        if tool == "propose_import":
            return _handle_propose_import(db, active_project_id, conversation_id, decision, now_utc)
        if tool == "start_short":
            return _handle_start_short(db, active_project_id, conversation_id, decision, now_utc)
        if tool == "start_overview":
            return _handle_start_overview(db, active_project_id, conversation_id, decision, now_utc)
        if tool == "follow_up":
            messages = repos.list_conversation_messages(db, conversation_id)
            return _handle_follow_up(db, conversation_id, messages, decision, now_utc)
        if tool == "revert":
            messages = repos.list_conversation_messages(db, conversation_id)
            return _handle_revert(db, conversation_id, messages, decision, now_utc)

        # Defensive: the router only ever hands out a tool from TOOLS, so this branch should be
        # unreachable — but the thread must never crash on a decision it does not recognize.
        return [_append_text(db, conversation_id, _UNKNOWN_TOOL_TEXT, now_utc)]
    except Exception:  # noqa: BLE001 — the thread must never 500 on a turn
        logger.exception("chat executor failed for tool %r", decision.get("tool"))
        return [_append_text(db, conversation_id, _EXECUTION_FAILED_TEXT, now_utc)]


def execute_import_approval(db: Database, *, message_id: str, now_utc: str) -> list[dict[str, Any]]:
    """Approve and execute an ``import_urls`` approval card — the only entry point into the
    import machinery from chat. Called ONLY by the approvals endpoint on an ``approve``
    decision; a ``reject`` only flips the card's status and never reaches this function
    (Task 6). Unlike :func:`execute_decision`, this DOES raise: the approvals endpoint
    translates the ``HTTPException`` into the response.
    """
    message = repos.get_conversation_message(db, message_id)
    if message is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "message not found")

    content = message["content"]
    if message["kind"] != "approval_request" or content.get("status") != "pending":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"approval already decided: status={content.get('status')!r}",
        )

    conversation_id = str(message["conversation_id"])
    payload = content.get("payload") or {}
    urls = [str(u) for u in payload.get("urls") or []]
    project_id = str(payload.get("project_id"))

    # Record the decision BEFORE executing: a crash mid-loop then leaves the card "approved",
    # not stuck "pending" — never re-executable by a naive retry.
    approved_content = {**content, "status": "approved", "decided_at": now_utc}
    repos.update_conversation_message_content(db, message_id, approved_content)

    asset_ids: list[str] = []
    job_ids: list[str] = []
    for url in urls:
        asset_id, job_id = _enqueue_url_fetch(
            db, project_id, url, display_name=None, fmt=None, cookies_from_browser=None,
        )
        asset_ids.append(asset_id)
        job_ids.append(job_id)

    executed_content = {
        **approved_content, "status": "executed", "result": {"asset_ids": asset_ids},
    }
    repos.update_conversation_message_content(db, message_id, executed_content)
    repos.touch_conversation(db, conversation_id, now_utc)

    updated_card = {**message, "content": executed_content}
    action_message = _append(
        db, conversation_id, role="assistant", kind="action",
        content={
            "tool": "import_urls",
            "args": {"urls": urls},
            "refs": {"asset_ids": asset_ids, "job_ids": job_ids},
            "outcome": "running",
        },
        now_utc=now_utc,
    )
    return [updated_card, action_message]
