"""Topic -> ranked material across the whole project (auto-short discovery layer).

Semantic when the [semantic] extra answers (the same index the search endpoint uses),
lexical fallback otherwise — one row shape either way. Hits are mapped onto each asset's
rough-cut scenes READ-ONLY: the ranking must never create timelines as a side effect
(spec 2026-07-21-auto-short-design.md §1)."""

from __future__ import annotations

import logging
import re
from typing import Any

from ..db import repos
from ..db.database import Database
from ..semantic import get_index
from . import context

logger = logging.getLogger(__name__)

_SNIPPET_CHARS = 160
_MAX_SCENE_SNIPPETS = 3
# Words that carry no topic signal. Deliberately tiny and bilingual (the boards are German,
# the transcripts often English): this is a noise filter, not a linguistic model. A word too
# short to be distinctive is dropped by _MIN_TERM_CHARS instead of being listed here.
_STOPWORDS = frozenset(
    {
        "aber", "auch", "auf", "aus", "bei", "das", "dem", "den", "der", "des", "die", "ein",
        "eine", "einen", "einer", "für", "fuer", "hier", "ist", "mit", "nicht", "noch", "oder",
        "per", "sich", "sind", "über", "ueber", "und", "van", "von", "was", "wie", "wir", "zum",
        "zur", "and", "are", "but", "for", "from", "how", "into", "not", "the", "this", "that",
        "with", "you", "your", "what", "when", "why", "does", "can", "via",
    }
)
_MIN_TERM_CHARS = 3


def topic_terms(topic: str) -> list[str]:
    """The distinctive words of *topic*, lowercased, in order, without repeats.

    Splits on everything that is not a letter or digit, so a compound written with a hyphen
    ("Desktop-Automatisierung") becomes the two words a transcript is likely to contain
    separately. Empty when the topic is only stopwords — the caller then falls back to the raw
    phrase rather than matching everything.
    """
    seen: list[str] = []
    for raw in re.split(r"[^0-9A-Za-zÄÖÜäöüß]+", topic.lower()):
        if len(raw) < _MIN_TERM_CHARS or raw in _STOPWORDS or raw in seen:
            continue
        seen.append(raw)
    return seen


def _lexical_hits(
    db: Database, project_id: str, topic: str, limit: int
) -> list[dict[str, Any]]:
    """Lexical hits for *topic*, matched WORD BY WORD and scored by distinct words hit.

    ``repos.search_transcript`` is a substring match, so handing it a whole sentence asks a
    transcript to contain that sentence verbatim — live 2026-08-02 that returned nothing for a
    topic the material plainly covered, and with Qdrant down this path IS the discovery. So the
    topic is taken apart and each word searched on its own; a segment that matched two of the
    topic's words outranks one that matched a single word. A topic with no distinctive words
    left keeps the old whole-phrase behaviour.
    """
    terms = topic_terms(topic)
    if not terms:
        return repos.search_transcript(db, project_id=project_id, query=topic, limit=limit)
    merged: dict[str, dict[str, Any]] = {}
    for term in terms:
        for row in repos.search_transcript(db, project_id=project_id, query=term, limit=limit):
            key = str(row.get("segment_id"))
            hit = merged.get(key)
            if hit is None:
                hit = {**row, "score": 0.0}
                merged[key] = hit
            hit["score"] = float(hit["score"]) + 1.0
    ranked = sorted(merged.values(), key=lambda h: (-float(h["score"]), str(h["segment_id"])))
    return ranked[:limit]


