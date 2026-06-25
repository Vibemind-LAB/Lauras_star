"""laura-deck MCP stdio server.

Registers all tool handlers on an MCP server and runs the stdio transport.
The ``mcp`` SDK is an OPTIONAL extra; this module guards the import so that
``import laura.mcp.server`` never hard-requires mcp to be installed.
The ``import mcp`` only runs inside ``main()`` at call-time.

Tools registered:
- next_action, batch_plan, batch_status, recipe_from_trace  (pure reads)
- analyze_video, extract_shorts                              (write: enqueue jobs)
- list_short_candidates, job_status, explain_candidate       (pure reads)

Usage (requires ``uv sync --extra mcp`` first):

    uv run laura-deck

or directly:

    uv run python -m laura.mcp.server
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _get_db_path() -> Path:
    """Locate the Laura SQLite database via Settings.load() (reads env vars)."""
    from ..config import Settings

    settings = Settings.load()
    return settings.db_path


def main() -> None:  # pragma: no cover
    """Entry point for the laura-deck MCP stdio server.

    Requires the ``mcp`` extra:  ``uv sync --extra mcp``.
    """
    try:
        import mcp.server.fastmcp as fastmcp  # noqa: PLC0415
    except ImportError as exc:
        raise SystemExit(
            "The 'mcp' extra is not installed. "
            "Run:  uv sync --extra mcp\n"
            f"Original error: {exc}"
        ) from exc

    from ..db.sqlite import SqliteDatabase
    from .tools import (
        tool_batch_plan,
        tool_batch_status,
        tool_deduplicate_shorts,
        tool_explain_candidate,
        tool_extract_shorts,
        tool_job_status,
        tool_list_short_candidates,
        tool_next_action,
        tool_recipe_from_trace,
        tool_render_short,
        tool_search_visual_moments,
        tool_similar_segments,
        tool_start_analysis,
        tool_visual_hook,
    )

    db_path = _get_db_path()
    db = SqliteDatabase(db_path)
    # Ensure schema is up-to-date (migrate is idempotent).
    db.migrate()

    mcp_server: Any = fastmcp.FastMCP("laura-deck")

    @mcp_server.tool(  # type: ignore[untyped-decorator]
        name="next_action",
        description=(
            "Return the deterministic next action to advance a short (video asset) "
            "to a finished reel.  Pass the asset's short_id (== asset_id in v1). "
            "Returns found=False when the asset does not exist."
        ),
    )
    def _next_action(short_id: str) -> dict[str, Any]:
        return tool_next_action(db, short_id)

    @mcp_server.tool(  # type: ignore[untyped-decorator]
        name="batch_plan",
        description=(
            "Resolve next_action for a list of short_ids and return an ordered "
            "batch plan with per-short hashes and a batch_hash merkle root. "
            "Unknown short_ids yield found=False without aborting others."
        ),
    )
    def _batch_plan(short_ids: list[str]) -> dict[str, Any]:
        return tool_batch_plan(db, short_ids)

    @mcp_server.tool(  # type: ignore[untyped-decorator]
        name="batch_status",
        description=(
            "Roll up next_action across a manifest of short_ids into stage counts "
            "(preparing/analyzing/analyze/cut/build/done/not_found) plus a "
            "needs_human count for assets with human-review policy."
        ),
    )
    def _batch_status(short_ids: list[str]) -> dict[str, Any]:
        return tool_batch_status(db, short_ids)

    @mcp_server.tool(  # type: ignore[untyped-decorator]
        name="recipe_from_trace",
        description=(
            "Reconstruct and verify a short_run's build recipe from its export trace. "
            "Pass the run_id (short_run.id).  Returns an empty dict when not found; "
            "otherwise returns short_id, status, recipe, recipe_hash, verified, available."
        ),
    )
    def _recipe_from_trace(run_id: str) -> dict[str, Any]:
        return tool_recipe_from_trace(db, run_id)

    # --- S7 agent tools -------------------------------------------------------

    @mcp_server.tool(  # type: ignore[untyped-decorator]
        name="analyze_video",
        description=(
            "Enqueue an analysis.run job for an asset (shot detection, ASR transcript, etc). "
            "Pass the asset_id. Returns ok=True with analysis_run_id and job_id on success, "
            "or ok=False with error='asset not found' when the asset does not exist. "
            "Poll job_status(job_id) to wait for completion before calling extract_shorts."
        ),
    )
    def _analyze_video(asset_id: str) -> dict[str, Any]:
        return tool_start_analysis(db, asset_id)

    @mcp_server.tool(  # type: ignore[untyped-decorator]
        name="extract_shorts",
        description=(
            "Enqueue a shorts.extract job to find short-form clip candidates in an asset. "
            "Requires a succeeded analysis run (call analyze_video first). "
            "Optional: min_duration_s, max_duration_s, max_candidates. "
            "Returns ok=True with job_id on success, or ok=False with an error message. "
            "Poll job_status(job_id) for completion, then list_short_candidates(asset_id)."
        ),
    )
    def _extract_shorts(
        asset_id: str,
        min_duration_s: float | None = None,
        max_duration_s: float | None = None,
        max_candidates: int | None = None,
    ) -> dict[str, Any]:
        return tool_extract_shorts(
            db,
            asset_id,
            min_duration_s=min_duration_s,
            max_duration_s=max_duration_s,
            max_candidates=max_candidates,
        )

    @mcp_server.tool(  # type: ignore[untyped-decorator]
        name="list_short_candidates",
        description=(
            "List all persisted short candidates for an asset, ordered by score (best first). "
            "Returns asset_id, count, and a candidates list. "
            "Call after extract_shorts job has succeeded."
        ),
    )
    def _list_short_candidates(asset_id: str) -> dict[str, Any]:
        return tool_list_short_candidates(db, asset_id)

    @mcp_server.tool(  # type: ignore[untyped-decorator]
        name="job_status",
        description=(
            "Check the status of a background job by job_id. "
            "Returns found=True with kind, "
            "status (queued/leased/running/succeeded/failed/canceled), "
            "attempts, result (parsed JSON), and error. "
            "Returns found=False when the job_id is unknown."
        ),
    )
    def _job_status(job_id: str) -> dict[str, Any]:
        return tool_job_status(db, job_id)

    @mcp_server.tool(  # type: ignore[untyped-decorator]
        name="explain_candidate",
        description=(
            "Return a human-readable explanation for one short candidate by candidate_id. "
            "Includes the overall score, top 2-3 score breakdown factors by value, "
            "qa_passed status, qa_issues list, and an explanation string. "
            "Returns found=False when the candidate_id is unknown."
        ),
    )
    def _explain_candidate(candidate_id: str) -> dict[str, Any]:
        return tool_explain_candidate(db, candidate_id)

    @mcp_server.tool(  # type: ignore[untyped-decorator]
        name="render_short",
        description=(
            "Render one short candidate to a finished vertical 9:16 MP4 with burned-in "
            "karaoke captions (and optional top hook text + loudness normalisation). "
            "Pass the candidate_id (optional: captions=True, hook_text=None, loudnorm=True). "
            "Creates an export and enqueues a shorts.render job; returns ok=True with "
            "export_id and job_id, or ok=False when the candidate is unknown. "
            "Poll job_status(job_id) for completion. This is the final step: "
            "analyze_video -> extract_shorts -> list_short_candidates -> explain_candidate -> "
            "render_short."
        ),
    )
    def _render_short(
        candidate_id: str,
        captions: bool = True,
        hook_text: str | None = None,
        loudnorm: bool = True,
    ) -> dict[str, Any]:
        return tool_render_short(
            db,
            candidate_id,
            captions=captions,
            hook_text=hook_text,
            loudnorm=loudnorm,
        )

    # --- VE5 visual tools -----------------------------------------------------

    @mcp_server.tool(  # type: ignore[untyped-decorator]
        name="get_similar_segments",
        description=(
            "Find the short candidates visually most similar to a given candidate "
            "within the same asset, using the per-frame visual embeddings. "
            "Pass asset_id and candidate_id (optional k, default 5). "
            "Image-image: needs no model. Returns ok=True with a 'similar' list "
            "(candidate_id, score, start_frame, end_frame_exclusive) sorted best-first, "
            "or ok=False with a reason when frame embeddings are missing "
            "(run shorts.embed_frames first) or the candidate is unknown."
        ),
    )
    def _get_similar_segments(
        asset_id: str, candidate_id: str, k: int = 5
    ) -> dict[str, Any]:
        return tool_similar_segments(db, asset_id, candidate_id, k=k)

    @mcp_server.tool(  # type: ignore[untyped-decorator]
        name="deduplicate_shorts",
        description=(
            "Group near-identical short candidates for an asset by visual similarity "
            "of their segment embeddings (greedy, highest-score keeper wins). "
            "Pass asset_id (optional threshold, default 0.9). Image-image: needs no model. "
            "Returns ok=True with groups (keep + duplicates), kept, and dropped id lists, "
            "or ok=False with a reason when frame embeddings are missing."
        ),
    )
    def _deduplicate_shorts(asset_id: str, threshold: float = 0.9) -> dict[str, Any]:
        return tool_deduplicate_shorts(db, asset_id, threshold=threshold)

    @mcp_server.tool(  # type: ignore[untyped-decorator]
        name="score_visual_hook",
        description=(
            "Score how strong a short candidate's visual opening is, blending the "
            "visual shift across the start cut with the continuity of the first seconds. "
            "Pass asset_id and candidate_id. Image-image: needs no model. "
            "Returns ok=True with visual_shift_at_start, opening_continuity, hook_score, "
            "and an explanation, or ok=False with a reason when frame embeddings are "
            "missing or the candidate is unknown."
        ),
    )
    def _score_visual_hook(asset_id: str, candidate_id: str) -> dict[str, Any]:
        return tool_visual_hook(db, asset_id, candidate_id)

    @mcp_server.tool(  # type: ignore[untyped-decorator]
        name="search_visual_moments",
        description=(
            "Search an asset's frames by a natural-language query (text->image), "
            "ranking frames by CLIP similarity to the query. "
            "Pass asset_id and query (optional k, default 10). "
            "REQUIRES the optional 'semantic'/visual extra (CLIP text encoder); without it "
            "this returns ok=False with a reason rather than failing. "
            "Returns ok=True with a 'moments' list (frame, time_s, score) sorted best-first."
        ),
    )
    def _search_visual_moments(
        asset_id: str, query: str, k: int = 10
    ) -> dict[str, Any]:
        return tool_search_visual_moments(db, asset_id, query, k=k)

    logger.info("laura-deck MCP server starting (db=%s)", db_path)
    mcp_server.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
