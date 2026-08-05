"""Deterministic line -> scene matching (Task 9, Transkript-Gates).

After a script (re-)approval the storyline must follow the TEXT, not the other way round:
this module answers, for each script line, which rough-cut scene of a given asset actually
carries that text. It is a pure derivation over the transcript — no LLM call, same score
every time for the same board state.

Reuses :func:`laura.short_creator.discovery._segment_hits` (lexical+semantic with the existing
fallback) and the segment->scene mapping established in
:func:`laura.short_creator.discovery.search_material`: a hit's ``start_frame`` lands inside a
scene's ``[src_start, src_end_exclusive)`` range, exactly as that function does it. This module
does not invent a parallel mapping — it is the same one, restricted to a single asset (each
line is searched project-wide, like ``search_material``, and hits belonging to a different
asset are dropped before mapping) and reduced to the single best-scoring scene per line rather
than a ranked list.
"""

from __future__ import annotations

from typing import Any

from ..db.database import Database
from . import discovery

# Mirrors search_material's default — generous enough that a line's real hit is not pushed
# out of a project-wide result by unrelated segments before the per-asset filter runs.
_LIMIT = 40


def match_lines_to_scenes(
    db: Database, project_id: str, asset_id: str, lines: list[str]
) -> list[dict[str, Any]]:
    """One entry per line, in order: ``{"line_index", "scene_number", "score", "matched_text"}``.

    Each line is used as the topic for its own ``_segment_hits`` call (one call per line, per
    the reference approach), hits are filtered to *asset_id*, then mapped onto that asset's
    rough-cut scenes via :func:`discovery._scene_ranges` — the same read-only mapping
    ``search_material`` uses. The best-scoring hit that lands inside a scene wins;
    ``scene_number`` is ``None`` when no hit for that line lands inside any scene of this asset
    (no rough cut, no matching segment, or every hit belongs to a different asset).
    """
    ranges = discovery._scene_ranges(db, project_id, asset_id)
    out: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        best_scene: int | None = None
        best_score = 0.0
        best_text: str | None = None
        if ranges is not None:
            hits, _source = discovery._segment_hits(db, project_id, line, _LIMIT)
            for hit in hits:
                if str(hit.get("asset_id")) != asset_id:
                    continue
                start = int(hit.get("start_frame", 0))
                scene_number = next(
                    (n for n, lo, hi in ranges if lo <= start < hi), None
                )
                if scene_number is None:
                    continue
                score = float(hit.get("score") or 1.0)  # lexical rows carry no score -> 1.0/hit
                if best_scene is None or score > best_score:
                    best_scene = scene_number
                    best_score = score
                    best_text = str(hit.get("text", ""))
        out.append(
            {
                "line_index": index,
                "scene_number": best_scene,
                "score": best_score,
                "matched_text": best_text,
            }
        )
    return out
