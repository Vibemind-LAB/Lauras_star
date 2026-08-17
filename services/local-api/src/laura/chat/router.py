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
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypedDict

from ..short_creator.providers import AgentConfig, build_model_client

if TYPE_CHECKING:  # annotation only — never imported at runtime
    from autogen_agentchat.agents import AssistantAgent

logger = logging.getLogger(__name__)

# Wall-clock cap on the real router's single model call (mirrors scout._SCOUT_TIMEOUT_S, but a
# router turn has no tools to iterate on, so the cap can be tighter).
_ROUTER_TIMEOUT_S = 30.0
_MAX_BRIEF_CONTEXT_CHARS = 600

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
        "review_transcript",
        "correct_transcript",
        "confirm_transcript",
        "approve_script",
        "select_scenes",
        "select_visuals",
        "approve_contact_sheet",
        "discuss",
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
    '- switch_project: {"ref": str} — switch to an existing project, by name or id. The '
    'context\'s "Projekte:" line lists every known project (the active one marked with "*"); '
    "when the user's message loosely names one of them — case-insensitive, substring or "
    "word-level, e.g. 'aus Drive Vibemind', 'im Vibemind projekt', 'switch to drive vibemind' "
    "all matching a listed 'Drive VibeMind' — choose switch_project with the EXACT listed name "
    "as ref. NEVER treat a name that matches a listed project as a Google-Drive link or URL "
    "request, even if it contains the word 'Drive' — a listed project name always wins over "
    "propose_import. Example: Projekte contains 'Drive VibeMind' and the user says 'aus Drive "
    'Vibemind\' -> {"tool": "switch_project", "args": {"ref": "Drive VibeMind"}}.\n'
    '- propose_import: {"urls": [str, ...]} — propose importing one or more media URLs (each '
    "must start with 'http'). Never invent URLs the user did not give you.\n"
    '- start_short: {"topic": str, "target_seconds"?: int, "format"?: "insta"|"x"|"linkedin", '
    '"language"?: str} — start building a short about a topic.\n'
    '- start_overview: {"topic": str, "target_seconds"?: int, "language"?: str} — start '
    "building an overview sequence about a topic.\n"
    '- follow_up: {"session_ref": str, "text": str} — send a follow-up instruction to an '
    "existing production session.\n"
    '- revert: {"session_ref": str, "artifact": str, "version": int} — revert a session\'s '
    "artifact to an earlier version.\n"
    '- review_transcript: {"asset_ref": str} — show the transcript of an asset for review. '
    "Example: 'zeig mir das Transkript von Clip 3'.\n"
    '- correct_transcript: {"asset_ref": str, "corrections": [{"segment_index": int, '
    '"text": str}, ...]} — apply one or more text corrections to transcript segments '
    "(segment_index is 1-based). Example: 'ersetze in Segment 3 \"Carpati\" durch "
    "\"Karpathy\"'.\n"
    '- confirm_transcript: {"asset_ref": str} — confirm the transcript is correct as-is, '
    "unlocking downstream steps. Example: 'Transkript passt'.\n"
    '- approve_script: {"session_ref": str} — approve the generated script so production can '
    "proceed. Example: 'Script freigeben'.\n"
    "- select_scenes: der User wählt Szenen für den Szenen-Vorschlag (Gate S). Nur wenn der "
    'Kontext eine Zeile "Szenen-Vorschlag offen" zeigt. args.scene_numbers ist die KOMPLETTE '
    "gewünschte Auswahl. Relativ-Anweisungen gegen die Empfehlung auflösen: bei Empfehlung "
    '[2, 4, 5] heißt "nimm 2 und 5 statt 4" -> [2, 5]; "passt so" / "nimm deine Auswahl" '
    "-> die Empfehlung unverändert.\n"
    "- select_visuals: der User bestätigt die aktuelle Visual-Auswahl. Nur wenn der Kontext "
    'eine Zeile "Visual-Auswahl offen" zeigt. args.proposal_hash muss exakt der dortige '
    "64-stellige Hash sein. Bei Rough-Cut-Auswahlen ist args.selections die KOMPLETTE "
    "geordnete Liste aus rough_cut_order, candidate_id, included und requested_duration_s; "
    "bei alten Beat-Auswahlen bleibt args.selected_candidate_ids die komplette Auswahl. Bei "
    "'deine Auswahl passt' die Empfehlungen aus dem Kontext unverändert übernehmen.\n"
    "- approve_contact_sheet: der User gibt den aktuellen Kontaktbogen frei. Nur wenn der "
    'Kontext eine Zeile "Kontaktbogen-Freigabe offen" zeigt. args.contact_sheet_hash muss '
    "exakt der dortige 64-stellige Hash sein.\n"
    '- discuss: {"text": str} — answer a question, critique, or comment about the result or '
    "process; pass the user's message verbatim as text. Choose this whenever the user is "
    "ASKING or COMPLAINING about the video, its scenes, its wording, or the transcript "
    "quality rather than requesting a specific action. Example: 'warum steht das im "
    "transkript das macht kein sinn' -> discuss (NOT review_transcript — review_transcript "
    "is ONLY for explicitly asking to SEE the transcript, e.g. 'zeig mir das Transkript').\n\n"
    "Rules: reply with EXACTLY one JSON object, no prose before or after it. Never invent "
    "project names, session references, or URLs that were not mentioned in the context or the "
    "user's latest message — ask via reply when unsure.\n"
    " When the context shows an active production session and the message talks about the "
    "RESULT (video, scenes, cut, captions, wording, transcript quality), prefer discuss or "
    "follow_up over asset tools. Adjustment requests are follow_up on the active session — "
    "examples: 'mach Szene 2 kürzer', 'anderes Intro', 'zeig das volle Bild', 'die Captions "
    "sind zu klein', 'mach das in english'. If the user agrees ('ja', 'mach das', 'genau') "
    "right after an assistant "
    "message containing a line starting with 'Vorschlag:', choose follow_up with the active "
    "session and use the text AFTER 'Vorschlag:' as the follow-up text — never the bare "
    "'ja'.\n"
    ' Set "language" on start_short/start_overview to "English" by DEFAULT — every video is '
    "English regardless of the language the instruction itself is written in. ONLY an explicit "
    "target-language mention ('auf Deutsch', 'in Spanish') overrides it (as an English language "
    'name: "German", "Spanish", ...). Examples: \'bau mir einen Short über X\' -> '
    '{"language": "English"}; \'build me a short about X\' -> {"language": "English"}; '
    '\'bau mir einen Short über X auf Deutsch\' -> {"language": "German"}.'
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


