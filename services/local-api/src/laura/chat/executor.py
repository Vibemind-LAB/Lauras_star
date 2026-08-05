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

from .. import audit

# Imported BY NAME (see the short_creator import block below) so tests can monkeypatch
# `laura.chat.executor.reindex_segments` without touching the real semantic index.
from ..analysis.semantic_sync import reindex_segments

# Documented exception to the private-import rule: the approval flow's only entry points into
# the existing import machinery are this playlist-expansion + asset-creation/enqueue pair.
# Mirrors discovery.py's own use of context._scene_src_ranges.
from ..api.assets import _enqueue_url_fetch, _expand_playlist_urls

# Imported BY NAME (not `from ..api import short_creator`) so tests can monkeypatch
# `laura.chat.executor.<name>` without touching the real service functions.
from ..api.short_creator import (
    run_production_follow_up,
    run_production_revert,
    run_project_auto_overview,
    run_project_auto_short,
)
from ..auth import Principal
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

# review_transcript caps its card at the first 100 segments (payload.total still carries the
# real count) — a transcript with thousands of segments must never blow up the message JSON.
_REVIEW_SEGMENT_LIMIT = 100
# media_assets.audio_sample_rate is NULL until a real ffprobe run has populated it (e.g. a
# synthetic test asset, or an asset probed before ASR); 16kHz matches ingest/audio.py's
# ASR_SAMPLE_RATE (the resample target ASR always writes samples against), so it is the
# least-wrong fallback for start_s.
_DEFAULT_AUDIO_SAMPLE_RATE = 16000


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


# The only refs that may fall back to the newest action. An explicit ref that matches
# nothing must NOT — the spec's Rückfrage rule (2026-08-03-chat-first: "kann er nicht
# auflösen → Rückfrage"), so a hallucinated router ref can never silently redirect a
# follow_up/revert mutation to the newest session.
_LAST_SESSION_PLACEHOLDERS = frozenset({"", "last", "latest"})


def _resolve_session_id(messages: list[dict[str, Any]], session_ref: str) -> str | None:
    """Exact ``session_id`` match against the thread's ``action`` messages; the literal
    placeholders ``"last"``/``"latest"`` (or an empty ref) resolve to the NEWEST action.
    ``None`` when the thread has no production action at all — or when an explicit ref
    matches nothing, so the caller asks back instead of executing."""
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
    if session_ref.strip().lower() in _LAST_SESSION_PLACEHOLDERS:
        return action_refs[-1]
    return None


