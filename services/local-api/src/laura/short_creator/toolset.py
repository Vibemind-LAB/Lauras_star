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
from typing import TYPE_CHECKING, Any

from ..db import repos
from ..db.database import Database
from ..mcp import tools as t
from . import context

if TYPE_CHECKING:  # annotation only — never imported at runtime
    from autogen_core.tools import FunctionTool

# Injectable for tests; the extract wait polls with this sleeper.
_sleep: Callable[[float], None] = time.sleep
EXTRACT_WAIT_SECONDS = 120
_EXTRACT_POLL_INTERVAL = 2.0


# Weight of the length-fit penalty when ranking candidates for a target duration: score minus
# (relative length deviation × weight). 2.0 lets a strong-but-far candidate lose to a good fit.
_LENGTH_FIT_WEIGHT = 2.0


def _asset_fps(db: Database, asset_id: str) -> float:
    """The asset's frame rate (probed, or the project's sequence rate); 30.0 when unknown."""
    asset = repos.get_asset(db, asset_id)
    if asset is None:
        return 30.0
    rate = context._frame_rate(db, asset)
    if rate is None or rate[1] == 0:
        return 30.0
    return rate[0] / rate[1]


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

    def render_short(
        candidate_id: str, captions: bool = True, hook_text: str | None = None
    ) -> dict[str, Any]:
        """Render ONE chosen candidate as a vertical 9:16 short with karaoke captions."""
        return t.tool_render_short(db, candidate_id, captions=captions, hook_text=hook_text)

    def check_voice_alignment(candidate_id: str) -> dict[str, Any]:
        """Verify the candidate's cut clips no word (voice aligned to the scene)."""
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
    ]
    return [
        ToolSpec(name=f.__name__, description=(f.__doc__ or "").strip(), func=f) for f in funcs
    ]


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
