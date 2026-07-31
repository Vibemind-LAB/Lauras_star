"""``shorts.extract`` background job — wires the pure shorts modules to the DB.

This is the runnable seam for the auto-shorts-cutter: a job handler that loads an
asset's already-persisted analysis (transcript words + shots), runs the three pure
modules (:mod:`laura.analysis.shorts_segments` →
:mod:`laura.analysis.shorts_score` → :mod:`laura.analysis.shorts_qa`), flattens
each survivor into the :func:`laura.db.repos.replace_shorts_candidates` row contract,
and persists the ranked set.

Nothing here decodes frames or imports a heavy model. ``silence`` is intentionally
``None`` (no ffmpeg in the job — MVP); every optional signal degrades gracefully in
the pure scorer/QA gate, so the cutter runs on a CPU-only backend.

Invariants honoured (same as the rest of Laura's editorial layer):

* Integer source frames everywhere; seconds are converted to frames once, inside the
  pure modules, and never carried as state.
* Ranges are end-exclusive (``end_frame_exclusive``).
* Idempotency: ``(asset_id, latest succeeded analysis run)`` determines the candidate
  set; re-running replaces the timeline's rows wholesale.

Hard rejection: a candidate whose score is ``rejected`` (a cut bisects a word) is a
construction error and is **never persisted** — dropping it also keeps the ``-inf``
sentinel total out of the persisted ``score`` column.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from ..db import repos
from ..db.database import Database
from ..jobs.runner import JobContext, JobHandler
from . import semantic
from .editorial import Word
from .shorts_qa import qa_candidate
from .shorts_score import score_candidates
from .shorts_segments import (
    DEFAULT_MAX_DURATION_S,
    DEFAULT_MIN_DURATION_S,
    generate_candidates,
)
from .shorts_types import ShortCandidate, ShortScore
from .types import ShotResult

_log = logging.getLogger(__name__)

__all__ = ["handle_shorts_extract", "register_shorts_handlers"]


def _pick_source_timeline_id(db: Database, project_id: str, asset_id: str) -> str:
    """Resolve the binding timeline for an asset's shorts candidates.

    Read-only mirror of ``laura.api.shorts._pick_timeline`` (kept inline so this job
    never imports the read-only next-action router): prefer the project's ``sequence``
    timeline when it has clips, else the newest ``rough_cut`` ``created_from=asset_id``.
    Falls back to ``asset_id`` (TEXT) — "binding timeline, or asset id if none yet" —
    so the cutter is invocable before any cut exists.
    """
    with db.connection() as conn:
        seq_row = conn.execute(
            "SELECT id FROM timelines WHERE project_id=? AND kind='sequence' "
            "ORDER BY created_at LIMIT 1",
            (project_id,),
        ).fetchone()
    if seq_row is not None and repos.list_timeline_clips(db, seq_row["id"]):
        return str(seq_row["id"])

    with db.connection() as conn:
        rc_row = conn.execute(
            "SELECT id FROM timelines WHERE project_id=? AND kind='rough_cut' "
            "AND created_from=? ORDER BY created_at DESC, id DESC LIMIT 1",
            (project_id, asset_id),
        ).fetchone()
    if rc_row is not None:
        return str(rc_row["id"])

    return asset_id


def handle_shorts_extract(ctx: JobContext) -> dict[str, Any]:
    """Generate, score, QA and persist short candidates for one asset.

    Payload: ``{"asset_id": str, "min_duration_s"?, "max_duration_s"?, "max_candidates"?}``.

    Requires a *succeeded* latest analysis run (raises :exc:`ValueError` otherwise — the
    API guards this with a 409 before enqueuing). Persists the ranked, transcript-safe
    candidate set (best score first) and returns a small summary dict.
    """
    db = ctx.db
    payload = ctx.payload
    asset_id: str = payload["asset_id"]

    min_duration_s: float = float(payload.get("min_duration_s") or DEFAULT_MIN_DURATION_S)
    max_duration_s: float = float(payload.get("max_duration_s") or DEFAULT_MAX_DURATION_S)
    max_candidates: int | None = payload.get("max_candidates")
    if max_candidates is not None:
        max_candidates = int(max_candidates)

    asset = repos.get_asset(db, asset_id)
    if asset is None:
        raise ValueError(f"asset not found: {asset_id}")
    project = repos.get_project(db, asset["project_id"])
    if project is None:
        raise ValueError(f"project not found for asset {asset_id}: {asset['project_id']}")

    rate_num: int = int(asset["rate_num"] or 25)
    rate_den: int = int(asset["rate_den"] or 1)
    total_frames: int | None = asset["duration_frames"]

    run = repos.get_latest_analysis_run(db, asset_id)
    if run is None or run["status"] != "succeeded":
        status = "none" if run is None else run["status"]
        raise ValueError(
            f"no succeeded analysis run for asset {asset_id} (latest status: {status})"
        )

    source_timeline_id = _pick_source_timeline_id(db, project["id"], asset_id)

    # --- Load transcript words → editorial Word view -----------------------
    # Words come from the run that HOLDS the transcript, which need not be the run above: a
    # scene-only re-analysis (stages.asr false) is the latest run and carries no segments.
    transcript_run = repos.get_latest_transcript_run(db, asset_id)
    word_rows = (
        repos.list_words_for_run(db, asset_id, str(transcript_run["id"]))
        if transcript_run is not None
        else []
    )
    words = [
        Word(
            start_frame=r["start_frame"],
            end_frame=r["end_frame"],
            text=r.get("text"),
            speaker=r.get("speaker_label"),
        )
        for r in word_rows
    ]

    sentence_frames = semantic.sentence_end_frames(words)
    speaker_frames = semantic.speaker_turn_frames(words)

    # --- Load shots → ShotResult (None when there are none) ----------------
    shot_rows = repos.list_shots(db, asset_id, run["id"])
    shots: list[ShotResult] | None = (
        [
            ShotResult(
                src_in_frame=r["src_in_frame"],
                src_out_frame_exclusive=r["src_out_frame_exclusive"],
                method=r.get("method") or "unknown",
                confidence=r.get("confidence"),
            )
            for r in shot_rows
        ]
        or None
    )

    # --- Load frame embeddings (VE4) → frame→vector map (None when empty) ----
    # Sparse map (1 fps + shot boundaries from the VE1/VE2 pipeline). An empty store
    # yields None, which keeps the visual scorer components strictly neutral — no
    # behaviour change relative to a run without embeddings.
    from .embeddings_store import SqliteVectorStore

    emb_items = SqliteVectorStore(db).list_frame_embeddings(asset_id, run["id"])
    embeddings: dict[int, np.ndarray] | None = {
        e.frame: e.vector for e in emb_items
    } or None

    # --- Generate → score → QA (silence=None: MVP, no ffmpeg in the job) ----
    cands: list[ShortCandidate] = generate_candidates(
        words,
        sentence_frames,
        speaker_frames,
        rate_num=rate_num,
        rate_den=rate_den,
        total_frames=total_frames,
        min_duration_s=min_duration_s,
        max_duration_s=max_duration_s,
        max_candidates=max_candidates,
    )
    scores: list[ShortScore] = score_candidates(
        cands,
        words,
        rate_num=rate_num,
        rate_den=rate_den,
        shots=shots,
        silence=None,
        sentence_frames=sentence_frames,
        speaker_frames=speaker_frames,
        embeddings=embeddings,
        min_duration_s=min_duration_s,
        max_duration_s=max_duration_s,
    )

    # --- Flatten + filter: drop hard-rejected (word-severing) candidates ----
    flat_rows: list[dict[str, Any]] = []
    for cand, score in zip(cands, scores, strict=True):
        if score.rejected:
            # Hard word-cut lock — invalid by construction. Never persist (also keeps
            # the -inf sentinel total out of the REAL score column).
            continue
        qa = qa_candidate(
            cand,
            words,
            silence=None,
            sentence_frames=sentence_frames,
            speaker_frames=speaker_frames,
        )
        flat_rows.append(
            {
                "start_frame": cand.start_frame,
                "end_frame_exclusive": cand.end_frame_exclusive,
                "start_boundary": cand.start_boundary,
                "end_boundary": cand.end_boundary,
                "score": score.total,
                "rejected": False,
                "reject_reason": None,
                "score_breakdown": score.breakdown,
                "qa_passed": qa.passed,
                "qa_issues": qa.issues,
            }
        )

    # Best first → positional order_index in replace_shorts_candidates.
    flat_rows.sort(key=lambda r: r["score"], reverse=True)

    repos.replace_shorts_candidates(
        db, project["id"], asset_id, source_timeline_id, flat_rows
    )

    kept = sum(1 for r in flat_rows if r["qa_passed"])
    _log.info(
        "shorts.extract asset=%s run=%s timeline=%s candidates=%d kept=%d",
        asset_id,
        run["id"],
        source_timeline_id,
        len(flat_rows),
        kept,
    )
    return {
        "candidates": len(flat_rows),
        "kept": kept,
        "analysis_run_id": run["id"],
        "source_timeline_id": source_timeline_id,
    }


def register_shorts_handlers(registry: dict[str, JobHandler]) -> None:
    """Register the ``shorts.extract`` handler on the job registry."""
    registry["shorts.extract"] = handle_shorts_extract
