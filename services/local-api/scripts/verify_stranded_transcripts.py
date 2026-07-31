"""Verify the stranded-transcript fix against a COPY of the live database.

Usage (from services/local-api):
    uv run python scripts/verify_stranded_transcripts.py ../../workspace-livetest/laura.db

Reads only: the source file is copied to a temp dir first. Two assets in the live DB have
their only transcript on runs frozen in 'running' (AgentFarm Autogen: 165 segments,
n8n Farm: 8), and their newer succeeded runs carry none -- see
docs/superpowers/specs/2026-07-31-stranded-transcript-runs-design.md.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
from pathlib import Path

from laura.db import repos
from laura.db.database import SqliteDatabase

logger = logging.getLogger("verify_stranded_transcripts")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_path", type=Path, help="path to the live laura.db (read-only)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "laura.db"
        shutil.copy2(args.db_path, copy)
        db = SqliteDatabase(copy)

        with db.connection() as conn:
            projects = [dict(r) for r in conn.execute("SELECT id, name FROM projects")]
            assets = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, project_id, display_name FROM media_assets ORDER BY display_name"
                )
            ]

        ok = True
        for asset in assets:
            run = repos.get_latest_transcript_run(db, str(asset["id"]))
            if run is None:
                logger.info("%-30s no transcript on any run", asset["display_name"][:30])
                continue
            segments = repos.get_transcript(db, str(asset["id"]), str(run["id"]))
            logger.info(
                "%-30s run %s (%s) -> %d segments",
                asset["display_name"][:30], str(run["id"])[:8], run["status"], len(segments),
            )
            if not segments:
                ok = False

        for project in projects:
            hits = repos.search_transcript(
                db, project_id=str(project["id"]), query="agent", limit=200
            )
            asset_names = sorted({str(h["asset_name"]) for h in hits})
            logger.info(
                "project %-20s lexical 'agent': %d hits across %s",
                str(project["name"])[:20], len(hits), asset_names or "-",
            )

        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