_MAX_PROJECTS_LISTED = 15


def _projects_line(
    all_projects: list[dict[str, Any]], active_project: dict[str, Any] | None
) -> str:
    """The 'Projekte:' roster line: every known project's name, ``*``-marked when it is the
    conversation's currently active one, capped at :data:`_MAX_PROJECTS_LISTED`.

    Exists so a loosely mentioned project name ('aus Drive Vibemind') is verifiable against a
    real roster instead of the model guessing — the same rationale as the Videos line below, but
    for projects, and rendered whether or not a project is bound yet (live incident 2026-08-07:
    a fresh, unbound conversation had NO roster to check a mentioned project name against, so
    'aus Drive Vibemind' was misread as a Google-Drive URL request)."""
    active_id = active_project.get("id") if active_project is not None else None
    names = []
    for p in all_projects[:_MAX_PROJECTS_LISTED]:
        name = str(p.get("name") or "?")
        if active_id is not None and p.get("id") == active_id:
            name = f"{name}*"
        names.append(name)
    return "Projekte: " + " | ".join(names)


def compose_context(
    *,
    project: dict[str, Any] | None,
    running_jobs: int,
    messages: list[dict[str, Any]],
    asset_names: list[str] | None = None,
    active_session: dict[str, Any] | None = None,
    all_projects: list[dict[str, Any]] | None = None,
) -> str:
    """Assemble the router's context string: project line, project roster, video roster,
    active-session line, running-jobs line, then the last 20 messages compacted to one line
    each (pure string assembly, no I/O).

    The video roster exists because the router's rules forbid inventing names: without it, an
    asset_ref the user names ('die Bildschirmaufnahme') is unverifiable and the model asks
    back instead of routing review_transcript (seen live 2026-08-05). ``all_projects`` (when
    given) renders one more line right after the Project line via :func:`_projects_line` — the
    SAME rationale, one level up: a loosely mentioned project name needs a real roster to
    resolve against, and this line is rendered even when no project is bound yet (unlike the
    Videos line, which needs an active project to enumerate against).

    ``active_session`` (FE3) grounds follow_up/discuss on the session the thread is actually
    working, instead of the router having to reconstruct it by re-reading compacted action
    cards: ``{"id": ..., "state": ...}`` renders as one line right after the Videos line (or
    right after the Project line when no Videos line was rendered), ``None`` omits it.

    An optional ``active_session["scene_gate"]`` (``{"recommended": [...], "candidates": [...]}``
    — GS4) appends one more line right after the session line, naming the open Gate-S proposal
    so the "select_scenes" rule above has something to key off of; the caller (``api/chat.py``'s
    ``_active_session``) only ever sets it while the gate is actually pending, so its mere
    presence here is the whole condition — this function stays a pure string assembler with no
    I/O of its own."""
    lines: list[str] = []
    if project is not None:
        name = project.get("name") or "?"
        project_id = project.get("id") or "?"
        lines.append(f"Project: {name} (id={project_id})")
    else:
        lines.append("Project: none selected")
    if all_projects:
        lines.append(_projects_line(all_projects, project))
    if project is not None and asset_names:
        lines.append("Videos: " + ", ".join(asset_names[:20]))
    if active_session is not None:
        lines.append(
            f"Active production session: {active_session['id']} "
            f"({active_session['state']})"
        )
        brief = " ".join(str(active_session.get("brief") or "").split())
        if brief:
            lines.append(
                "Original production brief: " + brief[:_MAX_BRIEF_CONTEXT_CHARS]
            )
        scene_gate = active_session.get("scene_gate")
        if isinstance(scene_gate, dict):
            recommended = scene_gate.get("recommended") or []
            candidates = scene_gate.get("candidates") or []
            lines.append(
                f"Szenen-Vorschlag offen: empfohlen {recommended} von Kandidaten {candidates}"
            )
        visual_gate = active_session.get("visual_selection_gate")
        if isinstance(visual_gate, dict):
            proposal_hash = visual_gate.get("proposal_hash")
            if isinstance(proposal_hash, str) and proposal_hash:
                recommended_selections = visual_gate.get("recommended_selections")
                if isinstance(recommended_selections, list) and recommended_selections:
                    lines.append(
                        f"Visual-Auswahl offen: proposal_hash={proposal_hash} "
                        "empfohlene Szenenentscheidungen "
                        f"{recommended_selections}"
                    )
                else:
                    recommended = visual_gate.get("recommended_candidate_ids") or []
                    lines.append(
                        f"Visual-Auswahl offen: proposal_hash={proposal_hash} "
                        f"empfohlen {recommended}"
                    )
        contact_sheet_gate = active_session.get("contact_sheet_gate")
        if isinstance(contact_sheet_gate, dict):
            contact_sheet_hash = contact_sheet_gate.get("contact_sheet_hash")
            if isinstance(contact_sheet_hash, str) and contact_sheet_hash:
                lines.append(
                    "Kontaktbogen-Freigabe offen: "
                    f"contact_sheet_hash={contact_sheet_hash}"
                )
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

