"""Verify the stranded-transcript fix against a COPY of the live database.

Usage (from services/local-api):
    uv run python scripts/verify_stranded_transcripts.py ../../workspace-livetest/laura.db

Reads only: the source file (and any -wal/-shm sidecars) is copied to a temp dir first --
mirroring laura.analysis.eval_cut_cli._open_db_copy, to dodge WAL/locks while the desktop app
may hold the live DB open. Two assets in the live DB have their only transcript on runs frozen
in 'running' (AgentFarm Autogen: 165 segments, n8n Farm: 8), and their newer succeeded runs
carry none -- see docs/superpowers/specs/2026-07-31-stranded-transcript-runs-design.md.

Exit code encodes the specific claim this script exists to check: every asset that has
transcript segments *somewhere* in the table resolves, via get_latest_transcript_run, to a run
that actually returns those segments. That catches a regression of the exact bug this SDD arc
fixed (the resolver going back to returning None, or picking an empty run, for an asset whose
transcript is real) without hardcoding which assets are expected to have one.
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


def _copy_db(source: Path, dest: Path) -> None:
    """Copy the DB and any -wal/-shm sidecars, mirroring eval_cut_cli._open_db_copy.

    ``sqlite.py``'s ``connect()`` always sets ``PRAGMA journal_mode = WAL``, so a live
    laura.db held open by the desktop app can have committed frames sitting in ``-wal`` that
    haven't been checkpointed into the main file yet. Copying only the ``.db`` file risks a
    stale/incomplete snapshot with no signal that anything was missed.
    """
    shutil.copy2(source, dest)
    for suffix in ("-wal", "-shm"):
        side = source.with_name(source.name + suffix)
        if side.exists():
            shutil.copy2(side, dest.with_name(dest.name + suffix))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_path", type=Path, help="path to the live laura.db (read-only)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "laura.db"
        _copy_db(args.db_path, copy)
        db = SqliteDatabase(copy)

        with db.connection() as conn:
            projects = [dict(r) for r in conn.execute("SELECT id, name FROM projects")]
            assets = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, project_id, display_name FROM media_assets ORDER BY display_name"
                )
            ]
            assets_with_segments = {
                str(r["asset_id"])
                for r in conn.execute("SELECT DISTINCT asset_id FROM transcript_segments")
            }

        ok = True
        resolved_with_segments: set[str] = set()
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
            else:
                resolved_with_segments.add(str(asset["id"]))

        stranded = assets_with_segments - resolved_with_segments
        if stranded:
            ok = False
            logger.info(
                "REGRESSION: %d asset(s) have transcript segments in the table but no run "
                "resolves to them: %s",
                len(stranded), sorted(stranded),
            )

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
