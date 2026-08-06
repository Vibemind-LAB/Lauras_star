"""v2 production run entrypoint: Production Board lifecycle + resume-aware task contract.

:func:`run_production` is the v2 counterpart of :func:`orchestrator.run_short_creator`: instead
of a single flat task prompt, it opens (or creates) a session's :class:`board.Board` first, asks
:func:`build_production_task` to describe the CURRENT board state as part of the task text (so a
resumed run does not redo finished work), then runs the fixed five-agent production team
(:mod:`production_agents`) through a small magentic-only A/B escalation ladder. v2 has no
GraphFlow fallback, so unlike v1's ``orchestrator._run_stage`` a hard failure at Stage A goes
straight to Stage B instead of trying GraphFlow first at Stage A.

Team execution stays injectable (``execute: ExecuteFn | None``) for the same reason as v1: the
ladder policy is fully testable without an LLM (:func:`orchestrator._safe_execute` guarantees a
raising ``execute`` still becomes a clean ``hard_fail`` outcome, so ``run_production`` itself
never raises). The default executor lazily builds the real
:func:`production_agents.build_production_team` and runs it with ``asyncio.run`` (mirrors
:func:`orchestrator._default_execute`); it is board-bound (the team needs ``board``/``asset_id``/
``deps``), so it is built as a closure rather than a bare module-level function, and only imports
``asyncio`` inside its own body — no autogen (nor anything that transitively needs it) is
imported at this module's top level.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..db import repos
from ..db.database import Database
from . import context
from .board import Board
from .board_models import (
    BoardMeta,
    Format,
    QaReport,
    RenderReport,
    SceneSelection,
    Script,
    canvas_for,
    content_hash,
)
from .orchestrator import ExecuteFn, StageOutcome, Status, TeamKind, _safe_execute
from .production_agents import build_production_team
from .production_pipeline import run_tail_with_qa
from .production_tools import ProductionDeps, follow_up_render_cap
from .providers import AgentConfig, Stage, config_warnings

logger = logging.getLogger(__name__)


def board_root_for(db: Database, asset_id: str, session_id: str) -> Path:
    """``<project.workspace_root>/agent-runs/<session_id>/board`` for *asset_id*'s project.

    Pure path construction: does not touch the filesystem and does not require a board to exist
    yet at the returned path.
    """
    asset = repos.get_asset(db, asset_id)
    if asset is None:
        raise ValueError(f"asset not found: {asset_id}")
    project = repos.get_project(db, str(asset["project_id"]))
    if project is None:
        raise ValueError(f"project not found for asset: {asset_id}")
    return Path(str(project["workspace_root"])) / "agent-runs" / session_id / "board"


def _expected_scene_numbers(db: Database, asset_id: str) -> list[int]:
    """Scene numbers this asset's rough cut has (the reviews the board should eventually cover).

    Reimplements ``production_tools._expected_scenes``' logic locally on top of
    :func:`context.scene_transcripts` rather than importing that private helper, keeping this
    module's dependency on :mod:`production_tools` limited to :class:`ProductionDeps`.
    """
    result = context.scene_transcripts(db, asset_id)
    if not result.get("ok"):
        return []
    return [int(s["scene_number"]) for s in result.get("scenes", [])]


# Per-scene excerpt sizing for the task text's SOURCE MATERIAL section. 300 chars mirrors the
# review snippet (production_tools' _SNIPPET_CHARS); the total cap keeps a many-scene rough
# cut from bloating every run's task string — past it, the tail is a pointer to
# get_scene_transcript instead of more text.
_EXCERPT_CHARS = 300
_EXCERPT_TOTAL_CHARS = 6000


def _source_material_section(db: Database, asset_id: str) -> str:
    """The per-scene transcript excerpt lines the task text carries as ground truth.

    Live 2026-08-04: the task string carried only the scout's hits — the team never saw what
    the source actually SAYS per scene, wrote invented marketing copy, and the operator rebuilt
    the script from the transcript by hand. One capped excerpt line per rough-cut scene, so
    every agent (not just the one holding get_scene_transcript) reads the same reality.
    """
    scenes = context.scene_transcripts(db, asset_id).get("scenes", [])
    lines: list[str] = []
    spent = 0
    for index, scene in enumerate(scenes):
        text = " ".join(str(scene.get("text") or "").split())
        excerpt = text[:_EXCERPT_CHARS] + ("…" if len(text) > _EXCERPT_CHARS else "")
        if not text:
            excerpt = "(no speech)"
        if spent + len(excerpt) > _EXCERPT_TOTAL_CHARS:
            lines.append(
                f"   ({len(scenes) - index} more scenes omitted — "
                "get_scene_transcript(scene_number) has each scene's full text)"
            )
            break
        spent += len(excerpt)
        lines.append(f"   - scene {int(scene['scene_number'])}: {excerpt}")
    if not lines:
        lines.append("   (no transcript available)")
    return "\n".join(lines)


def build_production_task(
    db: Database,
    board: Board,
    *,
    asset_id: str,
    task: str,
    target_seconds: int,
    message: str | None = None,
) -> str:
    """The task text handed to the magentic production team: a resume-aware contract.

    Seven fixed, deterministically-ordered elements, plus an eighth that only appears when this
    call is a follow-up on top of an already-produced board:

    1. goal + format + target length;
    2. the FIXED viral arc as a structural contract;
    3. the board's current resume status (reviews vs. expected scenes, per-artifact DONE/pending
       + version, the resume point, and an explicit "do not redo" instruction). When ``message``
       is set, any artifact that has archived versions also lists them (``[archived: vN, ...]``)
       so the team can name a concrete ``(name, version)`` pair back to ``revert_artifact``;
    4. the SOURCE MATERIAL ground truth: per-scene transcript excerpts
       (:func:`_source_material_section`) plus the grounding rule — a script claim not
       supported by the transcript or the scene's review is invented — followed by a SCENE
       FACTS block naming exactly what each scene SHOWS and SAYS: for a confirmed Gate-S pick,
       one line per SELECTED scene from its candidate (description + transcript_snippet);
       otherwise (no confirmed selection) one SHOWS-only line per the board's scene_reviews;
    5. the mandatory stage order (reviews -> storyline -> script -> voice+cutlist ->
       contact sheet -> render -> qa) plus the contact-sheet checkpoint as a known pattern:
       stopping at the Kontaktbogen or rendering later is steered purely by follow-up
       messages against the normal resume flow - no extra session state;
    6. the board's script language plus the coding-agent's ``voice_fits`` charter;
    7. the QA revision-round limit (one revise round, then ship with findings as warnings);
    8. only when ``message`` is set: the user's follow-up request text (capped at 2000 chars)
       plus instructions for interpreting it against the board status above - going back to an
       earlier version is a ``revert_artifact`` call using the ``archived_versions`` listed in
       section 3, a content change is a re-save of the affected artifact (the highest affected
       one wins when the request touches more than one) with everything downstream left to
       rebuild through the normal pipeline, and an intact upstream artifact the request does not
       mention must never be redone.
    """
    meta = board.meta()
    expected_scenes = _expected_scene_numbers(db, asset_id)
    status = board.status()
    resume_point = board.resume_point(expected_scenes)
    reviewed = status["scene_reviews"]["count"]
    show_archived = bool(message)

    def _artifact_line(name: str, info: dict[str, Any]) -> str:
        base = f"DONE (v{info['version']})" if info["version"] is not None else "pending"
        archived = info["archived_versions"]
        if show_archived and archived:
            versions = ", ".join(f"v{v}" for v in archived)
            return f"  - {name}: {base} [archived: {versions}]"
        return f"  - {name}: {base}"

    artifact_lines = "\n".join(
        _artifact_line(name, info) for name, info in status["artifacts"].items()
    )

    follow_up = ""
    if message:
        follow_up = (
            "\n"
            "9) USER FOLLOW-UP REQUEST:\n"
            f"   {message[:2000]}\n"
            "   Interpret this request against the BOARD STATUS above. Going back to an "
            "earlier version: coding_agent calls revert_artifact(name, version), using the "
            "archived_versions listed per artifact in the board status above. A content "
            "change: re-save the affected artifact - if the request touches more than one "
            "artifact, the highest affected artifact wins - and let everything downstream "
            "rebuild through the normal pipeline. NEVER redo an intact upstream artifact the "
            "request does not ask to change.\n"
        )

    # Loaded once and reused by both the Gate-S charter block below and the SCENE FACTS block
    # further down — both need to know whether a CONFIRMED selection exists.
    selection = board.load("scene_selection")
    confirmed_selection = (
        selection
        if isinstance(selection, SceneSelection) and selection.confirmed_utc is not None
        else None
    )

    gate_s_lines = ""
    if meta.scene_gate:
        if confirmed_selection is not None:
            # A confirmed pick is final. Steps 1-3 below are propose-then-stop instructions;
            # re-emitting them for a confirmed board invited exactly what production_tools'
            # structural guard now also refuses — a follow-up team turn re-proposing a
            # "better" set and silently clobbering the user's already-confirmed choice.
            selected = sorted(confirmed_selection.selected_scene_numbers)
            gate_s_lines = (
                f"SCENE SELECTION (confirmed by the user): use ONLY scenes {selected}. "
                "Do NOT call propose_scene_selection again — the pick is final unless the "
                "user asks to change it.\n"
            )
        else:
            gate_s_lines = (
                "SCENE SELECTION GATE (mandatory):\n"
                "1. Review every expected scene (review_scene) BEFORE proposing.\n"
                "2. Call propose_scene_selection with 4-8 candidates that fit the task —\n"
                "   description = what the scene SHOWS, transcript_snippet = what is SAID\n"
                "   (from get_scene_transcript), rationale = why it belongs in this film.\n"
                "   Mark your suggested subset recommended.\n"
                "3. Then STOP. Do not write a storyline or script — save_storyline refuses\n"
                "   until the user confirmed the selection in chat.\n"
                "4. After confirmation, use ONLY the selected scenes.\n"
            )

    # SCENE FACTS (VS5, 2026-08-06-voice-per-scene): a confirmed Gate-S pick names exactly what
    # each SELECTED scene SHOWS (candidate.description) and SAYS (candidate.transcript_snippet)
    # — scene_author must write every line FOR its scene, not free-floating marketing copy. When
    # there is no confirmed selection (Gate S off, or a proposal still sits unconfirmed), the
    # board's own scene_reviews are the fallback ground truth (SHOWS only — a review carries no
    # transcript snippet) so old, gate-off sessions get the same grounding as new gated ones.
    facts_lines: list[str] = []
    if confirmed_selection is not None:
        chosen = {
            c.scene_number: c
            for c in confirmed_selection.candidates
            if c.scene_number in set(confirmed_selection.selected_scene_numbers)
        }
        for number in sorted(chosen):
            cand = chosen[number]
            facts_lines.append(
                f"- scene {number}: SHOWS {cand.description} | SAYS "
                f"\"{cand.transcript_snippet}\""
            )
    else:
        for review in board.scene_reviews():
            facts_lines.append(f"- scene {review.scene_number}: SHOWS {review.description}")
    facts_block = ""
    if facts_lines:
        facts_block = (
            "SCENE FACTS (write every script line ABOUT its scene — what it shows and "
            "says; no free-floating marketing copy):\n" + "\n".join(facts_lines) + "\n"
        )

    _, (out_w, out_h) = canvas_for(meta.format)
    return (
        f"1) GOAL: {task}\n"
        f"   Format: {meta.format} — renders {out_w}x{out_h}. "
        f"Target length: ~{target_seconds}s.\n"
        "\n"
        "2) FIXED VIRAL ARC (structural contract - every short follows this shape; "
        "chapter roles are exactly hook, problem, feature, payoff_cta):\n"
        "   hook (2-3s, cold-open visual) -> problem -> 3-4 feature chapters "
        "(2-3 scenes each, building on each other) -> payoff_cta.\n"
        "\n"
        f"3) BOARD STATUS (session {meta.session_id}, asset {asset_id}):\n"
        f"   Scene reviews: {reviewed}/{len(expected_scenes)} expected scenes reviewed.\n"
        f"{artifact_lines}\n"
        f"   Resume point: {resume_point}\n"
        f"{gate_s_lines}"
        "   Artifacts already on the board are DONE - do not redo them; continue at the "
        "resume point.\n"
        "\n"
        "4) SOURCE MATERIAL (ground truth): each scene's spoken transcript, verbatim from "
        "the source video. Every script line must be supported by these words or by the "
        "scene's saved review — a claim backed by neither is INVENTED and must not be "
        "written; no generic marketing copy. scene_author reads a scene's full text via "
        "get_scene_transcript(scene_number).\n"
        f"{_source_material_section(db, asset_id)}\n"
        f"{facts_block}"
        "\n"
        "5) MANDATORY ORDER: reviews -> storyline -> script -> voice+cutlist -> contact sheet "
        "(save_contact_sheet: ALWAYS right after build_cutlist and BEFORE render_production, "
        "and again after every cutlist rebuild - a cutlist save archives the sheet) -> render "
        "(coding_agent) -> qa. Do not skip or reorder a stage.\n"
        "   CONTACT-SHEET CHECKPOINT (known pattern, no extra session state): the user steers "
        "around the Kontaktbogen purely by follow-up messages. When the task or a user message "
        "says to stop at the contact sheet (e.g. 'bau bis zum Kontaktbogen, dann stopp'), END "
        "the run right after save_contact_sheet and report the sheet's tiles instead of "
        "rendering; a later message (e.g. 'render jetzt') resumes at render_production through "
        "the normal resume flow.\n"
        "   SCRIPT-APPROVAL CHECKPOINT (known pattern, no extra session state): when this "
        "session's script_gate is enabled and the script is not yet approved, "
        "synthesize_script_voice refuses deterministically - the tool enforces it, this just "
        "tells the team so it does not waste a turn fighting it. END the run right after the "
        "last save_script_chapter call and report the script for the user to review; approval "
        "happens in chat (approve_script) and resumes the run through the normal follow-up "
        "flow - never call synthesize_script_voice while the gate is still pending. After every "
        "script (re-)approval, call suggest_scenes_for_script FIRST and validate the storyline "
        "against its suggestions before treating the script as final.\n"
        "\n"
        f"6) LANGUAGE + CHARTER: the script MUST be written in {meta.language} - never switch "
        "languages mid-script. If the user message asks for another language, the Scene "
        "Author calls set_board_language FIRST and then rewrites every chapter in that "
        "language - never leave chapters in the old language behind. Coding-agent charter: "
        "if voice_fits "
        "comes back False, never shorten the voice - rebuild the cutlist with a longer "
        "per-chapter time budget and render again. THE FOOTAGE IS FIXED: there is no asset "
        "owner, no admin, no upload channel, and no way to obtain new or longer scene files - "
        "never plan around acquiring material. When the scenes cannot cover the voice even at "
        "full stretch (a capacity_warning names this), the correct and pre-authorized move is "
        "a SHORTER SCRIPT and therefore a shorter film: trim or redistribute the flagged "
        "chapter's words and continue. A finished shorter film always beats an unfinished "
        "longer one.\n"
        "\n"
        "7) QA LIMIT: after ONE revise verdict, at most one revision round is allowed - if the "
        "next QA pass still finds issues, deliver anyway with the findings recorded as "
        "warnings instead of looping again.\n"
        "\n"
        f"{_tool_ownership_section(meta.language)}"
        f"{follow_up}"
    )


def _tool_ownership_section(language: str) -> str:
    """The roster of who holds which tool, derived from the same specs the team is built from.

    Agents cannot discover each other's toolsets, and across three runs the orchestrator
    routed writes to agents that do not hold the tool — ending once with the orchestrator and
    story_architect asking EACH OTHER to call save_script_chapter while the budget died.
    Generated, not hand-written, so the list cannot drift from the real team.
    """
    from .production_agents import production_agent_specs

    lines = "\n".join(
        f"   - {spec.name}: {', '.join(spec.tool_names)}"
        for spec in production_agent_specs(language)
    )
    return (
        "8) TOOL OWNERSHIP (exhaustive - no other agent has these):\n"
        f"{lines}\n"
        "   ONLY the named agent can call its tools. Never instruct any other agent - or "
        "yourself - to call a tool it does not hold; route the WORK to the agent that owns "
        "the tool and let it make the call itself.\n"
    )


def _qa_weak(board: Board) -> bool:
    """Whether the board's QA verdict marks this production as weak.

    Reads the board's structured ``QaReport`` (``verdict="ship"|"revise"``), not message
    scanning: a missing report or a "revise" verdict is weak, and only an explicit "ship"
    verdict is not. Shared by :func:`_parse_outcome` (a team just ran) and the full-restore
    short-circuit in :func:`run_production` (no team ran, but the board already carries a
    verdict) — both need the exact same read of the same board state.
    """
    qa = board.load("qa_report")
    return not (isinstance(qa, QaReport) and qa.verdict == "ship")


def _export_id_of(board: Board) -> str | None:
    """The render's export id, if the board carries a ``RenderReport``."""
    render_report = board.load("render_report")
    return render_report.export_id if isinstance(render_report, RenderReport) else None


