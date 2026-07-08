"""The escalation ladder: run the short-creator, falling back and escalating as configured.

Stage A (local provider): Magentic-One → GraphFlow on hard failure. Stage B (escalate provider,
e.g. 9router): the same, entered when Stage A hard-fails, or when Stage A is soft-weak AND
``LAURA_AGENT_AUTO_ESCALATE`` is on. Team execution is injectable (``execute``) so the ladder logic
is tested without an LLM; the default executor builds + runs the real AutoGen team and parses its
messages (manual-to-verify — no model in CI).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from ..db.database import Database
from . import graph, magentic
from .providers import AgentConfig, Stage

logger = logging.getLogger(__name__)

Status = Literal["ok", "hard_fail"]
TeamKind = Literal["magentic", "graph"]


@dataclass(frozen=True)
class StageOutcome:
    """The result of running one team at one stage."""

    status: Status
    weak: bool
    summary: str
    team: TeamKind
    stage: Stage


ExecuteFn = Callable[[Database, AgentConfig, Stage, TeamKind, str], StageOutcome]


async def _run_team(team: Any, task: str) -> Any:
    return await team.run(task=task)


def _parse_result(result: Any, *, kind: TeamKind, stage: Stage) -> StageOutcome:
    """Read the team's final messages into an outcome.

    ``weak`` reads ONLY the qa agent's messages (the task prompt itself contains the word
    "weak", so scanning the whole transcript would flag every run); a run in which qa never
    spoke was never validated and is weak too.
    """
    text = ""
    qa_text = ""
    for msg in getattr(result, "messages", None) or []:
        to_text = getattr(msg, "to_model_text", None)
        line = (to_text() if callable(to_text) else str(getattr(msg, "content", ""))) + "\n"
        text += line
        if str(getattr(msg, "source", "")) == "qa":
            qa_text += line
    weak = ("weak" in qa_text.lower()) if qa_text.strip() else True
    return StageOutcome(
        status="ok",
        weak=weak,
        summary=text.strip()[:2000],
        team=kind,
        stage=stage,
    )


def _default_execute(
    db: Database, config: AgentConfig, stage: Stage, kind: TeamKind, task: str
) -> StageOutcome:
    """Build + run the real AutoGen team for *kind* at *stage*; any failure is a hard fail."""
    import asyncio  # local: only the real run needs the event loop

    try:
        team = (
            magentic.build_magentic_team(db, config, stage=stage)
            if kind == "magentic"
            else graph.build_graph_team(db, config, stage=stage)
        )
        result = asyncio.run(_run_team(team, task))
    except Exception as exc:
        logger.warning("%s team failed at stage %s: %s", kind, stage, exc)
        return StageOutcome(
            status="hard_fail", weak=False, summary=str(exc), team=kind, stage=stage
        )
    return _parse_result(result, kind=kind, stage=stage)


def _safe_execute(
    execute: ExecuteFn,
    db: Database,
    config: AgentConfig,
    stage: Stage,
    kind: TeamKind,
    task: str,
) -> StageOutcome:
    """Run *execute*, converting any exception into a hard-fail outcome (never raises)."""
    try:
        return execute(db, config, stage, kind, task)
    except Exception as exc:
        logger.warning("%s team raised at stage %s: %s", kind, stage, exc)
        return StageOutcome(
            status="hard_fail", weak=False, summary=str(exc), team=kind, stage=stage
        )


def _run_stage(
    db: Database, config: AgentConfig, stage: Stage, task: str, execute: ExecuteFn
) -> StageOutcome:
    """Magentic-One for this stage, falling back to GraphFlow on a hard failure or exception.

    Never raises: any team exception is converted to a hard-fail outcome (via :func:`_safe_execute`)
    so the escalation ladder in :func:`run_short_creator` always runs.
    """
    if config.orchestration == "graph":
        return _safe_execute(execute, db, config, stage, "graph", task)
    magentic_outcome = _safe_execute(execute, db, config, stage, "magentic", task)
    if magentic_outcome.status == "hard_fail":
        return _safe_execute(execute, db, config, stage, "graph", task)
    return magentic_outcome


# Deterministic wish-parsing (7B-proof): a small model reliably follows an EXPLICIT tool call
# spelled out in the task, but not a conditional rule buried in a system prompt (live-run
# finding: "15 Szenen à 4s" and "Transkript neu" were both ignored despite prompt rules).
_TARGET_SECONDS_RE = re.compile(r"(\d{2,3})\s*s(?:ek\w*|ec\w*)?\b", re.IGNORECASE)
_SCENE_COUNT_RE = re.compile(r"(\d{1,2})\s*(?:s[cz]enen|scenes)\b", re.IGNORECASE)
_SCENE_SECONDS_RE = re.compile(
    r"(?:jede?r?|je|à|each)\s*(?:ca\.?\s*|~\s*)?(\d{1,2}(?:[.,]\d)?)\s*s(?:ek\w*|ec\w*)?\b",
    re.IGNORECASE,
)
_REVOICE_RE = re.compile(
    r"neu\s+einsprech\w*|re-?voice|new\s+voice\w*|neue\s+stimme|neues?\s+skript"
    r"|new\s+script|trans[a-z]*\s+(?:neu\b|new\b)|voice-?over\s+(?:neu\b|new\b)",
    re.IGNORECASE,
)


def _parse_target_seconds(topic: str) -> int | None:
    """A 2-3 digit "Ns" in the user's wording (e.g. "über 60s"), sanity-bounded."""
    for m in _TARGET_SECONDS_RE.finditer(topic):
        value = int(m.group(1))
        if 15 <= value <= 300:
            return value
    return None