# Floor is 2 chars (one required leading letter + at least one more), matching BoardMeta's
# own min_length=2 — a 1-char value ("E") used to pass this regex, sail past the HTTP request
# model's min_length (bypassed on the chat path), and reach an UNGUARDED BoardMeta(...)
# construction in the background job, where its ValidationError corpses the whole session.
_LANGUAGE_RE = re.compile(r"^[A-Za-z][A-Za-z ]{1,31}$")


def _validate_optional_language(args: dict[str, Any]) -> str | None:
    if "language" not in args:
        return None
    language = args["language"]
    if not isinstance(language, str) or _LANGUAGE_RE.fullmatch(language) is None:
        return (
            "language must be an English language name (letters/spaces, 2-32 chars), "
            'e.g. "German" or "English"'
        )
    return None


def _validate_correction_item(item: Any) -> str | None:
    """One ``corrections[]`` entry: ``segment_index`` (int >= 1, not bool — mirrors
    ``revert.version``) and a non-empty ``text``."""
    if not isinstance(item, dict):
        return "correct_transcript.corrections items must be objects"
    segment_index = item.get("segment_index")
    if (
        not isinstance(segment_index, int)
        or isinstance(segment_index, bool)
        or segment_index < 1
    ):
        return "correct_transcript.corrections[].segment_index must be an integer >= 1"
    text = item.get("text")
    if not isinstance(text, str) or not text.strip():
        return "correct_transcript.corrections[].text is missing or not a non-empty string"
    return None


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
        error = _validate_optional_language(args)
        if error is not None:
            return error
        return None

    if tool == "start_overview":
        error = _require_str(args, "topic")
        if error is not None:
            return error
        error = _validate_optional_target_seconds(args)
        if error is not None:
            return error
        return _validate_optional_language(args)

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

    if tool == "review_transcript":
        return _require_str(args, "asset_ref")

    if tool == "correct_transcript":
        error = _require_str(args, "asset_ref")
        if error is not None:
            return error
        corrections = args.get("corrections")
        if not isinstance(corrections, list) or not corrections:
            return "correct_transcript.corrections is missing, not a list, or empty"
        for item in corrections:
            error = _validate_correction_item(item)
            if error is not None:
                return error
        return None

    if tool == "confirm_transcript":
        return _require_str(args, "asset_ref")

    if tool == "approve_script":
        return _require_str(args, "session_ref")

    if tool == "select_scenes":
        numbers = args.get("scene_numbers")
        if not isinstance(numbers, list) or not numbers:
            return "select_scenes.scene_numbers is missing, not a list, or empty"
        if not all(
            isinstance(n, int) and not isinstance(n, bool) and n >= 1 for n in numbers
        ):
            return "select_scenes.scene_numbers must all be integers >= 1"
        return None

    if tool == "select_visuals":
        proposal_hash = args.get("proposal_hash")
        if (
            not isinstance(proposal_hash, str)
            or re.fullmatch(r"[0-9a-f]{64}", proposal_hash) is None
        ):
            return "select_visuals.proposal_hash must be a lowercase hexadecimal SHA-256 hash"
        has_selections = "selections" in args
        has_candidate_ids = "selected_candidate_ids" in args
        if has_selections == has_candidate_ids:
            return (
                "select_visuals requires selections for v2 or selected_candidate_ids for v1"
            )
        if has_selections:
            selections = args.get("selections")
            if not isinstance(selections, list) or not selections:
                return "select_visuals.selections is missing, not a list, or empty"
            required = {
                "rough_cut_order",
                "candidate_id",
                "included",
                "requested_duration_s",
            }
            for selection in selections:
                if not isinstance(selection, dict) or set(selection) != required:
                    return "select_visuals.selections must contain exact scene decisions"
                rough_cut_order = selection.get("rough_cut_order")
                if (
                    not isinstance(rough_cut_order, int)
                    or isinstance(rough_cut_order, bool)
                    or rough_cut_order < 0
                ):
                    return "select_visuals.selections rough_cut_order must be an integer >= 0"
                candidate_id = selection.get("candidate_id")
                if not isinstance(candidate_id, str) or not candidate_id:
                    return "select_visuals.selections candidate_id must be a non-empty string"
                if not isinstance(selection.get("included"), bool):
                    return "select_visuals.selections included must be a boolean"
                duration = selection.get("requested_duration_s")
                if (
                    not isinstance(duration, int)
                    or isinstance(duration, bool)
                    or not 1 <= duration <= 10
                ):
                    return (
                        "select_visuals.selections requested_duration_s must be an integer 1-10"
                    )
            return None
        candidate_ids = args.get("selected_candidate_ids")
        if not isinstance(candidate_ids, list) or not candidate_ids:
            return (
                "select_visuals.selected_candidate_ids is missing, not a list, or empty"
            )
        if not all(
            isinstance(candidate_id, str) and candidate_id
            for candidate_id in candidate_ids
        ):
            return "select_visuals.selected_candidate_ids must all be non-empty strings"
        return None

    if tool == "approve_contact_sheet":
        contact_sheet_hash = args.get("contact_sheet_hash")
        if not isinstance(contact_sheet_hash, str) or len(contact_sheet_hash) != 64:
            return "approve_contact_sheet.contact_sheet_hash must be a 64-character string"
        return None

    if tool == "discuss":
        return _require_str(args, "text")

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


