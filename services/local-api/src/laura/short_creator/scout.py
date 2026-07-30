"""The scout: one lightweight agent picks the best asset + scenes for a topic — or a
deterministic fallback does, so the auto-short endpoint can never die on an agent's whim
(spec 2026-07-21-auto-short-design.md §2).

The scout runs BEFORE any production session/board exists (the chicken-and-egg problem the
design rejected the alternative for): it takes :mod:`discovery`'s ranking, asks a single
``AssistantAgent`` to pick one asset and its most relevant scenes, and VALIDATES the answer
against the real data rather than trusting it. An invalid answer (parse failure, an asset id
that is not in the ranking, or scene numbers the asset does not have) gets exactly ONE retry
with the validation error appended to the task; still invalid, or the runner raising/timing
out, lands on a deterministic fallback — the ranking's top-scored asset and its scene hits.
``runner`` is injectable so tests never touch a real LLM; ``None`` builds the real single-agent
runner (mirrors :mod:`.agents`/:mod:`.production_agents`'s AssistantAgent + FunctionTool
pattern, but ONE agent, not a team).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypedDict

from ..db import repos
from ..db.database import Database
from . import context, discovery
from .providers import AgentConfig, build_model_client

if TYPE_CHECKING:  # annotation only — never imported at runtime
    from autogen_agentchat.agents import AssistantAgent

logger = logging.getLogger(__name__)

# Wall-clock cap on the real scout's single tool-call loop (spec §2: "Timeout-begrenzt").
_SCOUT_TIMEOUT_S = 60.0
_SCOUT_MAX_TOOL_ITERATIONS = 4


class ScoutDecision(TypedDict):
    """The scout's answer, adopted or fallback — the shape the auto-short endpoint consumes."""

    asset_id: str
    scene_numbers: list[int]
    rationale: str
    fallback: bool


# --- task text (pure) --------------------------------------------------------------------------


def _task_text(topic: str, ranking: list[dict[str, Any]]) -> str:
    """The task the scout sees: topic + the FULL ranking embedded — tools are optional depth,
    never required to answer well."""
    lines = [
        f'Topic: "{topic}"',
        "",
        "Candidate material, ranked by search score (asset id, name, score, top scene hits):",
    ]
    for entry in ranking:
        lines.append(
            f"- asset_id={entry['asset_id']} name={entry['display_name']!r} "
            f"score={float(entry['score']):.2f}"
        )
        for hit in entry["scene_hits"]:
            lines.append(
                f"    scene {hit['scene_number']} (score={float(hit['score']):.2f}): "
                f'"{hit["snippet"]}"'
            )
    lines += [
        "",
        "Pick the ONE best asset and the scene numbers (from that asset's rough cut) most "
        "relevant to the topic. You may call search_material, list_project_assets or "
        "get_scene_context for more depth, but the ranking above already has everything you "
        "need for a good answer — the tools are optional.",
        "Answer with EXACTLY one JSON object as your final message, nothing before or after "
        "it:",
        '{"asset_id": "<asset id>", "scene_numbers": [<int>, ...], "rationale": '
        '"<1-3 sentences why this asset and these scenes fit the topic>"}',
    ]
    return "\n".join(lines)


def _retry_task_text(task: str, error: str) -> str:
    return (
        f"{task}\n\n"
        f"Your previous reply was invalid: {error}. Reply again with ONE corrected JSON "
        "object as specified above — asset_id must be one of the candidate asset ids listed "
        "above, and scene_numbers must be real scene numbers of that asset."
    )


# --- reply parsing + validation (pure given db reads) -------------------------------------------


def _last_json_object(text: str) -> dict[str, Any] | None:
    """The LAST complete top-level ``{...}`` object found in *text*, or ``None``.

    Scans every ``{`` left to right and keeps the latest successful parse — an agent's final
    answer is expected to be the last JSON block in its reply (any earlier prose/example stays
    ignored).
    """
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


def _known_scene_numbers(
    db: Database, project_id: str, asset_id: str, ranking_entry: dict[str, Any] | None
) -> set[int]:
    """The asset's known scene universe: the ranking's own scene_hits PLUS the asset's real
    rough-cut scenes (:func:`discovery._scene_ranges`) — an agent may legitimately pick a scene
    the search snippets never surfaced."""
    known: set[int] = set()
    if ranking_entry is not None:
        known.update(int(hit["scene_number"]) for hit in ranking_entry["scene_hits"])
    ranges = discovery._scene_ranges(db, project_id, asset_id)
    if ranges is not None:
        known.update(scene_number for scene_number, _start, _end_exclusive in ranges)
    return known


