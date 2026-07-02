"""``short_creator.run`` job handler (Iteration 8).

Runs the NL-agent short-creator (escalation ladder) for an asset + topic. AutoGen is an OPTIONAL
extra: the orchestrator import is lazy inside the handler, so registering this handler (at
``create_app``) never requires autogen. A missing extra surfaces as the job's clear RuntimeError.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..db import repos
from ..jobs.runner import JobContext, JobHandler

if TYPE_CHECKING:  # annotation only — never imported at runtime
    from .orchestrator import ExecuteFn


def handle_short_creator_run(
    ctx: JobContext, *, execute: ExecuteFn | None = None
) -> dict[str, Any]:
    """Payload ``{asset_id, topic, target_seconds}`` → the escalation-ladder result dict.

    ``execute`` is injectable for tests; production uses the default (real AutoGen team run).
    """
    payload = ctx.payload
    asset_id = str(payload["asset_id"])
    topic = str(payload["topic"])
    target_seconds = int(payload.get("target_seconds", 60))

    if repos.get_asset(ctx.db, asset_id) is None:
        return {"ok": False, "error": "asset not found", "asset_id": asset_id}

    from .orchestrator import run_short_creator  # lazy — pulls the optional autogen chain at run
    from .providers import resolve_from_env

    config = resolve_from_env()
    return run_short_creator(
        ctx.db,
        config,
        asset_id=asset_id,
        topic=topic,
        target_seconds=target_seconds,
        execute=execute,
    )


def register_short_creator_handlers(registry: dict[str, JobHandler]) -> None:
    """Register the ``short_creator.run`` handler on the job registry."""
    registry["short_creator.run"] = handle_short_creator_run