def _deps_for_run(
    deps: ProductionDeps | None, board: Board, message: str | None
) -> ProductionDeps | None:
    """The deps a run actually executes with: unchanged for a plain resume/restart, render cap
    raised for an explicit user follow-up.

    ``_MAX_RENDER_CYCLES`` exists to stop the TEAM's own revision loops; a user asking for a
    change is not one. Live 2026-08-04: the user asked for a reframe against a board whose cap
    was already spent, and render_production silently shipped the old cut. A message run gets
    ``max_render_cycles = follow_up_render_cap(board)`` — one render above what has already
    been spent, so each follow-up grants at most one re-render and the backstop holds again
    right after. The caller's deps object is never mutated (``dataclasses.replace``).
    """
    if message is None:
        return deps
    cap = follow_up_render_cap(board)
    if deps is None:
        return ProductionDeps(max_render_cycles=cap)
    return replace(deps, max_render_cycles=cap)


def _parse_outcome(
    board: Board,
    result: Any,
    *,
    stage: Stage,
    tool_calls: int = 0,
    require_tool_call: bool = False,
) -> StageOutcome:
    """Read a finished production-team run into an outcome.

    ``weak`` comes from the board's QA verdict, not message scanning: v2's QA reviewer writes a
    structured ``QaReport`` (``verdict="ship"|"revise"``) to the board via ``save_qa_report``
    rather than saying the word "weak" in chat (v1's convention), so a missing report or a
    "revise" verdict is weak and only an explicit "ship" verdict is not.

    ``summary`` is the LAST non-empty message — the team's final answer. Concatenating all
    messages and truncating put ``messages[0]`` (the task text) into every summary
    (live finding 2026-07-20: three runs in a row "summarized" themselves with their own task).

    ``require_tool_call`` guards a follow-up run: a user message is by definition a request for
    work, and a run that never touched a single tool did none. Live 2026-08-04 (session
    6021d069, run 170643Z): the MagenticOne orchestrator answered a reframe request by
    declaring success with ZERO tool calls. That is a ``hard_fail`` — the ladder escalates to
    Stage B instead of reporting a success that changed nothing — with the team's own closing
    claim kept in the summary so the false success stays inspectable.
    """
    summary = ""
    for msg in reversed(getattr(result, "messages", None) or []):
        to_text = getattr(msg, "to_model_text", None)
        text = (to_text() if callable(to_text) else str(getattr(msg, "content", ""))).strip()
        if text:
            summary = text[:2000]
            break
    if require_tool_call and tool_calls == 0:
        return StageOutcome(
            status="hard_fail",
            weak=_qa_weak(board),
            summary=(
                "the team finished a user follow-up without a single tool call — nothing was "
                f"done; its closing message: {summary}"
            )[:2000],
            team="magentic",
            stage=stage,
        )
    return StageOutcome(
        status="ok", weak=_qa_weak(board), summary=summary, team="magentic", stage=stage
    )