def _normalize(parsed: dict[str, Any]) -> dict[str, Any]:
    """Accept the model's most common shape drift — ``{"<tool>": {...args}}`` instead of
    ``{"tool": ..., "args": ...}`` (seen live from gpt-4o 2026-08-05, on both attempts of a
    turn) — by rewriting it before validation. Anything else passes through untouched."""
    if "tool" not in parsed and len(parsed) == 1:
        [(key, value)] = parsed.items()
        if key in TOOLS and isinstance(value, dict):
            return {"tool": key, "args": value}
    return parsed


def _parse_and_validate(reply: str) -> tuple[RouterDecision | None, str | None]:
    parsed = _parse(reply)
    if parsed is None:
        return None, "no JSON object found in the reply"
    return _validate(_normalize(parsed))


def _fallback() -> RouterDecision:
    """The deterministic fallback: a ``reply`` asking the user to rephrase — the router must
    never leave a turn without SOME answer."""
    return {"tool": "reply", "args": {"text": _FALLBACK_TEXT}, "fallback": True}


# --- the real single-agent runner (autogen-touching) -------------------------------------------


def _build_router_agent(
    config: AgentConfig, *, system_message: str = _SYSTEM_PROMPT
) -> AssistantAgent:
    """One tool-less ``AssistantAgent`` that answers a one-shot task (lazy autogen import,
    mirrors :func:`laura.short_creator.scout._build_scout_agent`).

    ``system_message`` defaults to the router's own JSON-tool-call prompt (:data:`_SYSTEM_PROMPT`)
    so every pre-C1 caller keeps its exact behavior; :func:`build_one_shot_runner` overrides it
    for callers that need a DIFFERENT persona on the same one-shot machinery (the chat/executor.py
    discuss handler, spec 2026-08-05 final review C1) — a plain grounded-answer task must never
    run under the router's "reply with EXACTLY one JSON object" instructions."""
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
        system_message=system_message,
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


