"""The short-creator agent roster (AutoGen ``AssistantAgent``s).

``agent_specs`` is pure data — the roster, system prompts and tool assignment,
testable without autogen or a db. ``build_agents`` is the autogen-touching
constructor: it builds one shared agent-role model client (from
:mod:`providers`) and the in-process tools (from :mod:`toolset`), then wires an
``AssistantAgent`` per spec.

Iteration 4 wires the agents to the existing 10 tools. The Describer's dedicated
VLM frame-description tool and the Transcript-Analyst's ±15s transcript-window
tool land in Iteration 4b; until then those two agents reason from the context
the team passes them (Describer also has the visual-signal tools below).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..db.database import Database
from .providers import AgentConfig, Stage, build_model_client
from .toolset import build_function_tools

if TYPE_CHECKING:  # annotation only — never imported at runtime
    from autogen_agentchat.agents import AssistantAgent


@dataclass(frozen=True)
class AgentSpec:
    """One team member: a name, a one-line description, a system prompt, and tool names.

    ``max_tool_iterations`` — tool rounds per turn. AssistantAgent defaults to ONE, which cannot
    chain build_roughcut → render_timeline within a single graph turn (live-run finding).
    """

    name: str
    description: str
    system_message: str
    tool_names: tuple[str, ...]
    max_tool_iterations: int = 1


def agent_specs() -> list[AgentSpec]:
    """The fixed short-creator roster. Pure — no autogen, no db.

    Tool names must exist in :func:`toolset.build_tool_specs` (cross-checked in tests).
    """
    return [
        AgentSpec(
            name="scout",
            description="Finds candidate moments in the analyzed video for the topic.",
            system_message=(
                "You are the Scout. Answer in the task's language — never switch languages. "
                "Given a topic, find the most relevant candidate moments in "
                "the analyzed video. Use search_visual_moments (text->frame) and extract_shorts to "
                "surface candidates, then list_short_candidates to read them back. Return the "
                "candidate ids and frame ranges that best match the topic."
            ),
            tool_names=("search_visual_moments", "extract_shorts", "list_short_candidates"),
            max_tool_iterations=4,
        ),
        AgentSpec(
            name="describer",
            description="Describes what is visually happening in each candidate.",
            system_message=(
                "You are the Describer. For each candidate moment, describe what is visually "
                "happening. Use describe_moment on a representative frame to get a VLM "
                "description, and score_visual_hook to gauge the visual opening. Return a short "
                "visual description per candidate id."
            ),
            tool_names=("describe_moment", "score_visual_hook"),
            max_tool_iterations=4,
        ),
        AgentSpec(
            name="transcript_analyst",
            description="Summarizes what is said around each candidate (±15s).",
            system_message=(
                "You are the Transcript Analyst. FIRST call transcript_overview to see the whole "
                "video in blocks, and summarize each block in one line — that map guides the "
                "Director. Then, for specific candidates, use transcript_window on their center "
                "frame. window_frames is in FRAMES, not seconds — leave it at the default 450 "
                "(±15s at 30fps)."
            ),
            tool_names=("transcript_overview", "transcript_window"),
            max_tool_iterations=4,
        ),
        AgentSpec(
            name="director",
            description="Selects and orders the segments into a coherent short.",
            system_message=(
                "You are the Director. FIRST call rank_scenes_by_topic(asset_id, topic) — the "
                "transcript decides which scenes actually carry the topic. The topic is what the "
                "video is ABOUT (content words from the task/transcript) — never style words "
                "like 'energetisch'. Combine that with the "
                "visual descriptions and transcript summaries to select and order the best "
                "material within the target length (scene_transcripts shows each scene's words; "
                "list_short_candidates/explain_candidate compare candidates). End with exactly "
                "one line: CHOSEN: <candidate_id>, <candidate_id>, ... in play order."
            ),
            tool_names=(
                "rank_scenes_by_topic",
                "scene_transcripts",
                "list_short_candidates",
                "explain_candidate",
                "score_visual_hook",
                "get_similar_segments",
                "transcript_overview",
            ),
            max_tool_iterations=5,
        ),
        AgentSpec(
            name="editor",
            description="Assembles the chosen segments and renders the short.",
            system_message=(
                "You are the Editor. You MUST use your tools — never answer in prose only. "
                "If the task names SCENE NUMBERS (e.g. 'Szene 1, 2, 19') or PLATFORMS "
                "(insta/x/linkedin): call render_scenes(asset_id, scene_numbers, formats) — one "
                "export per format. If the Transcript Master reported 'VOICEOVER path=...', pass "
                "voiceover_path=<that path> and voiceover_text=<its SCRIPT> to render_scenes OR "
                "render_short so the new voice replaces the original audio. Else, for a SHORT "
                "of a target "
                "length: if the task ALSO names a scene count or per-scene length (e.g. "
                "'15 Szenen à 4s'), call render_short(asset_id=..., target_seconds=..., "
                "max_segments=<count>, max_segment_seconds=<seconds per scene>) — it picks the "
                "best scenes itself. Otherwise Step 1 — use the "
                "Director's CHOSEN candidate ids, otherwise call pick_best_candidates(asset_id, "
                "target_seconds). Step 2 — call render_short with those candidate_ids. "
                "IMPORTANT: if the Describer saw screen content / UI (apps, browser, code), pass "
                "fit='blur' so nothing is cropped off; only real camera footage uses the default "
                "crop. Reply exactly: EDITED export_id=<id> (one per render). Only for full-video "
                "tasks WITHOUT a target length: call build_roughcut with the asset_id, then "
                "render_timeline with its timeline_id, and reply: EDITED timeline_id=<id> "
                "export_id=<id>. If a call fails, report the error — do not pretend."
            ),
            tool_names=(
                "render_scenes",
                "pick_best_candidates",
                "pick_best_candidate",
                "render_short",
                "build_roughcut",
                "render_timeline",
                "job_status",
            ),
            max_tool_iterations=8,
        ),
        AgentSpec(
            name="transcript_master",
            description="Rewrites the script from the chosen scenes and voices it (ElevenLabs).",
            system_message=(
                "You are the Transcript Master. ONLY act when the task asks for a new voice or a "
                "new script/transcript — trigger phrases include: re-voice, neu einsprechen, new "
                "voiceover, neue stimme, neues skript, new script, transkript neu, transcript "
                "new (misspellings like 'transkipt new' count too). "
                "Otherwise reply exactly: SKIP. When active: read "
                "the chosen scenes' words via scene_transcripts, then write a tight, ENERGETIC "
                "script in the language of the task/video (German etc.) — NEVER in English — "
                "following the user's direction (tone, length, CTA) from the task. Then call "
                "synthesize_voiceover(asset_id, script). Reply exactly: "
                "VOICEOVER path=<voiceover_path>\nSCRIPT: <the script>. If synthesis fails, "
                "report the reason instead."
            ),
            tool_names=("scene_transcripts", "synthesize_voiceover"),
            max_tool_iterations=4,
        ),
        AgentSpec(
            name="qa",
            description="Judges whether the short matches the topic and is coherent.",
            system_message=(
                "You are the QA gate. Answer in the task's language — never switch languages. "
                "Judge whether the produced short matches the topic and is coherent. FIRST: if "
                "the Editor's message contains 'EDITED export_id=<id>', call export_status with "
                "that id — it WAITS for the render. status 'ready' means the short EXISTS: that "
                "is NOT weak. Weak for lack of output ONLY when there is no EDITED line at all, "
                "or export_status says found=false or status 'error'. Then call "
                "check_voice_alignment with the SAME export id (it checks every segment): "
                "clipped words mean the voice is cut mid-word — verdict weak. Respond with a "
                "verdict (good or weak) and a short reason; if weak, say what to improve."
            ),
            tool_names=(
                "export_status",
                "explain_candidate",
                "score_visual_hook",
                "check_voice_alignment",
            ),
            max_tool_iterations=3,
        ),
    ]


def build_agents(db: Database, config: AgentConfig, *, stage: Stage = "A") -> list[AssistantAgent]:
    """Construct one ``AssistantAgent`` per spec (lazy autogen import).

    All agents share one agent-role model client for *stage*; tools are the
    in-process FunctionTools selected by each spec's ``tool_names``. Raises a
    clear :class:`RuntimeError` if the optional ``autoshort`` extra is missing.
    """
    try:
        from autogen_agentchat.agents import AssistantAgent
    except ImportError as exc:
        raise RuntimeError(
            "The short-creator needs the optional 'autoshort' extra. "
            "Install it with: uv sync --extra autoshort"
        ) from exc

    tools_by_name = {tool.name: tool for tool in build_function_tools(db)}
    model_client = build_model_client(config, role="agent", stage=stage)
    return [
        AssistantAgent(
            name=spec.name,
            model_client=model_client,
            tools=[tools_by_name[n] for n in spec.tool_names if n in tools_by_name],
            description=spec.description,
            system_message=spec.system_message,
            max_tool_iterations=spec.max_tool_iterations,
        )
        for spec in agent_specs()
    ]