def _make_default_execute(
    board: Board,
    asset_id: str,
    deps: ProductionDeps | None,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
    *,
    require_tool_call: bool = False,
    agent_names: tuple[str, ...] | None = None,
) -> ExecuteFn:
    """The default ``ExecuteFn``: lazily builds and runs the real production team.

    Mirrors :func:`orchestrator._default_execute`, but the v2 team is board-bound
    (:func:`production_agents.build_production_team` needs ``board``/``asset_id``/``deps``), so
    those are captured in this closure instead of being ``ExecuteFn`` parameters.

    The team runs via ``run_stream`` and every normalized event goes to *event_sink* (the job
    handler points it at the session run log). A run once spent 44 minutes in its script phase
    and saved nothing, and the log held exactly two lines — meta and done; which tool was
    called and what it refused was unknowable. Observability only: a missing or crashing sink
    never affects the run.

    ``tool_call`` events are additionally COUNTED (sink or no sink) and handed to
    :func:`_parse_outcome`, which — with *require_tool_call* set for a follow-up run — turns a
    zero-tool-call finish into a ``hard_fail`` instead of a false success.

    ``agent_names`` passes straight through to :func:`production_agents.build_production_team`
    (MP2, the bounded QA stage): ``None`` builds the full roster as before, a narrower tuple
    builds only those agents.
    """

    def execute(
        db: Database, config: AgentConfig, stage: Stage, kind: str, task: str
    ) -> StageOutcome:
        import asyncio  # local: only the real run needs the event loop

        from .stream import _map_event

        async def _run() -> tuple[Any, int]:
            team = build_production_team(
                db, board, config, asset_id=asset_id, stage=stage, deps=deps,
                agent_names=agent_names,
            )
            final: Any = None
            n_tool_calls = 0
            async for raw in team.run_stream(task=task):
                if type(raw).__name__ == "TaskResult":
                    final = raw
                    continue
                mapped = _map_event(raw, "magentic")
                if mapped is None:
                    continue
                if mapped.get("type") == "tool_call":
                    n_tool_calls += 1
                if event_sink is None:
                    continue
                try:
                    event_sink(mapped)
                except Exception:  # noqa: BLE001 — logging must never fail the film
                    logger.warning("production event sink failed; continuing")
            return final, n_tool_calls

        try:
            result, n_tool_calls = asyncio.run(_run())
        except Exception as exc:
            logger.warning("magentic production team failed at stage %s: %s", stage, exc)
            return StageOutcome(
                status="hard_fail", weak=False, summary=str(exc), team="magentic", stage=stage
            )
        return _parse_outcome(
            board,
            result,
            stage=stage,
            tool_calls=n_tool_calls,
            require_tool_call=require_tool_call,
        )

    return execute


