"""Job handlers for the Product-Demo Assistant."""

from __future__ import annotations

from typing import Any

from ..db import repos
from ..jobs.runner import JobContext, JobHandler
from .drafts import build_demo_draft_items


def handle_demo_analyze(ctx: JobContext) -> dict[str, Any]:
    draft_id = str(ctx.payload["draft_id"])
    draft = repos.get_demo_draft(ctx.db, draft_id)
    if draft is None:
        raise ValueError(f"demo draft not found: {draft_id}")
    items = build_demo_draft_items(ctx.db, str(draft["asset_id"]))
    repos.update_demo_draft(ctx.db, draft_id, status="ready", items=items)
    return {"draft_id": draft_id, "items": len(items)}


def register_demo_handlers(registry: dict[str, JobHandler]) -> None:
    registry["demo.analyze"] = handle_demo_analyze
