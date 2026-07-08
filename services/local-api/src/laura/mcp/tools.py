"""laura-deck MCP tool handlers — testable plain functions, NO import mcp.

Each function takes an explicit ``db: Database`` (testable with in-memory DB)
and returns a JSON-serialisable dict.

**Read/write split:**

* Pure reads (no DB writes): ``tool_next_action``, ``tool_batch_plan``,
  ``tool_batch_status``, ``tool_recipe_from_trace``, ``tool_list_short_candidates``,
  ``tool_job_status``, ``tool_explain_candidate``.
* Writers: ``tool_start_analysis``, ``tool_extract_shorts``, ``tool_render_timeline``
  (enqueue jobs); ``tool_build_roughcut`` (synchronous rough-cut/scene build).

These are thin wrappers over the existing pure resolvers:
- ``resolve_next_action``  (api.shorts)
- ``plan_batch``           (api.batch)
- ``batch_status``         (api.batch)
- ``recipe_from_trace``    (api.batch)
- ``repos.create_analysis_run`` / ``enqueue`` / ``repos.get_job``
- ``repos.list_shorts_candidates_by_asset`` / ``repos.get_short_candidate``
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .. import PIPELINE_VERSION
from ..analysis import visual_query
from ..api.batch import batch_status, plan_batch, recipe_from_trace
from ..api.models import AnalysisStart
from ..api.shorts import resolve_next_action
from ..db import repos
from ..db.database import Database
from ..jobs.queues import queue_for
from ..jobs.runner import enqueue
from ..scenes.build import autobuild_asset_edit_ready

logger = logging.getLogger(__name__)

__all__ = [
    "tool_next_action",
    "tool_batch_plan",
    "tool_batch_status",
    "tool_recipe_from_trace",
    "tool_start_analysis",
    "tool_build_roughcut",
    "tool_render_timeline",
    "tool_extract_shorts",
    "tool_list_short_candidates",
    "tool_job_status",
    "tool_explain_candidate",
    "tool_render_short",
    "tool_render_segments",
    "tool_similar_segments",
    "tool_deduplicate_shorts",
    "tool_visual_hook",
    "tool_search_visual_moments",
]


def tool_next_action(db: Database, short_id: str) -> dict[str, Any]:
    """Return the next action for a short (asset).

    Returns the same shape as ``NextActionOut.model_dump()`` when found, or
    ``{"found": False, "short_id": short_id}`` when the asset does not exist.
    """
    action = resolve_next_action(db, short_id)
    if action is None:
        logger.debug("tool_next_action: short_id=%r not found", short_id)
        return {"found": False, "short_id": short_id}
    result: dict[str, Any] = action.model_dump()
    result["found"] = True
    return result


def tool_batch_plan(db: Database, short_ids: list[str]) -> dict[str, Any]:
    """Resolve next_action for each short_id and return a batch plan dict.

    Returns the dict produced by ``plan_batch`` with Pydantic models serialised
    to plain dicts (``NextActionOut`` → dict).
    """
    raw = plan_batch(db, short_ids)
    # plan_batch already returns dicts, but action values are NextActionOut objects
    plans: list[dict[str, Any]] = []
    for entry in raw["plans"]:
        action_val = entry.get("action")
        serialised_action: dict[str, Any] | None
        if action_val is not None and hasattr(action_val, "model_dump"):
            serialised_action = action_val.model_dump()
        else:
            serialised_action = action_val  # already None or dict
        plans.append(
            {
                "short_id": entry["short_id"],
                "found": entry["found"],
                "action": serialised_action,
                "hash": entry["hash"],
            }
        )
    return {"plans": plans, "batch_hash": raw["batch_hash"]}


def tool_batch_status(db: Database, short_ids: list[str]) -> dict[str, Any]:
    """Roll up next_action across a manifest into stage counts.

    Returns the dict produced by ``batch_status`` directly — it is already a
    plain JSON-serialisable dict.
    """
    return batch_status(db, short_ids)


def tool_recipe_from_trace(db: Database, run_id: str) -> dict[str, Any]:
    """Reconstruct and verify a short_run's recipe from its export trace.

    Returns the dict produced by ``recipe_from_trace``.  Empty dict ``{}``
    signals "not found" (the run_id does not exist in the ledger).
    """
    return recipe_from_trace(db, run_id)


# ---------------------------------------------------------------------------
# S7 — Agent-drivable tools (auto-shorts pipeline)
# ---------------------------------------------------------------------------


def tool_start_analysis(db: Database, asset_id: str) -> dict[str, Any]:
    """Enqueue an analysis.run job for *asset_id* using default pipeline config.

    Creates a fresh analysis_run row and enqueues the background worker job.
    This is one of two write operations in this module.

    Returns ``{"ok": True, "asset_id": ..., "analysis_run_id": ..., "job_id": ...}``
    on success, or ``{"ok": False, "error": "asset not found", "asset_id": ...}``
    when the asset does not exist.
    """
    if repos.get_asset(db, asset_id) is None:
        logger.debug("tool_start_analysis: asset_id=%r not found", asset_id)
        return {"ok": False, "error": "asset not found", "asset_id": asset_id}

    _defaults = AnalysisStart()
    config: dict[str, Any] = {
        "stages": {
            "scene": _defaults.scene,
            "asr": _defaults.asr,
            "diarize": _defaults.diarize,
            "align": _defaults.align,
        },
        "model": _defaults.model,
        "language": _defaults.language,
        "detector": _defaults.detector,
    }
    run = repos.create_analysis_run(
        db, asset_id=asset_id, pipeline_version=PIPELINE_VERSION, config=config
    )
    job_id = enqueue(
        db,
        queue=queue_for("analysis.run"),
        kind="analysis.run",
        payload={"asset_id": asset_id, "analysis_run_id": run["id"], "config": config},
        idempotency_key=f"analysis:{run['id']}",
        pipeline_version=PIPELINE_VERSION,
        max_attempts=2,
    )
    logger.debug(
        "tool_start_analysis: asset_id=%r analysis_run_id=%r job_id=%r",
        asset_id,
        run["id"],
        job_id,
    )
    return {
        "ok": True,
        "asset_id": asset_id,
        "analysis_run_id": run["id"],
        "job_id": job_id,
    }


def tool_build_roughcut(db: Database, asset_id: str) -> dict[str, Any]:
    """Build a rough cut + scenes for *asset_id* from its succeeded analysis (idempotent).

    Fills the asset's rough-cut timeline from kept shots and groups it into scenes — the
    executable counterpart to ``tool_next_action``'s ``roughcut_from_shots`` step. Requires a
    succeeded analysis run. Mutates the database synchronously (no background job).

    Returns ``{"ok": True, "asset_id": ..., "timeline_id": ..., "scene_count": ...}`` on
    success, or ``{"ok": False, "error": ..., "asset_id": ...}`` when the asset is missing or
    has no succeeded analysis run.
    """
    asset = repos.get_asset(db, asset_id)
    if asset is None:
        logger.debug("tool_build_roughcut: asset_id=%r not found", asset_id)
        return {"ok": False, "error": "asset not found", "asset_id": asset_id}
    run = repos.get_latest_analysis_run(db, asset_id)
    if run is None or run["status"] != "succeeded":
        return {"ok": False, "error": "no succeeded analysis run", "asset_id": asset_id}
    project_id = str(asset["project_id"])
    scene_count = autobuild_asset_edit_ready(
        db, project_id=project_id, asset_id=asset_id, run_id=str(run["id"])
    )
    timeline = repos.get_or_create_asset_rough_cut(db, project_id, asset_id)
    logger.debug(
        "tool_build_roughcut: asset_id=%r timeline_id=%r scene_count=%d",
        asset_id,
        timeline["id"],
        scene_count,
    )
    return {
        "ok": True,
        "asset_id": asset_id,
        "timeline_id": str(timeline["id"]),
        "scene_count": scene_count,
    }


def tool_render_timeline(db: Database, timeline_id: str, *, format: str = "mp4") -> dict[str, Any]:
    """Render a timeline to a finished export — the executable ``render_reel`` step.

    Creates an export and enqueues an ``export.render`` job (mirrors the timeline render
    endpoint). Requires the timeline to have clips. Enqueues a background job; poll
    ``job_status(job_id)`` for completion.

    Returns ``{"ok": True, "timeline_id": ..., "export_id": ..., "job_id": ...}`` on success,
    or ``{"ok": False, "error": ..., "timeline_id": ...}`` when the timeline is missing or empty.
    """
    tl = repos.get_timeline(db, timeline_id)
    if tl is None:
        return {"ok": False, "error": "timeline not found", "timeline_id": timeline_id}
    if not repos.list_timeline_clips(db, timeline_id):
        return {"ok": False, "error": "timeline has no clips", "timeline_id": timeline_id}
    export = repos.create_export(
        db, project_id=str(tl["project_id"]), timeline_id=timeline_id, format=format, options={}
    )
    job_id = enqueue(
        db,
        queue=queue_for("export.render"),
        kind="export.render",
        payload={"export_id": export["id"]},
        idempotency_key=f"render:{export['id']}",
    )
    logger.debug(
        "tool_render_timeline: timeline_id=%r export_id=%r job_id=%r",
        timeline_id,
        export["id"],
        job_id,
    )
    return {
        "ok": True,
        "timeline_id": timeline_id,
        "export_id": str(export["id"]),
        "job_id": job_id,
    }


def tool_extract_shorts(
    db: Database,
    asset_id: str,
    *,
    min_duration_s: float | None = None,
    max_duration_s: float | None = None,
    max_candidates: int | None = None,
) -> dict[str, Any]:
    """Enqueue a shorts.extract job for *asset_id*.

    Requires a succeeded analysis run; returns an error dict if none exists.
    This is one of two write operations in this module.

    Optional overrides (all default to module-level defaults when omitted):
    - ``min_duration_s``: minimum clip duration in seconds
    - ``max_duration_s``: maximum clip duration in seconds
    - ``max_candidates``: upper bound on how many candidates to produce

    Returns ``{"ok": True, "asset_id": ..., "analysis_run_id": ..., "job_id": ...}``
    or ``{"ok": False, "error": "asset not found", "asset_id": ...}`` when the asset
    does not exist, or ``{"ok": False, "error": "analyze the asset first ...", "asset_id": ...}``
    when no succeeded analysis run exists.
    """
    if repos.get_asset(db, asset_id) is None:
        logger.debug("tool_extract_shorts: asset_id=%r not found", asset_id)
        return {"ok": False, "error": "asset not found", "asset_id": asset_id}

    run = repos.get_latest_analysis_run(db, asset_id)
    if run is None or run["status"] != "succeeded":
        logger.debug(
            "tool_extract_shorts: asset_id=%r has no succeeded analysis run", asset_id
        )
        return {
            "ok": False,
            "error": "analyze the asset first (no succeeded analysis run)",
            "asset_id": asset_id,
        }

    config: dict[str, Any] = {}
    if min_duration_s is not None:
        config["min_duration_s"] = min_duration_s
    if max_duration_s is not None:
        config["max_duration_s"] = max_duration_s
    if max_candidates is not None:
        config["max_candidates"] = max_candidates

    job_id = enqueue(
        db,
        queue=queue_for("shorts.extract"),
        kind="shorts.extract",
        payload={"asset_id": asset_id, **config},
        idempotency_key=f"shorts:{asset_id}:{run['id']}",
        pipeline_version=PIPELINE_VERSION,
    )
    logger.debug(
        "tool_extract_shorts: asset_id=%r analysis_run_id=%r job_id=%r",
        asset_id,
        run["id"],
        job_id,
    )
    return {
        "ok": True,
        "asset_id": asset_id,
        "analysis_run_id": run["id"],
        "job_id": job_id,
    }


def tool_list_short_candidates(db: Database, asset_id: str) -> dict[str, Any]:
    """List all persisted short candidates for *asset_id*, ordered by score.

    Returns ``{"asset_id": ..., "count": N, "candidates": [...]}``.
    The candidates list is empty when none have been extracted yet.
    """
    cands = repos.list_shorts_candidates_by_asset(db, asset_id)
    return {"asset_id": asset_id, "count": len(cands), "candidates": cands}


def tool_job_status(db: Database, job_id: str) -> dict[str, Any]:
    """Return status information for a background job by *job_id*.

    Returns ``{"found": False, "job_id": ...}`` when the job does not exist.
    Otherwise returns ``{"found": True, "job_id": ..., "kind": ..., "status": ...,
    "queue": ..., "attempts": ..., "result": <parsed JSON or None>, "error": ...}``.
    """
    job = repos.get_job(db, job_id)
    if job is None:
        logger.debug("tool_job_status: job_id=%r not found", job_id)
        return {"found": False, "job_id": job_id}

    result_raw = job.get("result_json")
    result: Any = None
    if result_raw:
        try:
            result = json.loads(result_raw)
        except (json.JSONDecodeError, TypeError):
            result = None

    error_raw = job.get("error_json")
    error: Any = None
    if error_raw:
        try:
            error = json.loads(error_raw)
        except (json.JSONDecodeError, TypeError):
            error = None

    return {
        "found": True,
        "job_id": job_id,
        "kind": job.get("kind"),
        "status": job.get("status"),
        "queue": job.get("queue"),
        "attempts": job.get("attempt"),  # DB column is "attempt" (singular)
        "result": result,
        "error": error,
    }


def tool_explain_candidate(db: Database, candidate_id: str) -> dict[str, Any]:
    """Return a human-readable explanation for one short candidate.

    Returns ``{"found": False, "candidate_id": ...}`` when not found.
    Otherwise returns the full candidate metadata plus:
    - ``top_factors``: top 2–3 score_breakdown components by value (descending)
    - ``explanation``: a concise string summarising the score and key factors

    Useful for an agent to justify why a particular clip was selected (or rejected).
    """
    c = repos.get_short_candidate(db, candidate_id)
    if c is None:
        logger.debug("tool_explain_candidate: candidate_id=%r not found", candidate_id)
        return {"found": False, "candidate_id": candidate_id}

    score_breakdown: dict[str, float] = c.get("score_breakdown") or {}
    qa_issues: list[str] = c.get("qa_issues") or []
    qa_passed: bool = bool(c.get("qa_passed"))
    score: float = float(c.get("score", 0.0))

    # Rank breakdown components by value (descending), take top 3
    sorted_factors = sorted(score_breakdown.items(), key=lambda kv: kv[1], reverse=True)
    top_factors = [{"name": k, "value": v} for k, v in sorted_factors[:3]]

    # Build the explanation string
    if top_factors:
        factor_parts = ", ".join(
            f"{f['name']} ({f['value']:.2f})" for f in top_factors
        )
        explanation = f"Score {score:.2f} — strongest factors: {factor_parts}."
    else:
        explanation = f"Score {score:.2f} — no score breakdown available."

    if qa_passed:
        explanation += " QA passed."
    else:
        issues_str = ", ".join(qa_issues) if qa_issues else "unknown"
        explanation += f" QA FAILED: {issues_str}."

    logger.debug(
        "tool_explain_candidate: candidate_id=%r score=%.3f qa_passed=%s",
        candidate_id,
        score,
        qa_passed,
    )
    return {
        "found": True,
        "candidate_id": candidate_id,
        "asset_id": c.get("asset_id"),
        "start_frame": c.get("start_frame"),
        "end_frame_exclusive": c.get("end_frame_exclusive"),
        "score": score,
        "qa_passed": qa_passed,
        "qa_issues": qa_issues,
        "top_factors": top_factors,
        "explanation": explanation,
    }


def tool_render_short(
    db: Database,
    candidate_id: str,
    *,
    captions: bool = True,
    hook_text: str | None = None,
    loudnorm: bool = True,
    candidate_ids: list[str] | None = None,
    fit: str = "crop",
) -> dict[str, Any]:
    """Render one or more short candidates to a vertical 9:16 MP4 (export/job ids returned).

    Thin wrapper over the same logic as the ``POST /shorts-candidates/{id}/render`` API:
    creates the ``exports`` row up front and enqueues a ``shorts.render`` job. Completes the
    agent-drivable shorts toolset (analyze → extract → list → explain → **render**).

    ``candidate_ids`` (ordered, same asset) renders a MULTI-SEGMENT short — several scenes cut
    together with captions aligned per segment. ``fit="blur"`` letterboxes the source onto a
    blurred background instead of center-cropping (for screen recordings / UI content, where a
    9:16 crop cuts the picture off).

    Returns ``{"ok": True, "export_id": ..., "job_id": ...}`` on success, or
    ``{"ok": False, "error": "candidate not found", "candidate_id": ...}`` when a candidate
    (or its asset) does not exist.
    """
    ids = [str(c) for c in (candidate_ids or [candidate_id]) if c]
    if not ids:
        return {"ok": False, "error": "no candidate ids", "candidate_id": candidate_id}

    first_asset_id: str | None = None
    candidate: dict[str, Any] | None = None
    for cid in ids:
        row = repos.get_short_candidate(db, cid)
        if row is None:
            logger.debug("tool_render_short: candidate_id=%r not found", cid)
            return {"ok": False, "error": "candidate not found", "candidate_id": cid}
        if first_asset_id is None:
            first_asset_id = str(row["asset_id"])
            candidate = row
        elif str(row["asset_id"]) != first_asset_id:
            return {"ok": False, "error": "candidates span multiple assets", "candidate_id": cid}
    assert candidate is not None  # ids is non-empty and every row was found

    asset = repos.get_asset(db, candidate["asset_id"])
    if asset is None:
        logger.debug("tool_render_short: asset for candidate_id=%r not found", ids[0])
        return {"ok": False, "error": "asset not found", "candidate_id": ids[0]}

    options: dict[str, Any] = {
        "kind": "short",
        "candidate_id": ids[0],
        "candidate_ids": ids,
        "captions": captions,
        "hook_text": hook_text,
        "loudnorm": loudnorm,
        "reel_fit": fit == "blur",
        "reel_blur_fill": fit == "blur",
    }
    exp = repos.create_export(
        db,
        project_id=asset["project_id"],
        timeline_id=candidate.get("source_timeline_id"),
        format="mp4",
        options=options,
    )
    job_id = enqueue(
        db,
        queue=queue_for("shorts.render"),
        kind="shorts.render",
        payload={"export_id": exp["id"]},
        idempotency_key=f"shortrender:{exp['id']}",
    )
    logger.debug(
        "tool_render_short: candidate_id=%r export_id=%r job_id=%r",
        candidate_id,
        exp["id"],
        job_id,
    )
    return {"ok": True, "export_id": exp["id"], "job_id": job_id}


def tool_render_segments(
    db: Database,
    asset_id: str,
    segments: list[tuple[int, int]] | list[list[int]],
    *,
    captions: bool = True,
    hook_text: str | None = None,
    loudnorm: bool = True,
    fit: str = "crop",
    vertical: bool = True,
    out_size: tuple[int, int] = (1080, 1920),
) -> dict[str, Any]:
    """Render raw source segments of one asset to a short (export/job ids returned).

    The generic sibling of :func:`tool_render_short`: instead of persisted candidates it takes
    explicit ``[start_frame, end_frame_exclusive)`` ranges — e.g. rough-cut scenes picked by
    number. ``vertical``/``out_size`` select the canvas (1080×1920 reel, 1080×1080 square, or
    ``vertical=False`` for the native 16:9 pass-through); ``fit="blur"`` letterboxes onto a
    blurred background.
    """
    asset = repos.get_asset(db, asset_id)
    if asset is None:
        return {"ok": False, "error": "asset not found", "asset_id": asset_id}
    ranges = [(int(s), int(e)) for (s, e) in segments if int(e) > int(s)]
    if not ranges:
        return {"ok": False, "error": "no segments", "asset_id": asset_id}

    options: dict[str, Any] = {
        "kind": "short",
        "asset_id": asset_id,
        "segments": [[s, e] for (s, e) in ranges],
        "captions": captions,
        "hook_text": hook_text,
        "loudnorm": loudnorm,
        "reel_fit": fit == "blur",
        "reel_blur_fill": fit == "blur",
        "vertical": vertical,
        "out_size": [int(out_size[0]), int(out_size[1])],
    }
    exp = repos.create_export(
        db,
        project_id=asset["project_id"],
        timeline_id=None,
        format="mp4",
        options=options,
    )
    job_id = enqueue(
        db,
        queue=queue_for("shorts.render"),
        kind="shorts.render",
        payload={"export_id": exp["id"]},
        idempotency_key=f"shortrender:{exp['id']}",
    )
    return {"ok": True, "export_id": exp["id"], "job_id": job_id, "segments": len(ranges)}


# ---------------------------------------------------------------------------
# VE5 — visual MCP tools (queryable visual timeline over frame embeddings)
# ---------------------------------------------------------------------------
#
# Thin wrappers over ``laura.analysis.visual_query``. The image-image tools run
# with NO model; ``tool_search_visual_moments`` needs the optional visual/text
# extra and degrades gracefully (ok=False with a reason) when it is absent.


def tool_similar_segments(
    db: Database, asset_id: str, candidate_id: str, *, k: int = 5
) -> dict[str, Any]:
    """Find the candidates visually most similar to *candidate_id* (image-image).

    Wraps :func:`laura.analysis.visual_query.similar_segments`. Returns
    ``{"ok": True, "candidate_id", "similar": [...]}`` on success, or
    ``{"ok": False, "reason": ...}`` when no frame embeddings exist or the
    candidate is unknown.
    """
    return visual_query.similar_segments(db, asset_id, candidate_id, k=k)


def tool_deduplicate_shorts(
    db: Database, asset_id: str, *, threshold: float = 0.9
) -> dict[str, Any]:
    """Group near-identical short candidates for *asset_id* (image-image).

    Wraps :func:`laura.analysis.visual_query.deduplicate_shorts`. Returns
    ``{"ok": True, "groups", "kept", "dropped"}`` on success, or
    ``{"ok": False, "reason": ...}`` when no frame embeddings exist.
    """
    return visual_query.deduplicate_shorts(db, asset_id, threshold=threshold)


def tool_visual_hook(
    db: Database, asset_id: str, candidate_id: str
) -> dict[str, Any]:
    """Score a candidate's visual opening strength (image-image).

    Wraps :func:`laura.analysis.visual_query.visual_hook`. Returns
    ``{"ok": True, "candidate_id", "visual_shift_at_start", "opening_continuity",
    "hook_score", "explanation"}`` on success, or ``{"ok": False, "reason": ...}``
    when no frame embeddings exist or the candidate is unknown.
    """
    return visual_query.visual_hook(db, asset_id, candidate_id)


def tool_search_visual_moments(
    db: Database, asset_id: str, query: str, *, k: int = 10
) -> dict[str, Any]:
    """Search an asset's frames by a natural-language *query* (text→image).

    Wraps :func:`laura.analysis.visual_query.search_visual_moments`. Needs the
    optional visual/text extra (CLIP text encoder); when it is absent the call
    degrades gracefully to ``{"ok": False, "reason": ...}`` rather than raising.
    Returns ``{"ok": True, "query", "k", "moments": [...]}`` on success.
    """
    return visual_query.search_visual_moments(db, asset_id, query, k=k)