def _validate_reply(
    db: Database, project_id: str, ranking: list[dict[str, Any]], reply: str
) -> tuple[ScoutDecision | None, str | None]:
    """Parse + validate *reply* against the real ranking/scene data.

    Returns ``(decision, None)`` when the reply is good, else ``(None, error)`` — *error* is
    meant to be appended to a retry task, so it stays short and names exactly what was wrong.
    """
    parsed = _last_json_object(reply)
    if parsed is None:
        return None, "no JSON object found in the reply"

    asset_id = parsed.get("asset_id")
    scene_numbers = parsed.get("scene_numbers")
    rationale = parsed.get("rationale")

    if not isinstance(asset_id, str) or not asset_id:
        return None, "asset_id is missing or not a string"
    if not isinstance(scene_numbers, list) or not scene_numbers:
        return None, "scene_numbers is missing, not a list, or empty"
    if not all(isinstance(n, int) and not isinstance(n, bool) for n in scene_numbers):
        return None, "scene_numbers must be a list of integers"
    if not isinstance(rationale, str) or not rationale.strip():
        return None, "rationale is missing or not a string"

    by_asset = {str(entry["asset_id"]): entry for entry in ranking}
    entry = by_asset.get(asset_id)
    if entry is None:
        return None, f"asset_id {asset_id!r} is not one of the candidate assets"

    known = _known_scene_numbers(db, project_id, asset_id, entry)
    chosen = {int(n) for n in scene_numbers}
    unknown = sorted(chosen - known)
    if unknown:
        return None, f"scene numbers {unknown} are not real scenes of asset {asset_id!r}"

    return (
        {
            "asset_id": asset_id,
            "scene_numbers": [int(n) for n in scene_numbers],
            "rationale": rationale.strip(),
            "fallback": False,
        },
        None,
    )


def _fallback(ranking: list[dict[str, Any]]) -> ScoutDecision:
    """The deterministic fallback: the ranking's top-scored asset and its own scene hits —
    the scout must never leave the endpoint without SOME answer."""
    top = ranking[0]
    return {
        "asset_id": str(top["asset_id"]),
        "scene_numbers": [int(hit["scene_number"]) for hit in top["scene_hits"]],
        "rationale": "automatic fallback: top search score",
        "fallback": True,
    }


# --- read-only scene context (the scout must never write) ---------------------------------------


def _scene_context_readonly(
    db: Database, project_id: str, asset_id: str, scene_number: int
) -> dict[str, Any]:
    """The scout's ``get_scene_context`` tool, built strictly READ-ONLY: an asset without a
    rough cut yet returns ``{"ok": False, "reason": "no rough cut"}`` instead of creating one —
    the scout runs BEFORE any production session/board exists, and probing it must never leave
    a timeline behind (mirrors :func:`production_tools._resolve_scene`'s composition, but built
    on :func:`discovery._scene_ranges`, which is the same read-only lookup discovery's ranking
    already uses).
    """
    ranges = discovery._scene_ranges(db, project_id, asset_id)
    if ranges is None:
        return {"ok": False, "reason": "no rough cut"}
    match = next((r for r in ranges if r[0] == scene_number), None)
    if match is None:
        return {"ok": False, "reason": "unknown scene"}
    _scene_number, src_start, src_end_exclusive = match
    run = repos.get_latest_analysis_run(db, asset_id)
    segments = repos.get_transcript(db, asset_id, str(run["id"])) if run is not None else []
    in_scene = context._segments_in_ranges(segments, [(src_start, src_end_exclusive)])
    text = " ".join(str(seg.get("text") or "").strip() for seg in in_scene).strip()
    return {
        "ok": True,
        "asset_id": asset_id,
        "scene_number": scene_number,
        "src_start_frame": src_start,
        "src_end_frame_exclusive": src_end_exclusive,
        "text": text,
    }


# --- the real single-agent runner (autogen-touching) --------------------------------------------


def _build_scout_agent(db: Database, config: AgentConfig, project_id: str) -> AssistantAgent:
    """One ``AssistantAgent`` with the scout's three tools (lazy autogen import, mirrors
    :func:`.agents.build_agents` / :func:`.production_agents.build_production_team`)."""
    try:
        from autogen_agentchat.agents import AssistantAgent
        from autogen_core.tools import FunctionTool
    except ImportError as exc:
        raise RuntimeError(
            "The short-creator needs the optional 'autoshort' extra. "
            "Install it with: uv sync --extra autoshort"
        ) from exc

    def search_material(topic: str) -> dict[str, Any]:
        """Topic -> ranked candidate assets and their rough-cut scene hits, across the whole
        project (the same ranking already embedded in your task)."""
        return discovery.search_material(db, project_id, topic)

    def list_project_assets() -> list[dict[str, Any]]:
        """List the project's media assets (asset_id + display_name)."""
        return [
            {"asset_id": str(asset["id"]), "display_name": str(asset.get("display_name") or "")}
            for asset in repos.list_assets(db, project_id)
        ]

    def get_scene_context(asset_id: str, scene_number: int) -> dict[str, Any]:
        """A rough-cut scene's transcript text and source frame range for an asset (no VLM
        call, READ-ONLY — an asset without a rough cut yet is reported, never created)."""
        try:
            return _scene_context_readonly(db, project_id, asset_id, scene_number)
        except Exception as exc:  # tool must never kill the agent loop
            return {"ok": False, "reason": str(exc)[:200]}

    # list[Any]: AssistantAgent wants list[BaseTool | Callable] (invariant); FunctionTool is a
    # concrete subtype, so a plain list[FunctionTool] bound to a variable first (rather than
    # passed inline) fails mypy's invariance check (same reasoning as production_agents.py's
    # ``agents: list[Any]``).
    tools: list[Any] = [
        FunctionTool(
            search_material, name="search_material", description=(search_material.__doc__ or "")
        ),
        FunctionTool(
            list_project_assets,
            name="list_project_assets",
            description=(list_project_assets.__doc__ or ""),
        ),
        FunctionTool(
            get_scene_context,
            name="get_scene_context",
            description=(get_scene_context.__doc__ or ""),
        ),
    ]
    model_client = build_model_client(config, role="agent")
    return AssistantAgent(
        name="scout",
        model_client=model_client,
        tools=tools,
        description="Picks the best asset and scenes for a topic from ranked search material.",
        system_message=(
            "You are the Scout. Answer in the task's language — never switch languages. Given "
            "a topic and ranked candidate material, choose the single best asset and its most "
            "relevant scene numbers, and say why in 1-3 sentences. Reply with EXACTLY one JSON "
            "object as your final message — nothing before or after it."
        ),
        max_tool_iterations=_SCOUT_MAX_TOOL_ITERATIONS,
    )


