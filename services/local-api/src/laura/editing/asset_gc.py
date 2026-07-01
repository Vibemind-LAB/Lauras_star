"""Redo-safe GC for orphaned synthetic assets (#2).

A generated (``synthetic``) media asset stays reachable as long as ANY live clip references it OR
any undo/redo history snapshot does. Deleting one still reachable via a redo snapshot would break
redo (the snapshot re-inserts a clip whose ``asset_id`` FK is gone). So this GC removes a
synthetic asset only when referenced by neither — and it is deliberately conservative: an asset
id appearing anywhere in a ``timeline_history`` payload counts as referenced (an over-approximation
that only ever keeps assets, never deletes a reachable one).
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..db import repos
from ..db.database import Database

logger = logging.getLogger(__name__)


def _is_referenced(db: Database, asset_id: str) -> bool:
    """True if ``asset_id`` is referenced by any live clip (video/audio) or history snapshot."""
    with db.connection() as conn:
        for sql in (
            "SELECT 1 FROM timeline_clips WHERE asset_id=? LIMIT 1",
            "SELECT 1 FROM timeline_audio_clips WHERE asset_id=? LIMIT 1",
        ):
            if conn.execute(sql, (asset_id,)).fetchone() is not None:
                return True
        # Conservative history reachability: the id appearing anywhere in a snapshot payload means
        # a redo could restore a clip referencing it — keep the asset.
        row = conn.execute(
            "SELECT 1 FROM timeline_history WHERE payload_json LIKE ? LIMIT 1",
            (f"%{asset_id}%",),
        ).fetchone()
        return row is not None


def _asset_file_paths(db: Database, asset_id: str, source_path: str | None) -> list[str]:
    """All on-disk paths owned by the asset: its source_path plus every registered asset_file."""
    candidates: list[str] = []
    if source_path:
        candidates.append(str(source_path))
    for f in repos.list_asset_files(db, asset_id):
        p = f.get("path")
        if p:
            candidates.append(str(p))
    seen: set[str] = set()
    out: list[str] = []
    for p in candidates:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def gc_orphaned_synthetic_assets(db: Database, *, project_id: str | None = None) -> list[str]:
    """Delete synthetic assets referenced by neither live state nor any history snapshot.

    Scoped to ``project_id`` when given, else all projects. Unlinks each deleted asset's files
    (best-effort — a missing or locked file never fails the GC). Returns the deleted asset ids.
    """
    with db.connection() as conn:
        if project_id is None:
            rows = conn.execute(
                "SELECT id, source_path FROM media_assets WHERE synthetic=1"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, source_path FROM media_assets WHERE synthetic=1 AND project_id=?",
                (project_id,),
            ).fetchall()

    deleted: list[str] = []
    for row in rows:
        asset_id = str(row["id"])
        if _is_referenced(db, asset_id):
            continue
        paths = _asset_file_paths(db, asset_id, row["source_path"])
        if repos.delete_asset(db, asset_id):
            deleted.append(asset_id)
            for p in paths:
                try:
                    Path(p).unlink(missing_ok=True)
                except OSError as exc:  # a locked/absent file must not fail the GC
                    logger.warning("asset_gc: could not unlink %s: %s", p, exc)
    return deleted
