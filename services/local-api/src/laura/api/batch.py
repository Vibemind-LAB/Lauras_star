"""POST /shorts/batch-plan — pure read-model batch resolver, no side effects.

Applies resolve_next_action (P1-T1) to each short_id in the manifest and
returns an ordered list of per-short plans plus a deterministic batch_hash
(sha256 over the ordered leaf hashes — a flat "merkle root" for v1).

INVARIANT: plan_batch MUST NOT write to the database.  One blocked or
not-found short NEVER aborts the others.

Hash scheme (v1):
  - Found short:  sha256(canonical_json({"args": ..., "blocked_by": ...,
                                         "short_id": ..., "tool": ...}))
  - Not-found:    sha256(canonical_json({"found": False, "short_id": ...}))
  - batch_hash:   sha256(canonical_json([leaf_hash_0, leaf_hash_1, ...]))

Note: recipe_id is out of scope for v1 (no recipe store yet — P3-T2 only
computes hashes).  Accept short_ids only.
"""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, Depends, Request

from ..db.database import Database
from ..ledger.recipe import canonical_json
from ..policy import get_asset_policy, parse_policy
from .models import (
    BatchPlanIn,
    BatchPlanOut,
    BatchShortPlanOut,
    BatchStageCounts,
    BatchStatusIn,
    BatchStatusOut,
    NextActionOut,
)
from .security import require_token
from .shorts import resolve_next_action

router = APIRouter(tags=["batch"], dependencies=[Depends(require_token)])


# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------

def _hash_found(short_id: str, action: NextActionOut) -> str:
    """Per-short hash for a found short.

    Only the semantically relevant fields (short_id, tool, args, blocked_by)
    are included — label_key and reason are display hints and MUST NOT affect
    the hash so that i18n copy changes don't invalidate the plan.
    """
    payload: dict[str, Any] = {
        "args": action.args,
        "blocked_by": action.blocked_by,
        "short_id": short_id,
        "tool": action.tool,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _hash_not_found(short_id: str) -> str:
    """Per-short hash for a short_id that resolved to None (asset not found)."""
    payload: dict[str, Any] = {"found": False, "short_id": short_id}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Pure resolver
# ---------------------------------------------------------------------------

def plan_batch(db: Database, short_ids: list[str]) -> dict[str, Any]:
    """Resolve next_action for each short_id in order and return a batch plan dict.

    PURE READ — no writes, no enqueues, no minting.

    Returns::

        {
            "plans": [
                {
                    "short_id": str,
                    "found": bool,
                    "action": NextActionOut | None,
                    "hash": str,          # per-short sha256 hexdigest
                },
                ...
            ],
            "batch_hash": str,            # sha256 of canonical_json(ordered leaf hashes)
        }
    """
    plans: list[dict[str, Any]] = []
    for sid in short_ids:
        action = resolve_next_action(db, sid)
        if action is None:
            leaf_hash = _hash_not_found(sid)
            plans.append(
                {
                    "short_id": sid,
                    "found": False,
                    "action": None,
                    "hash": leaf_hash,
                }
            )
        else:
            leaf_hash = _hash_found(sid, action)
            plans.append(
                {
                    "short_id": sid,
                    "found": True,
                    "action": action,
                    "hash": leaf_hash,
                }
            )

    leaf_hashes = [entry["hash"] for entry in plans]
    batch_hash = hashlib.sha256(canonical_json(leaf_hashes).encode("utf-8")).hexdigest()

    return {"plans": plans, "batch_hash": batch_hash}


# ---------------------------------------------------------------------------
# HTTP endpoint
# ---------------------------------------------------------------------------

def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


# ---------------------------------------------------------------------------
# Stage mapping
# ---------------------------------------------------------------------------

#: Maps label_key values from resolve_next_action onto BatchStageCounts field names.
_LABEL_KEY_TO_STAGE: dict[str, str] = {
    "next_action.preparing": "preparing",
    "next_action.analyzing": "analyzing",
    "next_action.analyze": "analyze",
    "next_action.cut": "cut",
    "next_action.build_reel": "build",
    "next_action.done": "done",
}


# ---------------------------------------------------------------------------
# Pure resolver — batch_status
# ---------------------------------------------------------------------------


def batch_status(db: Database, short_ids: list[str]) -> dict[str, Any]:
    """Roll up resolve_next_action across a manifest into stage counts.

    PURE READ — no writes, no enqueues.

    Returns::

        {
            "total": int,
            "by_stage": {
                "preparing": int, "analyzing": int, "analyze": int,
                "cut": int, "build": int, "done": int, "not_found": int,
            },
            "needs_human": int,
        }

    Stage mapping: each short_id is resolved via resolve_next_action; the
    resulting label_key is mapped to a stage bucket via _LABEL_KEY_TO_STAGE.
    Shorts for which resolve_next_action returns None are counted as "not_found".

    needs_human: count of shorts where get_asset_policy returns a row whose
    policy string parses to mode == "human".

    Deferred: an "unverified" count requires per-short timeline-quality
    resolution from the short_runs ledger — that is out of scope for v1.
    """
    stage_counts: dict[str, int] = {
        "preparing": 0,
        "analyzing": 0,
        "analyze": 0,
        "cut": 0,
        "build": 0,
        "done": 0,
        "not_found": 0,
    }
    needs_human = 0

    for sid in short_ids:
        action = resolve_next_action(db, sid)
        if action is None:
            stage_counts["not_found"] += 1
        else:
            stage = _LABEL_KEY_TO_STAGE.get(action.label_key, "not_found")
            stage_counts[stage] += 1

        # needs_human: check asset policy (pure read — get_asset_policy never writes)
        policy_row = get_asset_policy(db, sid)
        if policy_row is not None:
            try:
                parsed = parse_policy(policy_row["policy"])
                if parsed.mode == "human":
                    needs_human += 1
            except ValueError:
                pass  # malformed policy row — skip silently; no writes, no abort

    return {
        "total": len(short_ids),
        "by_stage": stage_counts,
        "needs_human": needs_human,
    }


@router.post("/shorts/batch-plan", response_model=BatchPlanOut)
def post_batch_plan(body: BatchPlanIn, request: Request) -> BatchPlanOut:
    """Deterministic, side-effect-free batch next-action projection.

    Applies resolve_next_action to each short_id in the manifest and returns
    ordered per-short plans + a batch_hash over the leaf hashes.  Unknown
    short_ids yield found=False without aborting the rest.
    """
    db = _db(request)
    result = plan_batch(db, body.short_ids)

    plans_out = [
        BatchShortPlanOut(
            short_id=entry["short_id"],
            found=entry["found"],
            action=entry["action"],
            hash=entry["hash"],
        )
        for entry in result["plans"]
    ]
    return BatchPlanOut(plans=plans_out, batch_hash=result["batch_hash"])


@router.post("/shorts/batch-status", response_model=BatchStatusOut)
def post_batch_status(body: BatchStatusIn, request: Request) -> BatchStatusOut:
    """Conveyor rollup: aggregate resolve_next_action across a manifest into stage counts.

    Pure read — no writes.  Unknown short_ids are counted as ``not_found``
    without aborting the rest.  ``needs_human`` counts shorts whose persisted
    asset_policy has mode == "human".

    Deferred: ``unverified`` count (requires short_runs ledger).
    """
    db = _db(request)
    result = batch_status(db, body.short_ids)
    return BatchStatusOut(
        total=result["total"],
        by_stage=BatchStageCounts(**result["by_stage"]),
        needs_human=result["needs_human"],
    )
