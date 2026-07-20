"""In-process AutoGen tools adapting Laura's existing MCP ``tool_*`` functions.

The short-creator agents run in the SAME backend process as the db and the
``tool_*`` functions, so there is no reason to spawn the stdio MCP server
(``mcp/server.py``) and round-trip over a pipe. We wrap the same functions as
in-process tools, injecting ``db`` exactly as the MCP server does — this mirrors
``mcp/server.py``'s wrappers but targets AutoGen's ``FunctionTool``.

Decision (design spec, Iteration 3): in-process ``FunctionTool``, not an MCP
subprocess. ``build_tool_specs`` is pure (no autogen); ``build_function_tools``
is the only autogen-touching function and imports the optional extra lazily.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..db import repos
from ..db.database import Database
from ..mcp import tools as t
from ..util import new_id
from . import context
from .board_models import FORMAT_PRESETS
from .voice import resolve_voice_backend

if TYPE_CHECKING:  # annotation only — never imported at runtime
    from autogen_core.tools import FunctionTool

# Injectable for tests; the extract wait polls with this sleeper.
_sleep: Callable[[float], None] = time.sleep
EXTRACT_WAIT_SECONDS = 120
# export_status waits a render out (a ~60-90s reel takes 1-2 min): a 7B QA cannot be trusted
# to reason about a transient "rendering" state (live-run finding: it read found=True,
# status=rendering and still concluded "not found -> weak").
RENDER_WAIT_SECONDS = 240
_EXTRACT_POLL_INTERVAL = 2.0


# Weight of the length-fit penalty when ranking candidates for a target duration: score minus
# (relative length deviation × weight). 2.0 lets a strong-but-far candidate lose to a good fit.
_LENGTH_FIT_WEIGHT = 2.0


def _scene_segments(
    db: Database, asset_id: str, scene_numbers: list[int]
) -> list[tuple[int, int]] | None:
    """SOURCE segments for 1-based rough-cut scene numbers, in the given order. None = no scenes."""
    asset = repos.get_asset(db, asset_id)
    if asset is None:
        return None
    timeline = repos.get_or_create_asset_rough_cut(db, str(asset["project_id"]), asset_id)
    scenes = repos.list_scenes(db, str(timeline["id"]))
    if not scenes:
        return None
    by_number = {int(s["order_index"]) + 1: s for s in scenes}
    clips = repos.list_timeline_clips(db, str(timeline["id"]))
    segments: list[tuple[int, int]] = []
    for number in scene_numbers:
        scene = by_number.get(int(number))
        if scene is None:
            continue
        segments.extend(
            context._scene_src_ranges(
                clips,
                seq_in=int(scene["seq_in_frame"]),
                seq_out_exclusive=int(scene["seq_out_frame_exclusive"]),
            )
        )
    return segments


def _asset_fps(db: Database, asset_id: str) -> float:
    """The asset's frame rate (probed, or the project's sequence rate); 30.0 when unknown."""
    asset = repos.get_asset(db, asset_id)
    if asset is None:
        return 30.0
    rate = context._frame_rate(db, asset)
    if rate is None or rate[1] == 0:
        return 30.0
    return rate[0] / rate[1]


def _pick_best_segments(
    db: Database,
    asset_id: str,
    target_seconds: int = 20,
    max_segments: int = 4,
    max_segment_seconds: float | None = None,
) -> dict[str, Any]:
    """Deterministic multi-scene pick: greedy top-score, non-overlapping, ~target, chronological.

    With *max_segment_seconds*, each chosen candidate is trimmed to at most that length
    (integer frames from its start, end-exclusive) — so many SHORT scenes fit a target
    ("15 Szenen à 4s" tasks) instead of a few long ones. Overlap is checked against the
    candidates' ORIGINAL ranges (conservative).
    """
    listing = t.tool_list_short_candidates(db, asset_id)
    rows = [c for c in listing.get("candidates", []) if not c.get("rejected")]
    if not rows:
        return {"ok": False, "reason": "no candidates; run extract_shorts first"}
    fps = _asset_fps(db, asset_id)
    target = max(1, int(target_seconds))
    trim_frames: int | None = None
    if max_segment_seconds is not None and float(max_segment_seconds) > 0:
        trim_frames = max(1, round(float(max_segment_seconds) * fps))

    chosen: list[dict[str, Any]] = []
    total_s = 0.0
    for c in sorted(rows, key=lambda r: float(r.get("score") or 0.0), reverse=True):
        start, end = int(c["start_frame"]), int(c["end_frame_exclusive"])
        if any(start < p["end"] and p["start"] < end for p in chosen):
            continue  # overlaps an already chosen scene
        end_eff = min(end, start + trim_frames) if trim_frames else end
        duration_s = (end_eff - start) / fps
        if total_s + duration_s > target * 1.25 and chosen:
            continue  # would overshoot the target noticeably — try shorter ones
        chosen.append(
            {"row": c, "start": start, "end": end, "end_eff": end_eff, "duration_s": duration_s}
        )
        total_s += duration_s
        if total_s >= target or len(chosen) >= max(1, int(max_segments)):
            break
    chosen.sort(key=lambda p: int(p["start"]))  # story order
    return {
        "ok": True,
        "candidate_ids": [str(p["row"]["id"]) for p in chosen],
        "segments": [
            {
                "candidate_id": str(p["row"]["id"]),
                "start_frame": int(p["start"]),
                "end_frame_exclusive": int(p["end_eff"]),
                "duration_s": round(float(p["duration_s"]), 2),
                "score": float(p["row"].get("score") or 0.0),
                "trimmed": int(p["end_eff"]) < int(p["end"]),
            }
            for p in chosen
        ],
        "total_seconds": round(total_s, 2),
    }


def _wait_for_job(db: Database, job_id: str, *, timeout_s: float) -> str:
    """Poll a job until it reaches a terminal status (or timeout). Returns the last status.

    Agents cannot sleep, but tools can: extraction is an async job, and a scout that lists
    candidates immediately after enqueueing sees an empty list (live-run finding on a freshly
    imported recording).
    """
    deadline = time.monotonic() + timeout_s
    status = "unknown"
    while time.monotonic() < deadline:
        job = t.tool_job_status(db, job_id)
        status = str(job.get("status") or "unknown")
        if status in ("succeeded", "failed", "canceled"):
            return status
        _sleep(_EXTRACT_POLL_INTERVAL)
    return status


@dataclass(frozen=True)
class ToolSpec:
    """One agent-facing tool: a stable name, an LLM-facing description, a db-bound callable."""

    name: str
    description: str
    func: Callable[..., dict[str, Any]]


def build_tool_specs(db: Database) -> list[ToolSpec]:
    """Wrap Laura's ``tool_*`` functions as agent-facing callables (``db`` captured).

    Pure — no autogen. Signatures stay clean (no ``db``) and typed, and each
    docstring becomes the tool description AutoGen shows the model.
    """

    def next_action(short_id: str) -> dict[str, Any]:
        """Deterministic next step to advance an asset toward a finished short."""
        return t.tool_next_action(db, short_id)

    def search_visual_moments(asset_id: str, query: str, k: int = 10) -> dict[str, Any]:
        """Rank an asset's frames by CLIP similarity to a natural-language query (text->image)."""
        return t.tool_search_visual_moments(db, asset_id, query, k=k)

    def extract_shorts(
        asset_id: str,
        min_duration_s: float | None = None,
        max_duration_s: float | None = None,
        max_candidates: int | None = None,
    ) -> dict[str, Any]:
        """Find short-form clip candidates in an asset (waits until they are ready)."""
        result = t.tool_extract_shorts(
            db,
            asset_id,
            min_duration_s=min_duration_s,
            max_duration_s=max_duration_s,
            max_candidates=max_candidates,
        )
        job_id = result.get("job_id")
        if result.get("ok") and job_id:
            status = _wait_for_job(db, str(job_id), timeout_s=EXTRACT_WAIT_SECONDS)
            listing = t.tool_list_short_candidates(db, asset_id)
            return {**result, "job_final_status": status, "count": listing.get("count", 0)}
        return result

    def list_short_candidates(asset_id: str) -> dict[str, Any]:
        """List persisted short candidates for an asset, best score first."""
        return t.tool_list_short_candidates(db, asset_id)

    def explain_candidate(candidate_id: str) -> dict[str, Any]:
        """Explain one short candidate: overall score, top factors, QA status."""
        return t.tool_explain_candidate(db, candidate_id)

    def score_visual_hook(asset_id: str, candidate_id: str) -> dict[str, Any]:
        """Score a candidate's visual opening strength (start shift + opening continuity)."""
        return t.tool_visual_hook(db, asset_id, candidate_id)

    def get_similar_segments(asset_id: str, candidate_id: str, k: int = 5) -> dict[str, Any]:
        """Find the short candidates visually most similar to a given one (image-image)."""
        return t.tool_similar_segments(db, asset_id, candidate_id, k=k)

    def build_roughcut(asset_id: str) -> dict[str, Any]:
        """Build a rough cut with scenes from an asset's succeeded analysis (idempotent)."""
        return t.tool_build_roughcut(db, asset_id)

    def render_timeline(timeline_id: str) -> dict[str, Any]:
        """Render a timeline (rough cut / scene / sequence) to a finished mp4 export."""
        return t.tool_render_timeline(db, timeline_id)

    def job_status(job_id: str) -> dict[str, Any]:
        """Check a background job's status and result by job_id."""
        return t.tool_job_status(db, job_id)

    def describe_moment(asset_id: str, frame: int) -> dict[str, Any]:
        """Describe what is visibly happening at a candidate frame (VLM; empty if no model)."""
        return context.describe_moment(db, asset_id, frame)

    def transcript_window(
        asset_id: str, center_frame: int, window_frames: int = 450
    ) -> dict[str, Any]:
        """Summarize what is said around a candidate frame (+/- window) from the transcript."""
        return context.transcript_window(db, asset_id, center_frame, window_frames)

    def transcript_overview(asset_id: str, blocks: int = 8) -> dict[str, Any]:
        """The whole transcript grouped into time blocks — summarize the video per section."""
        return context.transcript_overview(db, asset_id, blocks)

    def scene_transcripts(asset_id: str) -> dict[str, Any]:
        """Per rough-cut scene (1-based number): what is said in it (needs build_roughcut)."""
        return context.scene_transcripts(db, asset_id)

    def rank_scenes_by_topic(asset_id: str, topic: str, k: int = 10) -> dict[str, Any]:
        """The scenes most relevant to a topic, ranked by their transcript text."""
        return context.rank_scenes_by_topic(db, asset_id, topic, k)

    def render_scenes(
        asset_id: str,
        scene_numbers: list[int],
        formats: list[str] | None = None,
        hook_text: str | None = None,
        fit: str = "blur",
        voiceover_path: str | None = None,
        voiceover_text: str | None = None,
    ) -> dict[str, Any]:
        """Render chosen rough-cut scenes (1-based numbers, in order) — one export PER format.

        formats: any of "insta" (9:16), "x" (16:9), "linkedin" (1:1); default ["insta"].
        fit="blur" letterboxes onto a blurred background (screen recordings / UI content).
        voiceover_path (+voiceover_text) replaces the original audio with a synthesized voice
        (see synthesize_voiceover); captions then follow the new script.
        """
        segments = _scene_segments(db, asset_id, [int(n) for n in scene_numbers])
        if segments is None:
            return {"ok": False, "reason": "no scenes; build_roughcut first"}
        if not segments:
            return {"ok": False, "reason": "scene numbers matched nothing"}
        wanted = [f.lower() for f in (formats or ["insta"])]
        renders: list[dict[str, Any]] = []
        for name in wanted:
            preset = FORMAT_PRESETS.get(name)
            if preset is None:
                renders.append({"format": name, "ok": False, "error": "unknown format"})
                continue
            vertical, out_size = preset
            result = t.tool_render_segments(
                db,
                asset_id,
                segments,
                hook_text=hook_text,
                fit=fit,
                vertical=vertical,
                out_size=out_size,
                voiceover_path=voiceover_path,
                voiceover_text=voiceover_text,
            )
            renders.append({"format": name, **result})
        ok = any(r.get("ok") for r in renders)
        return {"ok": ok, "segments": len(segments), "renders": renders}

    def synthesize_voiceover(asset_id: str, script: str) -> dict[str, Any]:
        """Speak a new script with the configured ElevenLabs voice; returns the audio path.

        Graceful without LAURA_ELEVENLABS_API_KEY (ok=False with a reason). Pass the returned
        voiceover_path (+ the script as voiceover_text) to render_scenes to replace the
        original audio.
        """
        backend = resolve_voice_backend()
        if backend is None:
            return {"ok": False, "reason": "no LAURA_ELEVENLABS_API_KEY configured"}
        text = script.strip()
        if not text:
            return {"ok": False, "reason": "empty script"}
        asset = repos.get_asset(db, asset_id)
        if asset is None:
            return {"ok": False, "reason": "asset not found"}
        project = repos.get_project(db, str(asset["project_id"]))
        if project is None:
            return {"ok": False, "reason": "project not found"}
        out_path = Path(str(project["workspace_root"])) / "voiceovers" / f"{new_id()}.mp3"
        result = backend.synthesize(text, out_path)
        if not result.get("ok"):
            return {"ok": False, "reason": str(result.get("reason") or "synthesis failed")}
        return {"ok": True, "voiceover_path": str(out_path), "chars": len(text)}

    def render_short(
        candidate_id: str = "",
        candidate_ids: list[str] | None = None,
        captions: bool = True,
        hook_text: str | None = None,
        fit: str = "blur",
        asset_id: str = "",
        target_seconds: int | None = None,
        max_segments: int | None = None,
        max_segment_seconds: float | None = None,
        voiceover_path: str | None = None,
        voiceover_text: str | None = None,
    ) -> dict[str, Any]:
        """Render a vertical 9:16 short with captions — from chosen candidates OR auto-picked.

        Mode 1: pass candidate_ids (ordered) from the Director / pick tools. Mode 2 (AUTO):
        pass asset_id + target_seconds (+ max_segments, + max_segment_seconds) and the best
        non-overlapping scenes are picked deterministically — e.g. "~60s, 15 Szenen à 4s" →
        render_short(asset_id=..., target_seconds=60, max_segments=15, max_segment_seconds=4).
        fit="blur" letterboxes onto a blurred background — use it for screen recordings / UI
        content (a crop cuts them off). voiceover_path (+voiceover_text) replaces the original
        audio with the synthesized voice (see synthesize_voiceover); captions follow the script.
        """
        if not candidate_id and not candidate_ids and asset_id and target_seconds:
            picked = _pick_best_segments(
                db,
                asset_id,
                target_seconds=int(target_seconds),
                max_segments=int(max_segments or 4),
                max_segment_seconds=max_segment_seconds,
            )
            if not picked.get("ok"):
                return picked
            segments = [
                (int(s["start_frame"]), int(s["end_frame_exclusive"])) for s in picked["segments"]
            ]
            result = t.tool_render_segments(
                db,
                asset_id,
                segments,
                captions=captions,
                hook_text=hook_text,
                fit=fit,
                voiceover_path=voiceover_path,
                voiceover_text=voiceover_text,
            )
            return {
                **result,
                "picked_candidate_ids": picked["candidate_ids"],
                "segments": len(segments),
                "total_seconds": picked["total_seconds"],
            }
        first = candidate_id or (candidate_ids[0] if candidate_ids else "")
        return t.tool_render_short(
            db,
            first,
            candidate_ids=candidate_ids,
            captions=captions,
            hook_text=hook_text,
            fit=fit,
            voiceover_path=voiceover_path,
            voiceover_text=voiceover_text,
        )

    def export_status(export_id: str) -> dict[str, Any]:
        """Check a rendered export by export_id — WAITS for the render to finish.

        For the QA gate: verify the Editor's "EDITED export_id=..." here. status "ready"
        (with path) means the short exists; "error" carries the reason; found=False means
        nothing was produced. No reasoning about a transient "rendering" state is needed —
        this tool waits it out (bounded).
        """
        row = repos.get_export(db, export_id)
        if row is None:
            return {"found": False, "export_id": export_id}
        deadline = time.monotonic() + RENDER_WAIT_SECONDS
        while str(row.get("status")) == "rendering" and time.monotonic() < deadline:
            _sleep(_EXTRACT_POLL_INTERVAL)
            row = repos.get_export(db, export_id) or row
        return {
            "found": True,
            "export_id": export_id,
            "status": str(row.get("status") or ""),
            "path": row.get("path"),
            "error": row.get("error"),
        }

    def check_voice_alignment(candidate_id: str) -> dict[str, Any]:
        """Verify the cut clips no word (voice aligned). Accepts a candidate id OR export id."""
        return context.check_voice_alignment(db, candidate_id)

    def pick_best_candidate(asset_id: str, target_seconds: int = 20) -> dict[str, Any]:
        """The best non-rejected candidate near the target length (deterministic ranking)."""
        listing = t.tool_list_short_candidates(db, asset_id)
        rows = [c for c in listing.get("candidates", []) if not c.get("rejected")]
        if not rows:
            return {"ok": False, "reason": "no candidates; run extract_shorts first"}
        fps = _asset_fps(db, asset_id)
        target = max(1, int(target_seconds))

        def ranked(c: dict[str, Any]) -> float:
            duration_s = (int(c["end_frame_exclusive"]) - int(c["start_frame"])) / fps
            fit_penalty = abs(duration_s - target) / target * _LENGTH_FIT_WEIGHT
            return float(c.get("score") or 0.0) - fit_penalty

        best = max(rows, key=ranked)
        duration_s = (int(best["end_frame_exclusive"]) - int(best["start_frame"])) / fps
        return {
            "ok": True,
            "candidate_id": str(best["id"]),
            "score": float(best.get("score") or 0.0),
            "duration_s": round(duration_s, 2),
            "start_frame": int(best["start_frame"]),
            "end_frame_exclusive": int(best["end_frame_exclusive"]),
        }

    def pick_best_candidates(
        asset_id: str,
        target_seconds: int = 20,
        max_segments: int = 4,
        max_segment_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Several top scenes ACROSS the video for a multi-scene short (deterministic).

        Greedy by score: take non-overlapping candidates until the total reaches the target
        length (or max_segments), then return them in CHRONOLOGICAL order — the story order the
        Editor should render. max_segment_seconds trims each scene to at most that length
        (frame-accurate) — use it for "N Szenen à M Sekunden" tasks.
        """
        return _pick_best_segments(
            db,
            asset_id,
            target_seconds=target_seconds,
            max_segments=max_segments,
            max_segment_seconds=max_segment_seconds,
        )

    funcs: list[Callable[..., dict[str, Any]]] = [
        next_action,
        search_visual_moments,
        extract_shorts,
        list_short_candidates,
        explain_candidate,
        score_visual_hook,
        get_similar_segments,
        build_roughcut,
        render_timeline,
        job_status,
        describe_moment,
        transcript_window,
        transcript_overview,
        render_short,
        check_voice_alignment,
        pick_best_candidate,
        pick_best_candidates,
        scene_transcripts,
        rank_scenes_by_topic,
        render_scenes,
        synthesize_voiceover,
        export_status,
    ]
    return [ToolSpec(name=f.__name__, description=(f.__doc__ or "").strip(), func=f) for f in funcs]


def build_function_tools(db: Database) -> list[FunctionTool]:
    """Wrap the tool specs as AutoGen ``FunctionTool``s (lazy autogen import).

    Raises a clear :class:`RuntimeError` (not ``ImportError``) if the optional
    ``autoshort`` extra is not installed.
    """
    try:
        from autogen_core.tools import FunctionTool
    except ImportError as exc:
        raise RuntimeError(
            "The short-creator needs the optional 'autoshort' extra. "
            "Install it with: uv sync --extra autoshort"
        ) from exc
    return [
        FunctionTool(spec.func, name=spec.name, description=spec.description)
        for spec in build_tool_specs(db)
    ]
