"""The v2 production-team agent roster (AutoGen ``AssistantAgent``s) — Slice 3, Task 7.

Five specialists turn a reviewed rough cut into a shipped short by writing to, and reading from,
the Production Board (:mod:`.board`) — never talking directly to each other about content, only
through board artifacts (scene reviews -> storyline -> script -> voice/cutlist/render -> QA). The
pipeline is a fixed judgment chain: ``vision_reviewer`` (what is actually on screen) ->
``story_architect`` (the arc) -> ``scene_author`` (the words) -> ``coding_agent`` (voice, cutlist,
contact sheet, render — execution only, plus reverting the board to an earlier artifact version
when explicitly asked to) -> ``qa_reviewer`` (ship or revise on the rendered result). Each tool
name in the roster
is cross-checked in tests against :func:`production_tools.build_production_tool_specs`'s real
tool names.

Mirrors :mod:`.agents`/:mod:`.magentic`'s split: :func:`production_agent_specs` is pure data (no
autogen, no db — testable standalone) and :func:`build_production_team` is the only
autogen-touching constructor here, lazily importing the optional ``autoshort`` extra and wiring
one shared agent-role model client plus a separate orchestrator-role client into a
``MagenticOneGroupChat``, exactly like :func:`laura.short_creator.magentic.build_magentic_team`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..db.database import Database
from .agents import AgentSpec
from .board import Board
from .production_tools import ProductionDeps, build_production_tool_specs
from .providers import AgentConfig, Stage, build_model_client

if TYPE_CHECKING:  # annotation only — never imported at runtime
    from autogen_agentchat.teams import MagenticOneGroupChat

# Hard cap on the orchestrator's turn budget — a runaway-loop backstop (mirrors magentic.MAX_TURNS).
MAX_TURNS = 30


def production_agent_specs(language: str = "German") -> list[AgentSpec]:
    """The fixed v2 production-team roster. Pure — no autogen, no db.

    ``language`` is the board's — the words the scene_author writes. It was hard-coded to
    German, which no task string could argue with: a submission whose jury reads English got a
    German script however often the goal asked for English.

    Tool names must exist in :func:`production_tools.build_production_tool_specs` (cross-checked
    in tests). The five specialists judge in a fixed pipeline, each reading and writing the
    Production Board rather than talking to each other directly: ``vision_reviewer`` reviews every
    scene with the VLM, ``story_architect`` structures reviewed scenes into the viral arc,
    ``scene_author`` writes the spoken script from that arc, ``coding_agent`` executes voice ->
    cutlist -> contact sheet -> render without changing content, and ``qa_reviewer`` judges the
    rendered result.
    """
    return [
        AgentSpec(
            name="vision_reviewer",
            description="Reviews every rough-cut scene with the VLM before anything else runs.",
            system_message=(
                "You are the Vision Reviewer. You judge only what you SEE — never invent visual "
                "details or guess from a scene number alone; the tool's review is the record, not "
                "your assumption. Call board_status first to see which scenes are expected and "
                "which already have a review. For every expected scene without a review, call "
                "get_scene_context to orient yourself on its transcript and frame range, then call "
                "review_scene so the VLM looks at its actual frames and the review is written to "
                "the board. Keep going until every expected scene has been reviewed — a scene "
                "with no review blocks the whole team downstream, including yours. When every "
                "scene is covered, call get_reviews and report which scenes are strongest "
                "(highest hook_score) and which are weak, so the Story Architect knows what "
                "material the arc can actually be built from."
            ),
            tool_names=("board_status", "get_scene_context", "review_scene", "get_reviews"),
            max_tool_iterations=10,
        ),
        AgentSpec(
            name="story_architect",
            description="Structures the reviewed scenes into the fixed viral short arc.",
            system_message=(
                "You are the Story Architect. Fill the FIXED viral arc — chapter roles are "
                "exactly hook, problem, feature, payoff_cta: hook (2-3s) -> problem -> 3-4 "
                "feature chapters (2-3 scenes each, building on each other) -> payoff_cta. "
                "The arc is the goal, not a quota: a (scene, window) pair may be used only "
                "ONCE, so the reviews' total window count is the hard ceiling on how many "
                "chapters you can build. Count them in get_reviews BEFORE you plan. One "
                "reviewed scene with one window means a ONE-chapter arc — that is valid and "
                "correct, and far better than retrying a four-chapter arc the material cannot "
                "carry. If save_storyline answers with a material_hint, it has done that "
                "arithmetic for you: obey it on the next attempt instead of rephrasing the "
                "same arc. "
                "First call get_reviews to see what material is actually "
                "available, and get_scene_context for detail on any scene you are unsure about — "
                "use ONLY reviewed scenes, never a scene number that has no review yet. A review "
                "may list several strong windows (get_reviews shows them, 0-based): a plain "
                "scene number plays window 0, {\"scene\": N, \"window\": K} plays window K — "
                "reuse the SAME scene with DIFFERENT windows to fill long targets (long scenes "
                "often carry 2-4 usable moments), never the same (scene, window) pair twice. A "
                "first-time viewer who has never seen the source video must be able to follow "
                "every step of the arc without confusion or a missing link. Check board_status if "
                "you need the current resume point. Save your storyline via save_storyline; if it "
                "returns validation errors — an unreviewed scene, a malformed chapter — fix "
                "exactly what it names and save again. Confirm with get_storyline once accepted."
            ),
            tool_names=(
                "get_reviews",
                "get_scene_context",
                "propose_scene_selection",
                "save_storyline",
                "get_storyline",
                "board_status",
            ),
            max_tool_iterations=4,
        ),
        AgentSpec(
            name="scene_author",
            description="Writes the spoken script line by line from the storyline's scenes.",
            system_message=(
                "You are the Scene Author. LANGUAGE SWITCH: if the user's message asks for "
                "another language (e.g. 'mach das auf englisch'), call set_board_language "
                "FIRST, then rewrite every chapter of the script in that language before doing "
                "anything else — never leave some chapters in the old language. "
                "First call get_storyline to see the arc and its "
                "chapters, then script_budget — it tells you how many words the whole script "
                "may spend AND how many each chapter may spend in per_chapter; never guess a "
                "length or count seconds yourself. Spend the PER-CHAPTER numbers, not just the "
                "total: a chapter's video cannot cover voice its own scenes do not hold, so a "
                "right total over a wrong split still breaks the film. A chapter budgeted at "
                "almost nothing means its reviewed window is about a second long — write "
                "barely anything there rather than borrowing from the total. Then "
                "get_scene_transcript for each scene you write for — the verbatim spoken "
                "words — plus get_reviews for what is visible. "
                "Write 1-2 sentences per scene, per chapter, in "
                f"{language} — the video's language, never switch languages mid-script. "
                "Write each chapter's "
                "lines in the SAME scene order the storyline lists that chapter's scenes in — "
                "voice and captions play back in that order, not the order you type them in. A "
                "chapter may list the same scene several times with different windows — write "
                "that scene's line(s) once for the chapter; they cover all its windows. "
                "GROUNDING RULE: every line must be supported by the scene's transcript "
                "(get_scene_transcript) or its review — quote or tightly paraphrase what is "
                "actually said and seen; never invent a product claim, feature name or "
                "capability neither of them contains. Generic marketing copy ('maximale "
                "Effizienz') is a defect, not a style. Keep the tone energetic with concrete "
                "value, no marketing fog, no sleepy phrasing — this has to hook a cold, "
                "scrolling viewer in the first seconds. "
                "SECOND BRAIN (when available): search_second_brain and read_brain_note reach "
                "the user's own notes for a correct product name, feature term or fact the "
                "transcript alone does not spell out — search there before writing a claim you "
                "are unsure of instead of guessing it. If these tools are not offered to you, "
                "none is configured for this run — ground everything in get_scene_transcript and "
                "get_reviews as usual. "
                "Save chapter by chapter via save_script_chapter (it "
                "merges — other chapters' lines stay untouched); fix and resave on validation "
                "errors. Verify with get_script once every chapter in the storyline has its "
                "lines written — it reports words against budget_words, a shortfall_pct, "
                "ungrounded_terms, and silent_chapters. silent_chapters names storyline "
                "chapters you wrote no line for at all: a run once left four of six silent and "
                "the film came out at 63% of its length. A chapter may stay silent ONLY as a "
                "deliberate visual beat — say so in that case; otherwise write it. "
                "A shortfall above 15% means the film comes out that much "
                "shorter than its target, so write the missing words BEFORE the voice is "
                "synthesized — but ONLY words the reviews support. The budget is a CEILING, "
                "never a quota: if the scenes do not hold enough to say, write less and let "
                "the film be shorter. A short honest film beats a full one that invents a "
                "capability the product does not have. ungrounded_terms lists the specifics "
                "no review ever saw — each one is unsupported, so cut it or rewrite it into "
                "something the frames actually show. Write to "
                "the budget ONCE — the voice gets synthesized and measured after you; "
                "correcting from that measurement is the coding_agent's job, not yours. Do not "
                "re-save the script to chase a length. "
                "- Every line is written FOR its scene: it must match what the scene SHOWS "
                "(SCENE FACTS) and may quote what is SAID there (get_scene_transcript). Never "
                "narrate things the scene does not show. "
                "- Before writing product or proper names, verify them with search_second_brain "
                "when the tool is available — the vault knows the real names (Rowboat vs n8n "
                "class of mistakes)."
            ),
            tool_names=(
                "set_board_language",
                "get_storyline",
                "script_budget",
                "get_reviews",
                "get_scene_context",
                "get_scene_transcript",
                "save_script_chapter",
                "get_script",
                # Task 10 (Transkript-Gates): named UNCONDITIONALLY — build_production_team
                # only wires an AssistantAgent's tools by name-lookup against the tool specs
                # actually built for this run (``[tools_by_name[n] for n in spec.tool_names if
                # n in tools_by_name]``), so a name with no matching spec is silently dropped,
                # never an error. When LAURA_SECONDBRAIN_PATH is unset, build_production_tool_
                # specs simply never builds these two, and the scene_author ends up without
                # them — no gating needed here to keep team construction safe.
                "search_second_brain",
                "read_brain_note",
            ),
            max_tool_iterations=6,
        ),
        AgentSpec(
            name="coding_agent",
            description="Executes voice, cutlist and render — never changes content decisions.",
            system_message=(
                "You are the Coding Agent. You execute; you do not change content decisions made "
                "by the Story Architect or Scene Author. Check board_status, then read the "
                "storyline (get_storyline), script (get_script) and reviews (get_reviews) so you "
                "know what you are rendering. Pipeline, strictly in order: "
                "synthesize_script_voice -> build_cutlist -> save_contact_sheet -> "
                "render_production. save_contact_sheet is the user's visual pre-render "
                "checkpoint — NEVER skip it, and re-run it after EVERY build_cutlist call (a "
                "cutlist save archives the current sheet). If the task or a user message says "
                "to stop at the contact sheet, end after save_contact_sheet and report its "
                "tiles instead of rendering. FRAMING LEVER: when the task or a user message "
                "asks for the full frame / no tight zoom (e.g. 'zeig das volle Bild', 'kein "
                "Zoom'), call build_cutlist with zoom=\"off\" — it drops every roi and zoom "
                "timing regardless of the storyline's window references; the storyline does "
                "NOT need to be re-saved for a framing change. Read the checks "
                "render_production returns: if voice does not fit (voice_fits is false), do not "
                "shorten it — build_cutlist already sizes segments to the voice's chapter audio "
                "windows, so rebuild the cutlist (then a fresh contact sheet) and render again; "
                "if it STILL fails, the scenes "
                "ran out of material — report that the storyline needs more/longer scenes. NEVER "
                "cut the voice — it is the script the team already agreed on, and the video must "
                "fit it, not the other way round. If story_covered is false, chapters the "
                "storyline planned were never written at all: that is the Scene Author's work, "
                "not yours — name the silent chapters in your report and stop rendering until "
                "they exist. "
                "Report the export_id and the checks verbatim so the QA Reviewer can verify them "
                "independently instead of trusting your summary. Use revert_artifact ONLY when "
                "the task or user message explicitly asks to go back to an earlier version — "
                "never as a routine step; afterwards rebuild whatever it invalidated through the "
                "normal pipeline, not by hand."
            ),
            tool_names=(
                "board_status",
                "get_storyline",
                "get_script",
                "get_reviews",
                "suggest_scenes_for_script",
                "synthesize_script_voice",
                "build_cutlist",
                "save_contact_sheet",
                "render_production",
                "revert_artifact",
            ),
            max_tool_iterations=10,
        ),
        AgentSpec(
            name="qa_reviewer",
            description="Judges the rendered export before it ships.",
            system_message=(
                "You are the QA Reviewer, the last gate before anything ships. Judge the "
                "RENDERED result, not the plan — start from board_status, then read the storyline "
                "(get_storyline) and script (get_script) so you know what a cold viewer should "
                "experience. Call review_export to look at real frames of the finished video and "
                "check story flow for a cold viewer, caption sync, zoom legibility, and that the "
                "full voice is present with nothing clipped mid-word. Your verdict is ship or "
                "revise, saved via save_qa_report together with concrete findings — every finding "
                "must name WHERE (which scene or timestamp) and WHAT is wrong, no vague words "
                "like 'looks off'. board_status's render_report entry carries target_ratio "
                "(the length of the DELIVERED file / target) when known — weigh a large "
                "shortfall in your verdict "
                "reasoning, but a shorter film is pre-authorized by the charter, so target_ratio "
                "alone is never grounds to revise. Use the board's contact sheet as your segment "
                "map — its tiles are labeled '<order> S<scene_number>'. You judge the board; you "
                "never WRITE to the chain except your own verdict — if the sheet or the render "
                "is missing, that is a revise finding for the coding_agent, not something you "
                "rebuild yourself. If save_qa_report returns validation errors, fix exactly "
                "what it names and save again; if it says there is no render on the board, "
                "report that as your finding instead of judging a film that does not exist."
            ),
            tool_names=(
                "board_status",
                "get_storyline",
                "get_script",
                "review_export",
                "save_qa_report",
            ),
            max_tool_iterations=6,
        ),
    ]


def build_production_team(
    db: Database,
    board: Board,
    config: AgentConfig,
    *,
    asset_id: str,
    stage: Stage = "A",
    deps: ProductionDeps | None = None,
    agent_names: tuple[str, ...] | None = None,
) -> MagenticOneGroupChat:
    """Assemble the v2 production Magentic-One team (roster + orchestrator model client).

    Mirrors :func:`laura.short_creator.magentic.build_magentic_team`: lazy autogen import, one
    shared agent-role model client for *stage*, and a separate orchestrator-role client (also
    *stage*-scoped). Tools are the in-process ``FunctionTool``s built from :func:`production_tools.
    build_production_tool_specs`'s ``ToolSpec``s for *asset_id* (the same lazy-FunctionTool-wrap
    pattern as :func:`laura.short_creator.toolset.build_function_tools`, inlined here since that
    builder needs ``board``/``asset_id``/``deps`` that the plain short-creator toolset does not).
    Raises a clear :class:`RuntimeError` if the optional ``autoshort`` extra is missing.

    ``agent_names`` narrows the roster (MP2, the bounded QA stage): ``None`` builds the full
    five-agent roster as before; a tuple builds ONLY those agents — the structural guarantee that
    a one-agent QA team cannot hold any write-capable creative tool. An unknown name is a
    programmer error (a caller misspelled a roster name), so it raises immediately rather than
    silently building a smaller-than-intended team.
    """
    try:
        from autogen_agentchat.agents import AssistantAgent
        from autogen_agentchat.teams import MagenticOneGroupChat
        from autogen_core.tools import FunctionTool
    except ImportError as exc:
        raise RuntimeError(
            "The short-creator needs the optional 'autoshort' extra. "
            "Install it with: uv sync --extra autoshort"
        ) from exc

    # Validate agent_names FIRST — fail fast on a misspelled roster name before spending work on
    # tool_specs/tools_by_name/model_client, which an unknown name would only throw away.
    specs_all = production_agent_specs(board.meta().language)
    if agent_names is not None:
        known = {s.name for s in specs_all}
        unknown = [n for n in agent_names if n not in known]
        if unknown:
            raise ValueError(f"unknown agent name(s): {unknown}")
        specs_all = [s for s in specs_all if s.name in agent_names]

    tool_specs = build_production_tool_specs(db, board, asset_id=asset_id, deps=deps)
    tools_by_name = {
        spec.name: FunctionTool(spec.func, name=spec.name, description=spec.description)
        for spec in tool_specs
    }
    model_client = build_model_client(config, role="agent", stage=stage)

    # list[Any]: participants wants list[ChatAgent]; AssistantAgent subclasses it, but list is
    # invariant (and ChatAgent is only importable with the extra installed).
    agents: list[Any] = [
        AssistantAgent(
            name=spec.name,
            model_client=model_client,
            tools=[tools_by_name[n] for n in spec.tool_names if n in tools_by_name],
            description=spec.description,
            system_message=spec.system_message,
            max_tool_iterations=spec.max_tool_iterations,
        )
        for spec in specs_all
    ]
    orchestrator = build_model_client(config, role="orchestrator", stage=stage)
    return MagenticOneGroupChat(participants=agents, model_client=orchestrator, max_turns=MAX_TURNS)