def _segment_hits(
    db: Database, project_id: str, topic: str, limit: int
) -> tuple[list[dict[str, Any]], str]:
    """(hits, source): semantic when the index exists AND answers, else lexical.
    Mirrors api/search.py's fallback stance — a broken index degrades, never raises."""
    try:
        index = get_index()
    except Exception:  # noqa: BLE001 - semantic search is best-effort: a down/unreachable
        # Qdrant server raises during client/collection construction; degrade to lexical
        # instead of bubbling a 500 out of this read endpoint.
        logger.warning("semantic index unavailable; falling back to lexical", exc_info=True)
        index = None
    if index is not None:
        try:
            hits = index.query(topic, project_id=project_id, limit=limit)
        except Exception:  # noqa: BLE001 - semantic search is best-effort
            logger.warning("semantic query failed; falling back to lexical", exc_info=True)
            hits = []
        if hits:
            return hits, "semantic"
    return _lexical_hits(db, project_id, topic, limit), "lexical"


def _scene_ranges(
    db: Database, project_id: str, asset_id: str
) -> list[tuple[int, int, int]] | None:
    """[(scene_number, src_start, src_end_exclusive)] for the asset's rough cut, or None
    when there is no rough cut / no scenes. Mirrors production_tools._resolve_scene's
    composition (list_scenes order_index+1, clips, context._scene_src_ranges) but strictly
    read-only."""
    timeline = repos.get_asset_rough_cut(db, project_id, asset_id)
    if timeline is None:
        return None
    scenes = repos.list_scenes(db, str(timeline["id"]))
    if not scenes:
        return None
    clips = repos.list_timeline_clips(db, str(timeline["id"]))
    out: list[tuple[int, int, int]] = []
    for scene in scenes:
        ranges = context._scene_src_ranges(
            clips,
            seq_in=int(scene["seq_in_frame"]),
            seq_out_exclusive=int(scene["seq_out_frame_exclusive"]),
        )
        if not ranges:
            continue
        src_start, src_end_exclusive = ranges[0][0], ranges[-1][1]
        out.append((int(scene["order_index"]) + 1, src_start, src_end_exclusive))
    return out


def search_material(
    db: Database, project_id: str, topic: str, *, limit: int = 40
) -> dict[str, Any]:
    hits, source = _segment_hits(db, project_id, topic, limit)
    per_asset: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, str]] = []
    ranges_cache: dict[str, list[tuple[int, int, int]] | None] = {}
    for hit in hits:
        asset_id = str(hit["asset_id"])
        if asset_id not in ranges_cache:
            ranges_cache[asset_id] = _scene_ranges(db, project_id, asset_id)
            if ranges_cache[asset_id] is None:
                skipped.append({"asset_id": asset_id, "reason": "no rough cut"})
        ranges = ranges_cache[asset_id]
        if ranges is None:
            continue
        start = int(hit.get("start_frame", 0))
        scene_number = next(
            (n for n, lo, hi in ranges if lo <= start < hi), None
        )
        if scene_number is None:
            continue
        score = float(hit.get("score") or 1.0)  # lexical rows carry no score -> 1.0/hit
        entry = per_asset.setdefault(
            asset_id,
            {
                "asset_id": asset_id,
                "display_name": str(hit.get("asset_name", "")),
                "score": 0.0,
                "scene_hits": [],
            },
        )
        entry["score"] += score
        entry["scene_hits"].append(
            {
                "scene_number": scene_number,
                "snippet": str(hit.get("text", ""))[:_SNIPPET_CHARS],
                "score": score,
                # Auto-overview (spec 2026-07-31 §2) builds its candidate windows from these;
                # Phase 1 never reads them. `end_frame` is ALREADY end-exclusive
                # (mapping.map_segment -> snap_out_to_frame, CEIL) — carried verbatim.
                "start_frame": start,
                "end_frame_exclusive": int(hit.get("end_frame", start)),
            }
        )
    ranking = sorted(per_asset.values(), key=lambda e: e["score"], reverse=True)
    for entry in ranking:
        # strongest first, capped per asset; stable by scene_number for equal scores
        entry["scene_hits"] = sorted(
            entry["scene_hits"], key=lambda h: (-h["score"], h["scene_number"])
        )[:_MAX_SCENE_SNIPPETS]
        entry["scene_hits"].sort(key=lambda h: h["scene_number"])
    return {"source": source, "ranking": ranking, "skipped": skipped}