def _completed_result(
    board: Board,
    *,
    session_id: str,
    restored: list[str],
    status: Status,
    stage: Stage,
    team: TeamKind,
    weak: bool,
    escalated: bool,
    summary: str,
    export_id: str | None,
    resume_point: str,
) -> dict[str, Any]:
    """The result dict :func:`run_production` returns, built once for both completion paths:
    the full-restore short-circuit (no team turn) and the normal completion tail (a team ran).

    A future key added to only one of the two call sites would silently miss the other — the
    exact way a past bug happened, since both built the same 13-key dict literal independently.

    ``ok`` is the agent LOOP's status: it ran without a hard failure. It does not mean a video
    exists — a live run reported ``ok=True`` with ``export_id=None`` and half a board. ``complete``
    is the production's status; the board always knows via ``resume_point``, so both are derived
    here from ``status``/``resume_point`` rather than passed in separately, since both call sites
    compute them the same way.
    """
    return {
        "ok": status == "ok",
        "complete": resume_point == "done",
        "status": status,
        "stage": stage,
        "team": team,
        "weak": weak,
        "escalated": escalated,
        "summary": summary,
        "session_id": session_id,
        "board": board.status(),
        "export_id": export_id,
        "resume_point": resume_point,
        "restored": restored,
    }


# The five resume points that :func:`production_pipeline.run_deterministic_tail` actually
# advances (``_STEP_BY_RESUME_POINT`` plus its terminal ``qa_report``). A resume point OUTSIDE
# this set (``storyline``/``script``, still creative work; ``done``, nothing left) makes the
# tail a no-op that still reports ``ok`` — by design, so a caller who has already checked
# eligibility can call it unconditionally. That makes this set-membership check the ONLY guard
# stopping ``run_production`` from handing a pre-chain or already-finished board to the tail and
# getting back a hollow "ok" that did nothing.
_TAIL_RESUME_POINTS = frozenset({
    "voice", "cutlist", "contact_sheet", "render_report", "qa_report",
})


