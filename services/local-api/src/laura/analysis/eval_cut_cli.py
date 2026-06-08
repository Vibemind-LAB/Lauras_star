"""CLI: report cut-exactness for an analysed asset straight from the workspace DB.

    uv run --no-sync python -m laura.analysis.eval_cut_cli <asset-id-prefix> [--window N]

Resolves the workspace SQLite DB, opens a *copy* (to dodge WAL/locks while the desktop app
may hold it), looks up the asset by id-prefix, takes its LATEST analysis run, derives the
internal cut boundaries from that run's shots, resolves the asset's video (proxy preferred,
else original), runs :func:`laura.analysis.eval_cut.evaluate_boundaries`, and prints a
readable report. Read-only and side-effect free — no app restart required.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from .eval_cut import DEFAULT_WINDOW, CutEvalReport, evaluate_boundaries


def default_db_path() -> Path | None:
    """Best-effort locate ``laura.db`` in the user's workspace.

    Note: ``%APPDATA%`` already resolves to ``...\\AppData\\Roaming``, so the real path is
    ``%APPDATA%\\Laura\\workspace\\laura.db`` even though docs spell it ``%APPDATA%\\Roaming\\...``.
    Both spellings are probed, plus ``$LAURA_WORKSPACE`` if set.
    """
    candidates: list[Path] = []
    ws = os.environ.get("LAURA_WORKSPACE")
    if ws:
        candidates.append(Path(ws) / "laura.db")
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "Laura" / "workspace" / "laura.db")
        candidates.append(Path(appdata) / "Roaming" / "Laura" / "workspace" / "laura.db")
    for c in candidates:
        if c.is_file():
            return c
    return None


def _open_db_copy(db_path: Path) -> sqlite3.Connection:
    """Copy the DB (and any -wal/-shm sidecars) to a temp file and open it read-only-ish."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="laura-evalcut-"))
    tmp_db = tmp_dir / "laura.db"
    shutil.copy2(db_path, tmp_db)
    for suffix in ("-wal", "-shm"):
        side = db_path.with_name(db_path.name + suffix)
        if side.exists():
            shutil.copy2(side, tmp_db.with_name(tmp_db.name + suffix))
    conn = sqlite3.connect(str(tmp_db))
    conn.row_factory = sqlite3.Row
    return conn


def resolve_asset_id(conn: sqlite3.Connection, prefix: str) -> tuple[str, str]:
    """Return (asset_id, display_name) for the single asset whose id starts with ``prefix``."""
    rows = conn.execute(
        "SELECT id, display_name FROM media_assets WHERE id LIKE ? ORDER BY id",
        (prefix + "%",),
    ).fetchall()
    if not rows:
        raise LookupError(f"no asset with id prefix {prefix!r}")
    if len(rows) > 1:
        ids = ", ".join(r["id"][:12] for r in rows)
        raise LookupError(f"prefix {prefix!r} is ambiguous: {ids}")
    return rows[0]["id"], rows[0]["display_name"]


def latest_run_id(conn: sqlite3.Connection, asset_id: str) -> str:
    """Latest analysis run for the asset (by finished_at, then started_at, then rowid)."""
    row = conn.execute(
        "SELECT id FROM analysis_runs WHERE asset_id = ? "
        "ORDER BY COALESCE(finished_at, started_at, '') DESC, rowid DESC LIMIT 1",
        (asset_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"asset {asset_id[:12]} has no analysis runs")
    return str(row["id"])


def boundaries_for_run(conn: sqlite3.Connection, asset_id: str, run_id: str) -> list[int]:
    """Internal cut frames = each shot's ``src_in_frame`` (ordered), dropping the leading 0."""
    rows = conn.execute(
        "SELECT src_in_frame FROM shots WHERE asset_id = ? AND analysis_run_id = ? "
        "ORDER BY src_in_frame",
        (asset_id, run_id),
    ).fetchall()
    starts = [int(r["src_in_frame"]) for r in rows]
    return [b for b in starts if b != 0]


def resolve_video(conn: sqlite3.Connection, asset_id: str) -> Path:
    """Prefer the proxy file, else original, else the asset's source_path."""
    rows = conn.execute(
        "SELECT kind, path FROM asset_files WHERE asset_id = ?", (asset_id,)
    ).fetchall()
    by_kind = {r["kind"]: r["path"] for r in rows}
    for kind in ("proxy", "original"):
        if kind in by_kind and Path(by_kind[kind]).is_file():
            return Path(by_kind[kind])
    src = conn.execute(
        "SELECT source_path FROM media_assets WHERE id = ?", (asset_id,)
    ).fetchone()
    if src and Path(src["source_path"]).is_file():
        return Path(src["source_path"])
    raise FileNotFoundError(f"no readable video file for asset {asset_id[:12]}")


def format_report(
    report: CutEvalReport, *, asset_id: str, display_name: str, video: Path, window: int
) -> str:
    lines = [
        "=== Cut-exactness baseline ===",
        f"asset      : {asset_id[:12]}  ({display_name})",
        f"video      : {video}",
        f"window     : +/-{window} frames",
        f"boundaries : {report.n_boundaries}",
    ]
    if report.n_boundaries == 0:
        lines.append("(no internal boundaries to evaluate)")
        return "\n".join(lines)
    lines += [
        f"mean|offset|: {report.mean_abs_offset:.3f} frames",
        f"pct_exact   : {report.pct_exact * 100:.1f}%  (|offset| == 0)",
        f"pct_within1 : {report.pct_within1 * 100:.1f}%  (|offset| <= 1)",
        f"pct_within2 : {report.pct_within2 * 100:.1f}%  (|offset| <= 2)",
        f"n_imprecise : {report.n_imprecise}  (|offset| > 2)",
        f"exactness   : {report.exactness_score:.3f}  (== pct_within1)",
        "",
        "worst offenders (boundary -> offset):",
    ]
    for b, off in report.worst:
        lines.append(f"  B={b:<7d} offset={off:+d}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cut-exactness eval for an analysed asset.")
    parser.add_argument("asset_prefix", help="asset id prefix (e.g. 1098bc7e)")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW, help="search half-window")
    parser.add_argument("--db", type=Path, default=None, help="override workspace laura.db path")
    args = parser.parse_args(argv)

    db_path = args.db or default_db_path()
    if db_path is None or not db_path.is_file():
        print(f"workspace DB not found (looked at {db_path})", file=sys.stderr)
        return 2

    conn = _open_db_copy(db_path)
    try:
        asset_id, display_name = resolve_asset_id(conn, args.asset_prefix)
        run_id = latest_run_id(conn, asset_id)
        boundaries = boundaries_for_run(conn, asset_id, run_id)
        video = resolve_video(conn, asset_id)
    except (LookupError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        conn.close()

    report = evaluate_boundaries(video, boundaries, window=args.window)
    print(format_report(
        report, asset_id=asset_id, display_name=display_name, video=video, window=args.window
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