def _default_runner(
    config: AgentConfig, *, system_message: str = _SYSTEM_PROMPT
) -> Callable[[str], str]:
    """The real runner: builds one tool-less ``AssistantAgent`` and runs it with a wall-clock
    cap. Any failure (missing extra, model error, timeout) raises out of ``run`` —
    :func:`run_router` treats every runner exception the same way: straight to the
    deterministic fallback. ``system_message`` defaults to the router's own prompt so
    ``run_router``'s own ``runner=None`` path is unchanged."""

    def run(task: str) -> str:
        async def _run() -> str:
            agent = _build_router_agent(config, system_message=system_message)
            result = await agent.run(task=task)
            return _last_message_text(result)

        return asyncio.run(asyncio.wait_for(_run(), _ROUTER_TIMEOUT_S))

    return run


def build_one_shot_runner(
    config: AgentConfig, *, system_message: str = _SYSTEM_PROMPT
) -> Callable[[str], str]:
    """Public facade over the one-shot agent runner — the discuss handler (chat/executor.py)
    runs its grounded answer through the same single-agent, wall-clock-capped machinery
    instead of growing a second LLM client path.

    ``system_message`` defaults to the router's own JSON-tool-call prompt so an unqualified
    call keeps its pre-C1 behavior, but a caller running a DIFFERENT one-shot task (discuss's
    grounded-answer persona, not a tool router) must pass its own system message — otherwise
    that task runs under instructions demanding "reply with EXACTLY one JSON object", which is
    exactly the C1 bug this seam exists to prevent. The returned callable carries the resolved
    ``system_message`` as a ``.system_message`` attribute so a caller/test can confirm which
    prompt a given runner was built with without re-deriving it."""
    runner = _default_runner(config, system_message=system_message)
    runner.system_message = system_message  # type: ignore[attr-defined]
    return runner


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
            decision, retry_error = _parse_and_validate(retry_reply)
            if decision is not None:
                return decision
            # Both attempts produced an invalid reply — without this line the turn degrades
            # to the clarify fallback with zero trace (cost a live-debugging session to find).
            logger.warning(
                "chat router reply failed validation twice; falling back (last error: %s)",
                retry_error,
            )

    return _fallback()
