"""The chat router: one turn, one tool call, never a crash (spec 2026-08-03-chat-first).

The chat surface's brain. Given the conversation context and the user's latest message, a
single lightweight agent picks exactly ONE tool call — or a plain ``reply`` when nothing else
fits. The reply is VALIDATED against the tool table below rather than trusted: an invalid
answer (parse failure, an unknown tool, a missing/malformed required arg) gets exactly ONE
retry with the validation error appended to the task; still invalid, or the runner
raising/timing out, lands on a deterministic fallback — a ``reply`` asking the user to
rephrase. Same seam design as :func:`laura.short_creator.scout.run_scout`: ``runner`` is
injectable so tests never touch a real LLM; ``None`` builds the real single-agent runner.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypedDict

from ..short_creator.providers import AgentConfig, build_model_client

if TYPE_CHECKING:  # annotation only — never imported at runtime
    from autogen_agentchat.agents import AssistantAgent

logger = logging.getLogger(__name__)

# Wall-clock cap on the real router's single model call (mirrors scout._SCOUT_TIMEOUT_S, but a
# router turn has no tools to iterate on, so the cap can be tighter).
_ROUTER_TIMEOUT_S = 30.0

TOOLS: frozenset[str] = frozenset(
    {
        "reply",
        "create_project",
        "switch_project",
        "propose_import",
        "start_short",
        "start_overview",
        "follow_up",
        "revert",
    }
)

_FALLBACK_TEXT = (
    "Ich bin mir nicht sicher, was ich tun soll — formulier es bitte einmal anders "
    "(z. B. 'bau mir einen 60s-Short über …')."
)

_SYSTEM_PROMPT = (
    "You are Laura's chat router. Laura is a local-first, frame-accurate AI video-editing "
    "platform. Given the conversation context and the user's latest message, decide exactly "
    "ONE next action and reply with EXACTLY one JSON object — nothing before or after it:\n"
    '{"tool": "<tool name>", "args": {...}}\n\n'
    "Available tools and their args:\n"
    '- reply: {"text": str} — say something back without taking an action. Use this whenever '
    "you are unsure what the user wants, instead of guessing.\n"
    '- create_project: {"name": str} — start a new project.\n'
    '- switch_project: {"ref": str} — switch to an existing project, by name or id.\n'
    '- propose_import: {"urls": [str, ...]} — propose importing one or more media URLs (each '
    "must start with 'http'). Never invent URLs the user did not give you.\n"
    '- start_short: {"topic": str, "target_seconds"?: int, "format"?: "insta"|"x"|"linkedin"} '
    "— start building a short about a topic.\n"
    '- start_overview: {"topic": str, "target_seconds"?: int} — start building an overview '
    "sequence about a topic.\n"
    '- follow_up: {"session_ref": str, "text": str} — send a follow-up instruction to an '
    "existing production session.\n"
    '- revert: {"session_ref": str, "artifact": str, "version": int} — revert a session\'s '
    "artifact to an earlier version.\n\n"
    "Rules: reply with EXACTLY one JSON object, no prose before or after it. Never invent "
    "project names, session references, or URLs that were not mentioned in the context or the "
    "user's latest message — ask via reply when unsure."
)


class RouterDecision(TypedDict):
    """The router's answer, adopted or fallback — the shape the chat endpoint consumes."""

    tool: str
    args: dict[str, Any]
    fallback: bool


# --- context assembly (pure) ---------------------------------------------------------------


def _compact_message(message: dict[str, Any]) -> str:
    """One message card compacted to a single line, by ``kind``.

    ``action`` refs are rendered as ``key=value`` pairs so ``session_id`` (when present)
    survives compaction — ``follow_up``/``revert`` resolution depends on it downstream.
    """
    role = str(message.get("role") or "?")
    kind = str(message.get("kind") or "text")
    content = message.get("content") or {}

    if kind == "text":
        text = str(content.get("text") or "").strip()
        return f"{role}: {text}"

    if kind == "approval_request":
        action_type = str(content.get("action_type") or "?")
        status = str(content.get("status") or "?")
        payload = content.get("payload") or {}
        urls = payload.get("urls") or []
        parts = [action_type, status, *[str(u) for u in urls]]
        return f"[approval {' '.join(parts)}]"

    if kind == "action":
        tool = str(content.get("tool") or "?")
        outcome = str(content.get("outcome") or "?")
        refs = content.get("refs") or {}
        ref_parts = [f"{key}={value}" for key, value in refs.items()]
        parts = [tool, outcome, *ref_parts]
        return f"[action {' '.join(parts)}]"

    return f"{role}: {kind}"