def deterministic_eligible(
    board: Board, message: str | None, expected_scenes: list[int]
) -> bool:
    """Spec 2026-08-05 (modular production): the post-approval resume of a gated session
    runs the deterministic tail instead of the agent team.

    True IFF ALL of: no follow-up ``message`` (a follow-up is itself a request for a team
    turn, same rule the full-restore short-circuit above uses); Gate B is on and approved;
    the approval is content-current — stamped against the SAME script that is on the board
    right now, the identical compare :meth:`Board.status`'s ``script_gate.approved`` uses, so
    an edit or revert after approval re-arms the gate here too; and the creative work is
    actually done, i.e. the board's resume point is one of the five the deterministic tail
    advances (:data:`_TAIL_RESUME_POINTS`) — never ``storyline``/``script`` (creative work
    still pending) or ``done`` (nothing left to run).
    """
    if message is not None:
        return False
    meta = board.meta()
    if not meta.script_gate or meta.script_approved_utc is None:
        return False
    script = board.load("script")
    if not isinstance(script, Script):
        # Defensive type-narrowing, likely unreachable: an approval stamp only exists once
        # some script was hashed into it (see set_script_approved), and scripts are versioned,
        # never deleted back to None — but this keeps content_hash() below from being handed
        # anything but a Script no matter how that invariant is reached.
        return False
    if meta.script_approved_script_hash != content_hash(script):
        return False
    return board.resume_point(expected_scenes) in _TAIL_RESUME_POINTS


