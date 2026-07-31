"""The overview scout: one agent picks and orders pre-built candidate windows — or a
deterministic fallback does (spec 2026-07-31-auto-overview-design.md §4).

The agent answers with INDICES into the candidate list, never with frame numbers. The
candidates were built deterministically (:mod:`.overview_windows`), so a selection can be
wrong about taste but never about time. That split is the NL-short-creator's own lesson:
with small models, every contract belongs in code, not in a conditional prompt rule.

Hardened like :mod:`.scout`, whose shape survived live use: validate -> exactly ONE retry
with the concrete error -> deterministic fallback. A runner exception, a timeout and an
infrastructure failure during validation all land in the same place — the endpoint always
gets an answer.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypedDict

from .overview_windows import Candidate, trim_to_target
from .providers import AgentConfig, build_model_client

if TYPE_CHECKING:  # annotation only — never imported at runtime
    from autogen_agentchat.agents import AssistantAgent

logger = logging.getLogger(__name__)

_SCOUT_TIMEOUT_S = 60.0


class OverviewDecision(TypedDict):
    """The scout's answer, adopted or fallback — what the auto-overview endpoint consumes."""

    clips: list[Candidate]
    rationale: str
    fallback: bool


# --- task text (pure) --------------------------------------------------------------------------


def _task_text(
    topic: str,
    candidates: list[Candidate],
    target_seconds: int,
    fps_by_asset: dict[str, tuple[int, int]],
) -> str:
    sources = sorted({c.display_name for c in candidates})
    lines = [
        f'Topic: "{topic}"',
        f"Target length: about {target_seconds} seconds.",
        "",
        f"Candidate clips from {len(sources)} video(s), already cut to watchable length:",
    ]
    for index, candidate in enumerate(candidates):
        # Each clip's own rate — a hardcoded 30 would misreport every 25fps or 29.97 source.
        fps_num, fps_den = fps_by_asset.get(candidate.asset_id, (25, 1))
        seconds = candidate.length_frames * fps_den / fps_num
        lines.append(
            f"  [{index}] {candidate.display_name!r} scene {candidate.scene_number} "
            f"(~{seconds:.0f}s): \"{candidate.snippet}\""
        )
    lines += [
        "",
        "Choose the clips that together give the best OVERVIEW of the topic, and put them in "
        "the order they should be watched. Cover at least two different videos when the list "
        "offers more than one — an overview drawn from a single source is just a long clip.",
        "Answer with EXACTLY one JSON object as your final message, nothing before or after "
        "it. Use the clip NUMBERS shown in brackets:",
        '{"clips": [<index>, ...], "rationale": "<1-3 sentences on why these clips and this '
        'order>"}',
    ]
    return "\n".join(lines)


def _retry_task_text(task: str, error: str) -> str:
    return (
        f"{task}\n\n"
        f"Your previous reply was invalid: {error}. Reply again with ONE corrected JSON "
        "object as specified above — every entry of \"clips\" must be one of the candidate "
        "numbers in brackets, each used at most once."
    )


# --- reply parsing + validation (pure) ----------------------------------------------------------


def _last_json_object(text: str) -> dict[str, Any] | None:
    """The LAST complete top-level ``{...}`` object in *text*, or ``None`` (mirrors
    :func:`.scout._last_json_object`: an agent's answer is the last JSON block in its reply)."""
    decoder = json.JSONDecoder()
    candidate: dict[str, Any] | None = None
    for start, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            candidate = obj
    return candidate


def _validate_reply(
    candidates: list[Candidate], reply: str
) -> tuple[OverviewDecision | None, str | None]:
    """Parse + validate *reply* against the candidate list.

    Returns ``(decision, None)`` when good, else ``(None, error)`` — *error* goes into the
    retry task, so it stays short and names exactly what was wrong.
    """
    parsed = _last_json_object(reply)
    if parsed is None:
        return None, "no JSON object found in the reply"

    clips = parsed.get("clips")
    rationale = parsed.get("rationale")

    if not isinstance(clips, list) or not clips:
        return None, "clips is missing, not a list, or empty"
    if not all(isinstance(i, int) and not isinstance(i, bool) for i in clips):
        return None, "clips must be a list of integers"
    if not isinstance(rationale, str) or not rationale.strip():
        return None, "rationale is missing or not a string"

    out_of_range = sorted({i for i in clips if not 0 <= i < len(candidates)})
    if out_of_range:
        return None, f"{out_of_range} is not a candidate index (0..{len(candidates) - 1})"
    if len(set(clips)) != len(clips):
        return None, "the same clip number appears more than once"

    chosen = [candidates[i] for i in clips]
    available_assets = {c.asset_id for c in candidates}
    if len(available_assets) > 1 and len({c.asset_id for c in chosen}) < 2:
        return None, (
            "the selection uses only one video although several have material — an overview "
            "must cover at least two"
        )

    return (
        {"clips": chosen, "rationale": rationale.strip(), "fallback": False},
        None,
    )


