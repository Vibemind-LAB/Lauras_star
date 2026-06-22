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
from .models import BatchPlanIn, BatchPlanOut, BatchShortPlanOut, NextActionOut
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