def run_production(
    db: Database,
    config: AgentConfig,
    *,
    asset_id: str,
    session_id: str,
    task: str,
    target_seconds: int = 60,
    format: Format = "insta",
    language: str = "German",
    message: str | None = None,
    script_gate: bool = False,
    scene_gate: bool = False,
    execute: ExecuteFn | None = None,
    deps: ProductionDeps | None = None,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run the v2 production team for one session: resume-aware board + magentic-only A/B ladder.

    A missing asset — or one whose project no longer exists (an orphaned asset) — is reported
    the same way a failed run is, never raised. Otherwise the session's board is opened if it
    already exists, or created fresh (this is what makes a second call for the same
    ``session_id`` a resume rather than a restart - nothing already on the board is touched or
    re-created). ``message`` is a follow-up request on top of an already-produced board (e.g.
    "go back to the previous storyline" or "make the hook punchier"); it assumes a prior
    production, so if the board does not exist yet, this is an error - report it and return
    WITHOUT creating a board (unlike a plain fresh run, a follow-up with no board to follow up
    on is not a valid restart). Stage A runs first; only a ``hard_fail`` escalates to Stage B
    (both magentic-only - v2 has no GraphFlow fallback). Every stage call goes through
    :func:`orchestrator._safe_execute`, so a raising ``execute`` never propagates out of here.

    ``script_gate`` only matters for a FRESH board (no effect on a resume/follow-up, whose
    ``BoardMeta`` already exists and is left untouched): it seeds Gate B (the script-approval
    checkpoint) onto the new session's meta, so ``synthesize_script_voice`` refuses until the
    user approves the script in chat. Callers opt in per session (currently only
    ``run_project_auto_short``'s chat-driven sessions do); the default keeps every other
    caller's fresh boards exactly as before this gate existed.

    ``scene_gate`` is the same opt-in-per-session, fresh-board-only shape as ``script_gate``,
    for Gate S (spec 2026-08-06): it seeds ``BoardMeta.scene_gate``, so the team must call
    ``propose_scene_selection`` and stop for the user to pick scenes before ``save_storyline``
    will accept a storyline. Defaults to False so v1's plain ``/assets/{asset_id}/production``
    endpoint and auto-overview (which never touches the production board) are unaffected.
    """
    if repos.get_asset(db, asset_id) is None:
        return {
            "ok": False,
            "error": "asset not found",
            "session_id": session_id,
            "restored": [],
        }

    try:
        root = board_root_for(db, asset_id, session_id)
    except ValueError:
        # The asset row exists but its project has been deleted out from under it —
        # board_root_for raises (it is a pure/raising path helper); this is the one caller that
        # must never propagate that, so it is turned into the same kind of reported failure as
        # a missing asset instead.
        return {
            "ok": False,
            "error": "project not found",
            "asset_id": asset_id,
            "session_id": session_id,
            "restored": [],
        }
    try:
        board = Board.open(root)
    except FileNotFoundError:
        if message:
            return {
                "ok": False,
                "error": "unknown session (no board)",
                "asset_id": asset_id,
                "session_id": session_id,
                "restored": [],
            }
        meta = BoardMeta(
            session_id=session_id,
            asset_id=asset_id,
            created_utc=datetime.now(UTC).isoformat(timespec="seconds"),
            task=task,
            format=format,
            language=language,
            target_seconds=float(target_seconds),
            script_gate=script_gate,
            scene_gate=scene_gate,
        )
        board = Board.create(root, meta)

    # Advisory (never a gate): say out loud when the text agents will run on a local ollama
    # model. Live incident 2026-07-20: three production runs silently ran qwen2.5:7b — tool
    # calls as prose, invented schemas — and nothing anywhere said so.
    warnings = config_warnings(config)
    if warnings and event_sink is not None:
        try:
            event_sink({"type": "config_warning", "warnings": warnings})
        except Exception:  # noqa: BLE001 - observability must never break the run
            logger.warning("config_warning event sink failed", exc_info=True)

    # Full-suffix restore (spec 2026-07-20-provenance-chain-design.md): bring back the
    # longest archived suffix whose parent-instance hashes match the board. Runs BEFORE
    # build_production_task so the resume contract reads DONE for what came back — the
    # task-text lie that killed the single-link restore is structurally impossible here.
    restored = board.restore_coherent_suffix()
    if restored and event_sink is not None:
        try:
            event_sink({"type": "restored", "artifacts": list(restored)})
        except Exception:  # noqa: BLE001 — observability must never fail the run
            logger.warning("restored-event sink failed; continuing")

    # Computed once and reused below (the short-circuit condition and the completion tail both
    # need it); build_production_task computes its own copy internally for its other callers.
    expected_scenes = _expected_scene_numbers(db, asset_id)

    # Spec decision 2 (2026-07-20-provenance-chain-design.md, §Entscheidungen (User)): a fully
    # coherent board reaches complete WITHOUT an agent-team turn. A follow-up ``message`` is
    # itself a request for a team turn (e.g. "make the hook punchier" against an already-done
    # board), so the short-circuit applies only to a plain resume/restart with no message.
    if message is None and board.resume_point(expected_scenes) == "done":
        board.set_status("complete")
        return _completed_result(
            board,
            session_id=session_id,
            restored=restored,
            status="ok",
            stage="A",
            team="magentic",
            weak=_qa_weak(board),
            escalated=False,
            summary="board already coherent through qa_report; no team turn needed",
            export_id=_export_id_of(board),
            resume_point="done",
        )

    # Gate S (spec 2026-08-06): a proposal is on the board and the user has not picked yet.
    # A team turn now would only run into save_storyline's structural refusal — so a plain
    # resume parks instead of spending an LLM run. A follow-up MESSAGE still goes through
    # (the user may be adjusting the proposal in chat via the team).
    if (
        message is None
        and board.meta().scene_gate
        and isinstance(board.load("scene_selection"), SceneSelection)
        and board.resume_point(expected_scenes) == "scene_selection"
    ):
        return _completed_result(
            board,
            session_id=session_id,
            restored=restored,
            status="ok",
            stage="A",
            team="magentic",
            weak=_qa_weak(board),
            escalated=False,
            summary="awaiting user scene selection — pick scenes in chat to continue",
            export_id=_export_id_of(board),
            resume_point="scene_selection",
        )

    # Spec 2026-08-05 (modular production): a gated session resuming past user approval needs
    # no creative agent turn at all — voice/cutlist/contact_sheet/render/qa are plain tool
    # calls the deterministic pipeline runs itself, so an approved script can never be
    # silently rewritten by a resumed team. deterministic_eligible is the ONE guard deciding
    # this; run_deterministic_tail is a no-op-but-``ok`` outside its five resume points, so
    # skipping this check would let a pre-chain or already-finished board reach the tail and
    # come back reporting a hollow success.
    if deterministic_eligible(board, message, expected_scenes):
        # C1 incident class (2026-08-05 final review): deterministic_eligible() requires
        # `message is None`, so routing this branch through `_deps_for_run` (which only
        # raises the render cap when `message` is set) was a no-op here — the cap never
        # rose across repeated approve->rewrite cycles. Once _MAX_RENDER_CYCLES was spent,
        # render_production's cap branch (production_tools.py) silently reverted an
        # ARCHIVED render onto the board (ok:False, diagnosis buried under `note`) and the
        # NEXT "Script freigeben" sailed past the missing render, with QA judging the
        # OLD-script export as if it were the film just approved. An explicit user approval
        # IS the one-render grant a message run gets — mirror _deps_for_run's policy
        # directly (same follow_up_render_cap + dataclasses.replace idiom, never mutating
        # the caller's deps) instead of a message gate that is structurally never open here.
        run_deps: ProductionDeps | None = replace(
            deps or ProductionDeps(), max_render_cycles=follow_up_render_cap(board)
        )
        tail, qa_outcome = run_tail_with_qa(
            db, board, config, asset_id=asset_id, deps=run_deps,
            event_sink=event_sink, expected_scenes=expected_scenes,
        )
        resume_point = board.resume_point(expected_scenes)
        # A hard-failed QA stage (LLM outage, etc. — _safe_execute turns any exception into a
        # hard_fail StageOutcome) is a failure of THIS run just as much as a failed chain step:
        # the chain succeeded but the run as a whole did not finish, and the board must not be
        # left "active" over it — exactly the dead-run class the board docstring warns about
        # (`Board.set_status`: only the job result knowing left a session reporting "active"
        # for 55 minutes after it had actually died).
        qa_hard_failed = qa_outcome is not None and qa_outcome.status == "hard_fail"
        # I1 (2026-08-05 final review): defense in depth alongside _make_qa_execute's
        # require_tool_call guard. A QA StageOutcome that reports "ok" but never actually
        # saved a qa_report (a zero-tool-call turn the guard should already have caught, or
        # any future QA execute that bypasses _make_qa_execute) must not let the board
        # finish with no verdict on record — the same stranding class the tail's own
        # dead-run guard above exists for, just one write later in the chain.
        qa_stranded = (
            tail.ok and not qa_hard_failed and qa_outcome is not None
            and board.load("qa_report") is None
        )
        if not tail.ok:
            board.set_status("failed")
            summary = f"deterministic tail failed at {tail.failed_step}: {tail.reason}"
            ok = False
        elif qa_hard_failed:
            assert qa_outcome is not None  # narrows for mypy; qa_hard_failed already checked
            board.set_status("failed")
            summary = tail.summary + f"; qa failed: {qa_outcome.summary}"
            ok = False
        elif qa_stranded:
            board.set_status("failed")
            summary = tail.summary + "; qa stage finished without saving a qa_report"
            ok = False
        else:
            if resume_point == "done":
                board.set_status("complete")
            summary = tail.summary + (
                f"; qa: {qa_outcome.summary}" if qa_outcome is not None else ""
            )
            ok = True
        return _completed_result(
            board,
            session_id=session_id,
            restored=restored,
            status="ok" if ok else "hard_fail",
            stage="A",
            team="magentic",  # cosmetic result field; cards read summary/status.
            weak=_qa_weak(board),
            escalated=False,
            summary=summary,
            export_id=_export_id_of(board),
            resume_point=resume_point,
        )

    task_text = build_production_task(
        db, board, asset_id=asset_id, task=task, target_seconds=target_seconds, message=message
    )
    # A follow-up message run gets two guards the plain resume does not (live 2026-08-04): the
    # render-cycle cap is raised by one so an operator-requested re-render is not eaten by the
    # team's runaway-loop backstop, and a run that finishes without a single tool call is a
    # hard_fail instead of a false success.
    run_deps = _deps_for_run(deps, board, message)
    run: ExecuteFn = execute if execute is not None else _make_default_execute(
        board, asset_id, run_deps, event_sink, require_tool_call=message is not None
    )

    outcome = _safe_execute(run, db, config, "A", "magentic", task_text)
    escalated = False
    if outcome.status == "hard_fail":
        outcome = _safe_execute(run, db, config, "B", "magentic", task_text)
        escalated = True

    export_id = _export_id_of(board)
    resume_point = board.resume_point(expected_scenes)

    # Tell the BOARD how this ended, not just the caller. The result dict goes into the job row;
    # the board is what the session endpoint reads. A run that hard-failed on a missing API key
    # left the board reporting "active" for 55 minutes because only the result was ever told.
    if outcome.status == "hard_fail":
        board.set_status("failed")
    elif resume_point == "done":
        board.set_status("complete")

    return _completed_result(
        board,
        session_id=session_id,
        restored=restored,
        status=outcome.status,
        stage=outcome.stage,
        team=outcome.team,
        weak=outcome.weak,
        escalated=escalated,
        summary=outcome.summary,
        export_id=export_id,
        resume_point=resume_point,
    )