def compose_context(
    *, project: dict[str, Any] | None, running_jobs: int, messages: list[dict[str, Any]]
) -> str:
    """Assemble the router's context string: project line, running-jobs line, then the last 20
    messages compacted to one line each (pure string assembly, no I/O)."""
    lines: list[str] = []
    if project is not None:
        name = project.get("name") or "?"
        project_id = project.get("id") or "?"
        lines.append(f"Project: {name} (id={project_id})")
    else:
        lines.append("Project: none selected")
    lines.append(f"Running jobs: {running_jobs}")
    lines.append("")
    lines.append("Recent conversation (oldest first):")
    for message in messages[-20:]:
        lines.append(_compact_message(message))
    return "\n".join(lines)


# --- task text (pure) ------------------------------------------------------------------------


def _task_text(context: str, user_text: str) -> str:
    return (
        f"{_SYSTEM_PROMPT}\n\n"
        f"Conversation context:\n{context}\n\n"
        f"User message: {user_text}\n\n"
        'Answer with EXACTLY one JSON object as your final message, nothing before or after '
        'it: {"tool": "<tool name>", "args": {...}}'
    )


def _retry_task_text(task: str, error: str) -> str:
    return (
        f"{task}\n\n"
        f"Your previous reply was invalid: {error}. Reply again with ONE corrected JSON "
        'object as specified above: {"tool": "<tool name>", "args": {...}}.'
    )


# --- reply parsing + validation (pure) --------------------------------------------------------