def _fallback(candidates: list[Candidate]) -> OverviewDecision:
    """Deterministic: the candidates in their own order (assets by search score, chronological
    within an asset) — the scout must never leave the endpoint without SOME answer."""
    return {
        "clips": list(candidates),
        "rationale": "automatic fallback: top search scores",
        "fallback": True,
    }


# --- the real single-agent runner (autogen-touching) --------------------------------------------


def _build_scout_agent(config: AgentConfig) -> AssistantAgent:
    """One ``AssistantAgent``, no tools: everything it needs is in the task text (lazy autogen
    import, mirrors :func:`.scout._build_scout_agent`)."""
    try:
        from autogen_agentchat.agents import AssistantAgent
    except ImportError as exc:
        raise RuntimeError(
            "The short-creator needs the optional 'autoshort' extra. "
            "Install it with: uv sync --extra autoshort"
        ) from exc

    model_client = build_model_client(config, role="agent")
    return AssistantAgent(
        name="overview_scout",
        model_client=model_client,
        description="Picks and orders the clips of a multi-video overview.",
        system_message=(
            "You are the Overview Scout. Answer in the task's language — never switch "
            "languages. Given a topic and a numbered list of candidate clips, choose the ones "
            "that together explain the topic best and put them in a sensible watching order. "
            "Reply with EXACTLY one JSON object as your final message — nothing before or "
            "after it."
        ),
    )


def _last_message_text(result: Any) -> str:
    """The LAST non-empty message's text from a ``TaskResult`` (mirrors
    :func:`.scout._last_message_text`: messages[0] echoes the task itself)."""
    for msg in reversed(getattr(result, "messages", None) or []):
        to_text = getattr(msg, "to_model_text", None)
        text = (to_text() if callable(to_text) else str(getattr(msg, "content", ""))).strip()
        if text:
            return text
    return ""


def _default_runner(config: AgentConfig) -> Callable[[str], str]:
    def run(task: str) -> str:
        async def _run() -> str:
            agent = _build_scout_agent(config)
            result = await agent.run(task=task)
            return _last_message_text(result)

        return asyncio.run(asyncio.wait_for(_run(), _SCOUT_TIMEOUT_S))

    return run


# --- orchestration ------------------------------------------------------------------------------


def _safe_call(run: Callable[[str], str], task: str) -> str | None:
    try:
        return run(task)
    except Exception:  # noqa: BLE001 — any runner failure degrades to the fallback
        logger.warning("overview scout runner failed; falling back", exc_info=True)
        return None


def _safe_validate(
    candidates: list[Candidate], reply: str
) -> tuple[OverviewDecision | None, str | None]:
    try:
        return _validate_reply(candidates, reply)
    except Exception as exc:  # noqa: BLE001 — must not escape run_overview_scout
        logger.warning("overview scout validation failed; treating as invalid", exc_info=True)
        return None, f"validation failed: {exc}"


def run_overview_scout(
    config: AgentConfig,
    *,
    topic: str,
    candidates: list[Candidate],
    target_seconds: int,
    fps_by_asset: dict[str, tuple[int, int]],
    runner: Callable[[str], str] | None = None,
) -> OverviewDecision:
    """Pick and order the overview's clips out of *candidates*.

    ``runner`` takes the composed task text and returns the agent's final reply; ``None``
    builds the real one. An invalid reply gets exactly ONE retry with the error appended; a
    runner exception goes straight to the fallback (no retry storm). The result is trimmed to
    ``target_seconds`` either way. *candidates* empty is a programming error — the endpoint
    guards it with a 422 before calling — and raises ``ValueError``.
    """
    if not candidates:
        raise ValueError("candidates is empty — nothing for the overview scout to choose from")

    run = runner if runner is not None else _default_runner(config)
    task = _task_text(topic, candidates, target_seconds, fps_by_asset)

    decision: OverviewDecision | None = None
    reply = _safe_call(run, task)
    if reply is not None:
        decision, error = _safe_validate(candidates, reply)
        if decision is None:
            assert error is not None  # decision is None => _safe_validate always sets error
            retry_reply = _safe_call(run, _retry_task_text(task, error))
            if retry_reply is not None:
                decision, _error = _safe_validate(candidates, retry_reply)

    if decision is None:
        decision = _fallback(candidates)

    decision["clips"] = trim_to_target(
        decision["clips"], target_seconds=target_seconds, fps_by_asset=fps_by_asset
    )
    return decision