def _task_directives(asset_id: str, topic: str, target_seconds: int) -> str:
    """Explicit, mandatory directives derived from the user's wording (pure; '' if none)."""
    parts: list[str] = []
    count_m = _SCENE_COUNT_RE.search(topic)
    secs_m = _SCENE_SECONDS_RE.search(topic)
    if count_m or secs_m:
        args = [f"asset_id='{asset_id}'", f"target_seconds={target_seconds}"]
        if count_m:
            args.append(f"max_segments={int(count_m.group(1))}")
        if secs_m:
            args.append(f"max_segment_seconds={secs_m.group(1).replace(',', '.')}")
        parts.append(
            "RENDER PLAN (mandatory): Editor, do NOT pick candidates yourself — call "
            f"render_short({', '.join(args)}) and reply EDITED export_id=<id>. "
            "Use fit='blur' if the Describer saw screen content / UI, else fit='crop'."
        )
    if _REVOICE_RE.search(topic):
        parts.append(
            "RE-VOICE REQUESTED (mandatory): Transcript Master, do NOT reply SKIP — write the "
            "new ENERGETIC script in the task's language (NEVER English), call "
            f"synthesize_voiceover(asset_id='{asset_id}', script=<your script>) and reply "
            "VOICEOVER path=<voiceover_path> SCRIPT: <script>. Editor: pass "
            "voiceover_path=<that path> and voiceover_text=<that script> to the render call."
        )
    return ("\n" + "\n".join(parts)) if parts else ""


def _task_prompt(asset_id: str, topic: str, target_seconds: int) -> str:
    return (
        f"Create a ~{target_seconds}s vertical short about: {topic}.\n"
        f"Work on asset_id='{asset_id}'. Scout candidate moments; the Transcript Analyst "
        f"summarizes the video per block and reads around candidates; the Describer says what is "
        f"visible (and whether it is screen content); the Director picks the best SCENES across "
        f"the video for the topic and target length (CHOSEN: <id>, <id>, ...); the Editor renders "
        f"them with render_short (fit='blur' for screen content); the QA gate judges the result "
        f"against the topic (say 'weak' if it does not match)."
        + _task_directives(asset_id, topic, target_seconds)
    )


def _result(outcome: StageOutcome, *, escalated: bool) -> dict[str, Any]:
    return {
        "ok": outcome.status == "ok",
        "status": outcome.status,
        "stage": outcome.stage,
        "team": outcome.team,
        "weak": outcome.weak,
        "escalated": escalated,
        "summary": outcome.summary,
    }


def run_short_creator(
    db: Database,
    config: AgentConfig,
    *,
    asset_id: str,
    topic: str,
    target_seconds: int = 60,
    execute: ExecuteFn | None = None,
) -> dict[str, Any]:
    """Run the ladder: Stage A (local), then Stage B (escalated) when Stage A is too bad."""
    run = execute if execute is not None else _default_execute
    # An explicit "Ns" in the user's wording beats the API default target.
    task = _task_prompt(asset_id, topic, _parse_target_seconds(topic) or target_seconds)

    a = _run_stage(db, config, "A", task, run)
    if a.status == "ok" and not a.weak:
        return _result(a, escalated=False)
    if a.status == "hard_fail" or (config.auto_escalate and a.weak):
        b = _run_stage(db, config, "B", task, run)
        return _result(b, escalated=True)
    return _result(a, escalated=False)  # soft-weak: manual escalation left to the user
