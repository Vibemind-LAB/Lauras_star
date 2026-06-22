"""GET /shorts/{short_id}/next-action — pure read-model, no side effects.

v1 scope: short_id == asset_id.  The endpoint walks the existing DB tables
(asset_files, analysis_runs, timelines, timeline_clips, exports) and projects
the current state onto the single next action to produce a finished reel.

INVARIANT: resolve_next_action MUST NOT write to the database.  It only reads.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..db import repos
from ..db.database import Database
from .models import NextActionOut
from .security import require_token

router = APIRouter(tags=["shorts"], dependencies=[Depends(require_token)])


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


# ---------------------------------------------------------------------------
# Pure resolver (no DB writes, importable for parity/no-writes tests)
# ---------------------------------------------------------------------------

def resolve_next_action(db: Database, short_id: str) -> NextActionOut | None:
    """Return the next action for *short_id* (== asset_id in v1), or None if not found.

    State machine — first applicable rule wins:
    1. No proxy / waveform file yet           → blocked PROXY_PENDING
    2. Analysis run queued / running          → blocked ANALYSIS_RUNNING
    3. No analysis run OR latest run failed   → suggest analysis_run
    4. Analysis succeeded, no rough_cut clips → suggest roughcut_from_shots
    5. rough_cut (or sequence) has clips,
       no succeeded render export             → suggest render_reel
    6. A succeeded render export exists       → done
    """
    asset = repos.get_asset(db, short_id)
    if asset is None:
        return None

    # -----------------------------------------------------------------------
    # State 1: proxy / waveform not yet available
    # -----------------------------------------------------------------------
    kinds = {f["kind"] for f in repos.list_asset_files(db, short_id)}
    if not (kinds & {"proxy", "waveform"}):
        return NextActionOut(
            short_id=short_id,
            tool=None,
            args={},
            label_key="next_action.preparing",
            reason="proxy not ready",
            blocked_by=["PROXY_PENDING"],
        )

    # -----------------------------------------------------------------------
    # State 2 / 3: analysis run status
    # -----------------------------------------------------------------------
    run = repos.get_latest_analysis_run(db, short_id)
    if run is not None and run["status"] in ("queued", "running"):
        return NextActionOut(
            short_id=short_id,
            tool=None,
            args={},
            label_key="next_action.analyzing",
            reason=f"analysis run {run['id']} is {run['status']}",
            blocked_by=["ANALYSIS_RUNNING"],
        )

    if run is None or run["status"] != "succeeded":
        # No run, or latest run failed/cancelled — suggest (re-)analysis
        return NextActionOut(
            short_id=short_id,
            tool="analysis_run",
            args={"asset_id": short_id},
            label_key="next_action.analyze",
            reason="no analysis yet" if run is None else "latest analysis did not succeed",
            blocked_by=[],
        )

    # -----------------------------------------------------------------------
    # State 4 / 5 / 6: need a timeline with clips
    # -----------------------------------------------------------------------
    project_id: str = asset["project_id"]

    # Prefer a kind=sequence timeline for the project (brief requirement).
    # Fall back to the newest rough_cut created_from this asset.
    chosen_timeline: dict[str, Any] | None = _pick_timeline(db, project_id, short_id)

    if chosen_timeline is None or not repos.list_timeline_clips(db, chosen_timeline["id"]):
        # No cut yet
        return NextActionOut(
            short_id=short_id,
            tool="roughcut_from_shots",
            args={"asset_id": short_id},
            label_key="next_action.cut",
            reason="ready to cut",
            blocked_by=[],
        )

    timeline_id: str = chosen_timeline["id"]

    # -----------------------------------------------------------------------
    # State 5 / 6: look for a succeeded render export for this timeline
    # -----------------------------------------------------------------------
    succeeded_export = _latest_succeeded_export(db, project_id, timeline_id)
    if succeeded_export is not None:
        return NextActionOut(
            short_id=short_id,
            tool=None,
            args={"export_id": succeeded_export["id"]},
            label_key="next_action.done",
            reason="reel ready",
            blocked_by=[],
        )

    return NextActionOut(
        short_id=short_id,
        tool="render_reel",
        args={"timeline_id": timeline_id},
        label_key="next_action.build_reel",
        reason="cut ready",
        blocked_by=[],
    )


def _pick_timeline(db: Database, project_id: str, asset_id: str) -> dict[str, Any] | None:
    """Choose the best timeline to render.

    Preference order (brief §v1):
    1. First kind=sequence timeline for the project (if it has clips).
    2. Newest kind=rough_cut timeline created_from asset_id.
    """
    # Read-only: we never call get_or_create_project_sequence here.
    with db.connection() as conn:
        seq_row = conn.execute(
            "SELECT * FROM timelines WHERE project_id=? AND kind='sequence' "
            "ORDER BY created_at LIMIT 1",
            (project_id,),
        ).fetchone()
    if seq_row is not None:
        seq = dict(seq_row)
        if repos.list_timeline_clips(db, seq["id"]):
            return seq

    # Fall back to rough_cut created_from this asset
    with db.connection() as conn:
        rc_row = conn.execute(
            "SELECT * FROM timelines WHERE project_id=? AND kind='rough_cut' "
            "AND created_from=? ORDER BY created_at DESC, id DESC LIMIT 1",
            (project_id, asset_id),
        ).fetchone()
    if rc_row is not None:
        return dict(rc_row)
    return None


def _latest_succeeded_export(
    db: Database, project_id: str, timeline_id: str
) -> dict[str, Any] | None:
    """Return the most recent succeeded render-pipeline export for this timeline, or None."""
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM exports WHERE project_id=? AND timeline_id=? AND status='ready' "
            "ORDER BY created_at DESC LIMIT 1",
            (project_id, timeline_id),
        ).fetchone()
    return dict(row) if row is not None else None


# ---------------------------------------------------------------------------
# HTTP endpoint
# ---------------------------------------------------------------------------

@router.get("/shorts/{short_id}/next-action", response_model=NextActionOut)
def get_next_action(short_id: str, request: Request) -> NextActionOut:
    """Deterministic, side-effect-free next-action projection for a short (v1: short_id=asset_id).

    Returns the single next tool call to advance a source asset to a finished reel, or null
    when blocked / done.  404 when the asset does not exist.
    """
    db = _db(request)
    result = resolve_next_action(db, short_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "short not found")
    return result