def _last_message_text(result: Any) -> str:
    """The LAST non-empty message's text from a ``TaskResult`` (mirrors
    ``production_orchestrator._parse_outcome``: concatenating every message would put the task
    text itself — ``messages[0]`` echoes it — into the reply)."""
    for msg in reversed(getattr(result, "messages", None) or []):
        to_text = getattr(msg, "to_model_text", None)
        text = (to_text() if callable(to_text) else str(getattr(msg, "content", ""))).strip()
        if text:
            return text
    return ""


def _default_runner(db: Database, config: AgentConfig, project_id: str) -> Callable[[str], str]:
    """The real runner: builds one AssistantAgent and runs it with a wall-clock cap.

    Any failure (missing extra, model error, timeout) raises out of ``run`` — :func:`run_scout`
    treats every runner exception the same way: straight to the deterministic fallback.
    """

    def run(task: str) -> str:
        async def _run() -> str:
            agent = _build_scout_agent(db, config, project_id)
            result = await agent.run(task=task)
            return _last_message_text(result)

        return asyncio.run(asyncio.wait_for(_run(), _SCOUT_TIMEOUT_S))

    return run


# --- orchestration (pure given db reads + the injected/real runner) -----------------------------


def _safe_call(run: Callable[[str], str], task: str) -> str | None:
    """Run *run*, converting any exception (including a timeout) into ``None`` — the scout must
    never let a runner failure escape as an exception (spec §2: "stirbt nie an Agenten-Launen")."""
    try:
        return run(task)
    except Exception:  # noqa: BLE001 — any runner failure degrades to the fallback
        logger.warning("scout runner failed; falling back", exc_info=True)
        return None


def _safe_validate(
    db: Database, project_id: str, ranking: list[dict[str, Any]], reply: str
) -> tuple[ScoutDecision | None, str | None]:
    """``_validate_reply``, but any infra failure underneath it (a torn-down rough cut, a bad
    timeline row under ``discovery._scene_ranges``) degrades to a validation failure instead of
    escaping run_scout — the "never dies" guarantee covers reads done DURING validation too, not
    only the runner call itself."""
    try:
        return _validate_reply(db, project_id, ranking, reply)
    except Exception as exc:  # noqa: BLE001 — an infra failure here must not escape run_scout
        logger.warning("scout validation hit an infra error; treating as invalid", exc_info=True)
        return None, f"validation failed: {exc}"


def run_scout(
    db: Database,
    config: AgentConfig,
    *,
    project_id: str,
    topic: str,
    material: dict[str, Any],
    runner: Callable[[str], str] | None = None,
) -> ScoutDecision:
    """Pick an asset + scenes for *topic* out of *material* (Task 1's ``search_material`` shape).

    ``runner`` takes the composed task text and returns the agent's final reply text; ``None``
    builds the real single-agent runner. A reply that fails validation (parse failure, unknown
    asset id, unknown scene numbers) gets exactly ONE retry with the validation error appended
    to the task; a runner exception/timeout goes straight to the deterministic fallback (no
    retry storm). ``material["ranking"]`` empty is a programming error (the endpoint guards it
    with a 422 before ever calling the scout) — raises ``ValueError``.
    """
    ranking = material["ranking"]
    if not ranking:
        raise ValueError("material['ranking'] is empty — nothing for the scout to choose from")

    run = runner if runner is not None else _default_runner(db, config, project_id)
    task = _task_text(topic, ranking)

    reply = _safe_call(run, task)
    if reply is not None:
        decision, error = _safe_validate(db, project_id, ranking, reply)
        if decision is not None:
            return decision
        assert error is not None  # decision is None => _safe_validate always sets error
        retry_reply = _safe_call(run, _retry_task_text(task, error))
        if retry_reply is not None:
            decision, _error = _safe_validate(db, project_id, ranking, retry_reply)
            if decision is not None:
                return decision

    return _fallback(ranking)
