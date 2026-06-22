"""laura-deck MCP stdio server.

Registers the four read-surface tools on an MCP server and runs the stdio
transport.  The ``mcp`` SDK is an OPTIONAL extra; this module guards the import
so that ``import laura.mcp.server`` never hard-requires mcp to be installed.
The ``import mcp`` only runs inside ``main()`` at call-time.

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
        tool_next_action,
        tool_recipe_from_trace,
    )

    db_path = _get_db_path()
    db = SqliteDatabase(db_path)
    # Ensure schema is up-to-date (read-only use case — migrate is idempotent).
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

    logger.info("laura-deck MCP server starting (db=%s)", db_path)
    mcp_server.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