def _resolve_asset_ref(
    db: Database, active_project_id: str, asset_ref: str
) -> tuple[dict[str, Any] | None, str | None]:
    """Resolve ``asset_ref`` against the active project's assets — mirrors
    ``_handle_switch_project``'s name matching EXACTLY: an exact ``display_name`` match
    (case-insensitive) OR an ``id`` prefix match. Returns ``(asset, None)`` on an
    unambiguous match, else ``(None, <German clarification text>)`` — no match lists the
    project's available video names (unlike ``switch_project``'s no-match text, which does
    not enumerate existing projects); more than one match asks which one, same as
    ``switch_project``.
    """
    ref = asset_ref.strip()
    ref_lower = ref.lower()
    assets = repos.list_assets(db, active_project_id)
    matches = [
        a
        for a in assets
        if str(a["display_name"]).strip().lower() == ref_lower
        or str(a["id"]).lower().startswith(ref_lower)
    ]
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        if assets:
            names = ", ".join(f"‚{a['display_name']}'" for a in assets)
            text = f"Ich finde kein Video zu ‚{ref}' — meinst du eins von: {names}?"
        else:
            text = f"Ich finde kein Video zu ‚{ref}' — in diesem Projekt sind noch keine Videos."
        return None, text
    names = ", ".join(f"‚{a['display_name']}'" for a in matches)
    return None, f"Das ist nicht eindeutig — meinst du {names}?"


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
    db: Database,
    settings: Settings,
    conversation_id: str,
    decision: RouterDecision,
    now_utc: str,
    principal: Principal | None,
) -> list[dict[str, Any]]:
    """Mirrors ``api/projects.create_project``'s core exactly, including its two
    request-scoped side effects: the project's ``org_id`` and the audit trail. Chat has no
    HTTP principal of its own — when none is supplied (the default, matching every caller
    before this fix), this uses the SAME implicit local-owner identity
    ``auth/deps.resolve_principal`` falls back to when no API key is presented
    (:func:`laura.audit.system_principal`), rather than inventing a new actor scheme."""
    name = str(decision["args"]["name"])
    pid = new_id()
    project_root = settings.workspace_root / f"project-{pid}"
    project_root.mkdir(parents=True, exist_ok=True)
    org_id = principal.org_id if principal is not None else None
    repos.create_project(
        db,
        project_id=pid,
        name=name,
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(project_root),
        org_id=org_id,
    )
    audit.record(
        db, principal or audit.system_principal(), "project.create",
        entity_type="project", entity_id=pid,
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


_SCRIPT_ALREADY_APPROVED_TEXT = "Script war schon freigegeben."
_NO_SCRIPT_TO_APPROVE_TEXT = (
    "Es gibt noch kein Script zum Freigeben — die Produktion hält von selbst am Gate."
)
_SCRIPT_APPROVED_FOLLOW_UP_TEXT = (
    "Script freigegeben — bitte fortsetzen: Voice, Cutlist, Contact Sheet, Render."
)


def _handle_approve_script(
    db: Database,
    conversation_id: str,
    messages: list[dict[str, Any]],
    decision: RouterDecision,
    now_utc: str,
) -> list[dict[str, Any]]:
    """Gate B: the user approving the script in chat. Resolves ``session_ref`` exactly like
    ``follow_up``/``revert`` (:func:`_resolve_session_id`), opens the session's board directly
    (``board_root_for`` + ``Board.open``, imported locally — mirrors every board-touching helper
    in ``api/short_creator.py``, and keeps this module import-light for callers without the
    'autoshort' extra) to read and flip the gate itself, then enqueues the SAME follow-up run
    ``follow_up`` would (:func:`run_production_follow_up`) so voice/cutlist/contact
    sheet/render actually continue.

    Refuses outright (no stamp, no run) when the board has no script yet — an approval before
    the team ever wrote one would otherwise land pre-emptively and the gate would never
    actually pause the run (review finding).

    Approval is bound to the script's CONTENT, not just a bare timestamp
    (:func:`~laura.short_creator.board_models.content_hash`, the same idiom
    ``synthesize_script_voice``/``build_cutlist`` already use for staleness): an
    already-approved board whose stamped hash still matches the CURRENT script is a no-op
    (German text, no new run — approving twice must not burn a second production turn), but
    one whose script has since changed (a team rewrite, or a revert to a DIFFERENT version) is
    treated as a FRESH approval — the whole point of content-binding is that the old stamp no
    longer speaks for new text.

    The approval stamp must land on the board BEFORE the follow-up run is enqueued (the voice
    tool reads ``meta.script_approved_utc``/``script_approved_script_hash`` at job runtime,
    which can start before the enqueue call here even returns), so a failure to start that
    follow-up run cannot be fixed by reordering the two calls — only by a COMPENSATING rollback
    once the failure is known (review finding: the stamp used to persist unconditionally, so a
    session race, a config preflight failure, or any bug in the follow-up call left the gate
    permanently open while the user read an error implying nothing had happened). Both
    exception paths below revert via ``board.clear_script_approval()`` before the existing
    error text goes out — the known ``HTTPException`` case keeps its honest-passthrough text,
    and anything else re-raises into ``execute_decision``'s own catch-all, unchanged."""
    args = decision["args"]
    session_ref = str(args["session_ref"])
    session_id = _resolve_session_id(messages, session_ref)
    if session_id is None:
        return [_append_text(db, conversation_id, _NO_SESSION_TEXT, now_utc)]
    session = repos.get_production_session(db, session_id)
    if session is None:
        return [_append_text(db, conversation_id, _NO_SESSION_TEXT, now_utc)]
    asset_id = str(session["asset_id"])

    from ..short_creator.board import Board
    from ..short_creator.board_models import Script, content_hash
    from ..short_creator.production_orchestrator import board_root_for

    try:
        board = Board.open(board_root_for(db, asset_id, session_id))
    except (ValueError, FileNotFoundError):
        return [_append_text(db, conversation_id, _NO_SESSION_TEXT, now_utc)]

    script = board.load("script")
    if not isinstance(script, Script):
        return [_append_text(db, conversation_id, _NO_SCRIPT_TO_APPROVE_TEXT, now_utc)]
    current_hash = content_hash(script)

    meta = board.meta()
    if (
        meta.script_approved_utc is not None
        and meta.script_approved_script_hash == current_hash
    ):
        return [_append_text(db, conversation_id, _SCRIPT_ALREADY_APPROVED_TEXT, now_utc)]
    board.set_script_approved(now_utc, current_hash)

    try:
        result = run_production_follow_up(db, session_id, _SCRIPT_APPROVED_FOLLOW_UP_TEXT)
    except HTTPException as exc:
        board.clear_script_approval()
        return [_append_text(db, conversation_id, _detail_reason(exc.detail), now_utc)]
    except Exception:
        board.clear_script_approval()
        raise
    action_msg = _append(
        db, conversation_id, role="assistant", kind="action",
        content={
            "tool": "approve_script",
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


# --- Transkript-Gates (Task 5): review / correct / confirm --------------------------------------


def _segment_review_row(seg: dict[str, Any], index: int, sample_rate: int) -> dict[str, Any]:
    return {
        "index": index,
        "id": seg["id"],
        "start_s": round(seg["start_sample"] / sample_rate, 1),
        "text": seg["text"],
    }


def _review_transcript_content(db: Database, asset: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """The ``review_transcript``/``correct_transcript`` action card content plus the real
    segment count (``payload.total`` — the card itself carries at most the first
    ``_REVIEW_SEGMENT_LIMIT`` segments)."""
    asset_id = str(asset["id"])
    run = repos.get_latest_transcript_run(db, asset_id)
    segments = repos.get_transcript(db, asset_id, str(run["id"])) if run is not None else []
    sample_rate = int(asset.get("audio_sample_rate") or _DEFAULT_AUDIO_SAMPLE_RATE)
    total = len(segments)
    rows = [
        _segment_review_row(seg, i, sample_rate)
        for i, seg in enumerate(segments[:_REVIEW_SEGMENT_LIMIT], start=1)
    ]
    content = {
        "tool": "review_transcript",
        "refs": {"asset_id": asset_id},
        "outcome": "done",
        "payload": {
            "confirmed_at": asset.get("transcript_confirmed_at"),
            "segments": rows,
            "total": total,
        },
    }
    return content, total


def _append_review_transcript_messages(
    db: Database, conversation_id: str, asset: dict[str, Any], now_utc: str
) -> list[dict[str, Any]]:
    """Append the review card (capped at ``_REVIEW_SEGMENT_LIMIT`` segments). Shared by
    ``_handle_review_transcript``, ``_handle_correct_transcript`` (whose card is the SAME
    shape, just built from the just-corrected transcript), and ``_handle_confirm_transcript``
    (re-appends the SAME card so its confirmed badge flips after reload).

    Deliberately does NOT append a remainder text line: the card itself already renders
    "… und N weitere Segmente" from ``payload.total - payload.segments.length``
    (``ReviewTranscriptCard`` in ``apps/desktop/src/components/chat/ActionCard.tsx``) — a
    second text message here just repeated the same fact in the thread (review finding)."""
    content, _total = _review_transcript_content(db, asset)
    card = _append(
        db, conversation_id, role="assistant", kind="action", content=content, now_utc=now_utc
    )
    return [card]


def _invalid_segment_index_text(segment_index: int, total: int) -> str:
    if total == 0:
        return (
            f"Segment {segment_index} gibt es nicht — für dieses Video gibt es noch "
            "keine Segmente."
        )
    return f"Segment {segment_index} gibt es nicht — gültiger Bereich ist 1–{total}."


def _handle_review_transcript(
    db: Database,
    active_project_id: str | None,
    conversation_id: str,
    decision: RouterDecision,
    now_utc: str,
) -> list[dict[str, Any]]:
    if not active_project_id:
        return [_append_text(db, conversation_id, _NO_ACTIVE_PROJECT_TEXT, now_utc)]
    ref = str(decision["args"]["asset_ref"])
    asset, error_text = _resolve_asset_ref(db, active_project_id, ref)
    if asset is None:
        assert error_text is not None  # _resolve_asset_ref always pairs None with a text
        return [_append_text(db, conversation_id, error_text, now_utc)]
    return _append_review_transcript_messages(db, conversation_id, asset, now_utc)


def _handle_correct_transcript(
    db: Database,
    active_project_id: str | None,
    conversation_id: str,
    decision: RouterDecision,
    now_utc: str,
    principal: Principal | None,
) -> list[dict[str, Any]]:
    if not active_project_id:
        return [_append_text(db, conversation_id, _NO_ACTIVE_PROJECT_TEXT, now_utc)]
    args = decision["args"]
    ref = str(args["asset_ref"])
    asset, error_text = _resolve_asset_ref(db, active_project_id, ref)
    if asset is None:
        assert error_text is not None
        return [_append_text(db, conversation_id, error_text, now_utc)]
    asset_id = str(asset["id"])
    run = repos.get_latest_transcript_run(db, asset_id)
    segments = repos.get_transcript(db, asset_id, str(run["id"])) if run is not None else []
    total = len(segments)
    corrections = list(args["corrections"])

    # Validate every correction BEFORE writing any of them — a batch with one bad index must
    # leave the transcript untouched, not half-corrected.
    for item in corrections:
        segment_index = int(item["segment_index"])
        if segment_index < 1 or segment_index > total:
            text = _invalid_segment_index_text(segment_index, total)
            return [_append_text(db, conversation_id, text, now_utc)]

    for item in corrections:
        segment_index = int(item["segment_index"])
        segment_id = str(segments[segment_index - 1]["id"])
        repos.update_segment(db, segment_id, text=str(item["text"]), speaker_id=None)
        audit.record(
            db, principal or audit.system_principal(), "transcript.update",
            entity_type="segment", entity_id=segment_id,
        )
        # Best-effort re-index of just this segment (never raises), mirroring the HTTP
        # PATCH /transcript/segments/{id} endpoint's own sequencing (api/analysis.py).
        reindex_segments(db, asset_id, [segment_id])

    k = len(corrections)
    reply_text = f"{k} {'Segment' if k == 1 else 'Segmente'} korrigiert."
    text_msg = _append_text(db, conversation_id, reply_text, now_utc)
    return [text_msg, *_append_review_transcript_messages(db, conversation_id, asset, now_utc)]


def _handle_confirm_transcript(
    db: Database,
    active_project_id: str | None,
    conversation_id: str,
    decision: RouterDecision,
    now_utc: str,
    principal: Principal | None,
) -> list[dict[str, Any]]:
    if not active_project_id:
        return [_append_text(db, conversation_id, _NO_ACTIVE_PROJECT_TEXT, now_utc)]
    ref = str(decision["args"]["asset_ref"])
    asset, error_text = _resolve_asset_ref(db, active_project_id, ref)
    if asset is None:
        assert error_text is not None
        return [_append_text(db, conversation_id, error_text, now_utc)]
    asset_id = str(asset["id"])
    repos.set_transcript_confirmed_at(db, asset_id, now_utc)
    # Audit parity with the HTTP twin POST /assets/{asset_id}/transcript:confirm
    # (api/analysis.py's confirm_transcript), which records the same action.
    audit.record(
        db, principal or audit.system_principal(), "transcript.confirm",
        entity_type="asset", entity_id=asset_id,
    )
    text = f"Transkript von ‚{asset['display_name']}' bestätigt."
    text_msg = _append_text(db, conversation_id, text, now_utc)
    # Re-append a FRESH review_transcript card so its confirmed badge flips after reload — the
    # card already in the thread is frozen at the content it was built from (message content
    # never mutates in place), so without this the badge stays "unbestätigt" forever even
    # though the confirmation itself took (review finding). Re-fetch the asset first:
    # `asset` above was resolved BEFORE `set_transcript_confirmed_at` ran, so it still carries
    # the OLD (pre-confirm) `transcript_confirmed_at`.
    refreshed_asset = repos.get_asset(db, asset_id)
    assert refreshed_asset is not None  # just confirmed against this id; cannot vanish mid-call
    return [
        text_msg,
        *_append_review_transcript_messages(db, conversation_id, refreshed_asset, now_utc),
    ]


# --- entry points --------------------------------------------------------------------------


def execute_decision(
    db: Database,
    settings: Settings,
    *,
    conversation_id: str,
    decision: RouterDecision,
    now_utc: str,
    principal: Principal | None = None,
) -> list[dict[str, Any]]:
    """Run one validated router decision, appending the resulting message(s) to the thread.

    Never raises: every failure this function can observe — an ``HTTPException`` from the
    machinery it calls, an unresolved reference, or anything else gone wrong (including a
    programming error) — becomes an assistant ``text`` message instead, so a chat turn can
    never 500 the thread.

    ``principal`` is the HTTP caller (Task 6 passes the resolved request principal); it
    defaults to ``None`` so every pre-existing caller keeps working. ``create_project``
    consumes it to give chat-created projects the same ``org_id``/audit-trail parity as
    ``POST /projects``; ``correct_transcript`` and ``confirm_transcript`` consume it the same
    way for their ``transcript.update`` / ``transcript.confirm`` audit rows (falling back to
    :func:`laura.audit.system_principal` when chat has no HTTP caller of its own, exactly
    like ``create_project`` — and matching each tool's HTTP twin in ``api/analysis.py``).
    """
    try:
        conversation = repos.get_conversation(db, conversation_id)
        active_project_id = (conversation or {}).get("active_project_id")
        tool = decision["tool"]

        if tool == "reply":
            return _handle_reply(db, conversation_id, decision, now_utc)
        if tool == "create_project":
            return _handle_create_project(
                db, settings, conversation_id, decision, now_utc, principal
            )
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
        if tool == "review_transcript":
            return _handle_review_transcript(
                db, active_project_id, conversation_id, decision, now_utc
            )
        if tool == "correct_transcript":
            return _handle_correct_transcript(
                db, active_project_id, conversation_id, decision, now_utc, principal
            )
        if tool == "confirm_transcript":
            return _handle_confirm_transcript(
                db, active_project_id, conversation_id, decision, now_utc, principal
            )
        if tool == "approve_script":
            messages = repos.list_conversation_messages(db, conversation_id)
            return _handle_approve_script(db, conversation_id, messages, decision, now_utc)

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
    translates the ``HTTPException`` into the response. Raises 404 on an unknown message, 409
    when the card is already decided OR when the card's project has since been deleted (card
    stays "pending" in that case, not flipped).

    Playlist/channel URLs fan out exactly like the HTTP import lane: one asset + fetch job
    per entry. The action message's ``args.urls`` stays the approved URLs; ``refs``/``result``
    carry the full fan-out.
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

    # The card's project can be deleted between the card's creation and the user clicking
    # "Freigeben" — verify it still exists BEFORE flipping the card to "approved". Without this,
    # _enqueue_url_fetch hits an FK failure mid-loop and the endpoint 500s with the card stuck
    # "approved" forever (unlike a genuine crash mid-execution, this one is fully preventable:
    # the card must stay "pending" so a later, valid approve can still run).
    if repos.get_project(db, project_id) is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"reason": "Projekt wurde gelöscht — Import kann nicht ausgeführt werden."},
        )

    # Record the decision BEFORE executing: a crash mid-loop then leaves the card "approved",
    # not stuck "pending" — never re-executable by a naive retry.
    approved_content = {**content, "status": "approved", "decided_at": now_utc}
    repos.update_conversation_message_content(db, message_id, approved_content)

    asset_ids: list[str] = []
    job_ids: list[str] = []
    for url in urls:
        # A playlist/channel URL fans out into one asset + fetch job per entry — the same
        # expansion the HTTP import lane runs (assets.import_asset); chat has no browser
        # cookies to forward. A falsy expansion (None/empty) keeps the URL a single asset.
        entry_urls = _expand_playlist_urls(url, None)
        for entry_url in entry_urls if entry_urls else [url]:
            asset_id, job_id = _enqueue_url_fetch(
                db, project_id, entry_url, display_name=None, fmt=None,
                cookies_from_browser=None,
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
