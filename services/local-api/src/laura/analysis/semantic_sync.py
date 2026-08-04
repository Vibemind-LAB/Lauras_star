"""Shared semantic-index item shape + best-effort per-segment re-index.

``segment_index_item`` is the exact item construction that used to live inline in
``handlers.py``'s ASR stage (the point-id/payload convention Qdrant sees). Extracted here
so a later Gate-A patch path can re-index just the segments it edited, through the same
shape, without duplicating it.

``reindex_segments`` follows the same best-effort contract as the ASR stage's embed step
(fd0914b): ``get_index()`` itself can raise when Qdrant is configured but unreachable (the
client/collection construction talks to the server). Semantic indexing must never take an
otherwise-successful edit down with it, so every exception here is logged and swallowed --
this function never raises.
"""

from __future__ import annotations

import logging
from typing import Any

from ..db import repos
from ..db.database import Database
from ..semantic import get_index

_log = logging.getLogger(__name__)


def segment_index_item(
    asset: dict[str, Any], seg_row: dict[str, Any], speaker_label: str | None
) -> dict[str, Any]:
    """Build the ``{id, text, payload}`` item ``SemanticIndex.index()`` upserts.

    ``seg_row`` must carry ``id``, ``text``, ``start_frame`` and ``end_frame`` (both the
    in-memory row from :func:`..analysis.mapping.map_segment` with an ``id`` added, and a
    DB row from :func:`..db.repos.get_transcript` satisfy this). ``speaker_label`` is taken
    as a separate argument rather than read off ``seg_row`` since the two call sites carry
    it differently (a fresh ``SegmentResult.speaker_label`` vs. the joined DB column).
    """
    return {
        "id": seg_row["id"],
        "text": seg_row["text"],
        "payload": {
            "project_id": asset["project_id"],
            "asset_id": asset["id"],
            "segment_id": seg_row["id"],
            "asset_name": asset["display_name"],
            "text": seg_row["text"],
            "start_frame": seg_row["start_frame"],
            "end_frame": seg_row["end_frame"],
            "speaker_label": speaker_label,
        },
    }


def reindex_segments(db: Database, asset_id: str, segment_ids: list[str]) -> int:
    """Best-effort re-index of specific segments (e.g. after a Gate-A text edit).

    Loads the asset's latest transcript run, filters its segments to ``segment_ids``, and
    upserts their items through ``get_index()``. Returns the number of items upserted, or
    ``0`` when there is nothing to do, semantic search is unavailable, or indexing failed --
    this never raises.
    """
    try:
        if not segment_ids:
            return 0
        asset = repos.get_asset(db, asset_id)
        if asset is None:
            return 0
        run = repos.get_latest_transcript_run(db, asset_id)
        if run is None:
            return 0
        wanted = set(segment_ids)
        segments = repos.get_transcript(db, asset_id, str(run["id"]))
        items = [
            segment_index_item(asset, seg, seg.get("speaker_label"))
            for seg in segments
            if seg["id"] in wanted
        ]
        if not items:
            return 0
        index = get_index()
        if index is None:
            return 0
        return index.index(items)
    except Exception as exc:  # noqa: BLE001 - semantic indexing is best-effort
        _log.warning("asset %s: semantic reindex failed (best-effort): %s", asset_id, exc)
        return 0