def _parse(text: str) -> dict[str, Any] | None:
    """Best-effort JSON object out of an agent reply (mirrors
    :func:`laura.short_creator.production_tools._parse_review_reply`): the substring from the
    first ``{`` to the last ``}`` (strips code fences and surrounding prose as a side effect),
    ``json.loads`` it. ``None`` on any failure."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _require_str(args: dict[str, Any], key: str) -> str | None:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        return f"{key} is missing or not a non-empty string"
    return None


def _require_int(args: dict[str, Any], key: str) -> str | None:
    value = args.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        return f"{key} is missing or not an integer"
    return None


def _validate_optional_target_seconds(args: dict[str, Any]) -> str | None:
    if "target_seconds" not in args:
        return None
    value = args["target_seconds"]
    if not isinstance(value, int) or isinstance(value, bool):
        return "target_seconds must be an integer"
    return None


_SHORT_FORMATS = frozenset({"insta", "x", "linkedin"})


def _validate_args(tool: str, args: dict[str, Any]) -> str | None:
    """Required (and constrained optional) args per tool. Returns ``None`` when valid, else a
    short, agent-correctable error naming exactly what was wrong."""
    if tool == "reply":
        return _require_str(args, "text")

    if tool == "create_project":
        return _require_str(args, "name")

    if tool == "switch_project":
        return _require_str(args, "ref")

    if tool == "propose_import":
        urls = args.get("urls")
        if not isinstance(urls, list) or not urls:
            return "propose_import.urls is missing, not a list, or empty"
        if not all(isinstance(url, str) and url.startswith("http") for url in urls):
            return "propose_import.urls must all be strings starting with 'http'"
        return None

    if tool == "start_short":
        error = _require_str(args, "topic")
        if error is not None:
            return error
        error = _validate_optional_target_seconds(args)
        if error is not None:
            return error
        if "format" in args and args["format"] not in _SHORT_FORMATS:
            return f"format must be one of {sorted(_SHORT_FORMATS)}"
        return None

    if tool == "start_overview":
        error = _require_str(args, "topic")
        if error is not None:
            return error
        return _validate_optional_target_seconds(args)

    if tool == "follow_up":
        error = _require_str(args, "session_ref")
        if error is not None:
            return error
        return _require_str(args, "text")

    if tool == "revert":
        for key in ("session_ref", "artifact"):
            error = _require_str(args, key)
            if error is not None:
                return error
        return _require_int(args, "version")

    return f"tool {tool!r} has no validator (programming error)"  # unreachable: tool in TOOLS


def _validate(parsed: dict[str, Any]) -> tuple[RouterDecision | None, str | None]:
    """Validate a parsed reply against the tool table. Returns ``(decision, None)`` when good,
    else ``(None, error)`` — *error* is meant to be appended to a retry task."""
    tool = parsed.get("tool")
    if not isinstance(tool, str) or tool not in TOOLS:
        return None, f"tool {tool!r} is not one of the known tools: {sorted(TOOLS)}"

    args = parsed.get("args")
    if not isinstance(args, dict):
        return None, f"{tool}.args is missing or not an object"

    error = _validate_args(tool, args)
    if error is not None:
        return None, error

    return {"tool": tool, "args": args, "fallback": False}, None


def _parse_and_validate(reply: str) -> tuple[RouterDecision | None, str | None]:
    parsed = _parse(reply)
    if parsed is None:
        return None, "no JSON object found in the reply"
    return _validate(parsed)


def _fallback() -> RouterDecision:
    """The deterministic fallback: a ``reply`` asking the user to rephrase — the router must
    never leave a turn without SOME answer."""
    return {"tool": "reply", "args": {"text": _FALLBACK_TEXT}, "fallback": True}


# --- the real single-agent runner (autogen-touching) -------------------------------------------


def _build_router_agent(config: AgentConfig) -> AssistantAgent:
    """One tool-less ``AssistantAgent`` that answers the router's task in one shot (lazy
    autogen import, mirrors :func:`laura.short_creator.scout._build_scout_agent`)."""
    try:
        from autogen_agentchat.agents import AssistantAgent
    except ImportError as exc:
        raise RuntimeError(
            "The chat router needs the optional 'autoshort' extra. "
            "Install it with: uv sync --extra autoshort"
        ) from exc

    model_client = build_model_client(config, role="agent")
    return AssistantAgent(
        name="chat_router",
        model_client=model_client,
        description="Routes one chat turn to exactly one tool call.",
        system_message=_SYSTEM_PROMPT,
    )


def _last_message_text(result: Any) -> str:
    """The LAST non-empty message's text from a ``TaskResult`` (mirrors
    :func:`laura.short_creator.scout._last_message_text`: ``messages[0]`` echoes the task
    itself, so concatenating every message would put it into the reply)."""
    for msg in reversed(getattr(result, "messages", None) or []):
        to_text = getattr(msg, "to_model_text", None)
        text = (to_text() if callable(to_text) else str(getattr(msg, "content", ""))).strip()
        if text:
            return text
    return ""


def _default_runner(config: AgentConfig) -> Callable[[str], str]:
    """The real runner: builds one tool-less ``AssistantAgent`` and runs it with a wall-clock
    cap. Any failure (missing extra, model error, timeout) raises out of ``run`` —
    :func:`run_router` treats every runner exception the same way: straight to the
    deterministic fallback."""

    def run(task: str) -> str:
        async def _run() -> str:
            agent = _build_router_agent(config)
            result = await agent.run(task=task)
            return _last_message_text(result)

        return asyncio.run(asyncio.wait_for(_run(), _ROUTER_TIMEOUT_S))

    return run


# --- orchestration (pure given the injected/real runner) ---------------------------------------


def _safe_call(run: Callable[[str], str], task: str) -> str | None:
    """Run *run*, converting any exception (including a timeout) into ``None`` — the router must
    never let a runner failure escape as an exception; the thread must never 500 on a turn."""
    try:
        return run(task)
    except Exception:  # noqa: BLE001 — any runner failure degrades to the fallback
        logger.warning("chat router runner failed; falling back", exc_info=True)
        return None


def run_router(
    config: AgentConfig,
    *,
    context: str,
    user_text: str,
    runner: Callable[[str], str] | None = None,
) -> RouterDecision:
    """Route one chat turn to exactly one validated tool decision.

    ``runner`` takes the composed task text and returns the agent's final reply text; ``None``
    builds the real single-agent runner. A reply that fails validation (parse failure, unknown
    tool, missing/malformed required arg) gets exactly ONE retry with the validation error
    appended to the task; a runner exception/timeout goes straight to the deterministic
    fallback (no retry storm).
    """
    run = runner if runner is not None else _default_runner(config)
    task = _task_text(context, user_text)

    reply = _safe_call(run, task)
    if reply is not None:
        decision, error = _parse_and_validate(reply)
        if decision is not None:
            return decision
        assert error is not None  # decision is None => _parse_and_validate always sets error
        retry_reply = _safe_call(run, _retry_task_text(task, error))
        if retry_reply is not None:
            decision, _error = _parse_and_validate(retry_reply)
            if decision is not None:
                return decision

    return _fallback()
