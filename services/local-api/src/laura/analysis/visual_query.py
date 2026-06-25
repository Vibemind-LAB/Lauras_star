"""Queryable visual timeline over frame embeddings (VE5) — pure, testable logic.

Gives an agent visual search / compare operations on top of the per-frame
embeddings produced by VE1/VE2 and persisted in
:mod:`laura.analysis.embeddings_store`.  Four operations, all taking an explicit
``db``:

* :func:`similar_segments`     — nearest candidates to a target candidate.
* :func:`deduplicate_shorts`   — greedy grouping of near-identical candidates.
* :func:`visual_hook`          — opening-strength heuristic for one candidate.
* :func:`search_visual_moments`— optional text→image search (CLIP text encoder).

The image-image operations (the first three) run with **no model**: they only
need the stored vectors.  The text→image search is **optional and graceful** —
it needs a CLIP text encoder which is an optional extra; absent that (and with no
injected ``text_embedder``) it returns ``{"ok": False, "reason": …}`` rather than
raising or downloading anything.

Reuses the VE4 helpers from :mod:`laura.analysis.shorts_score`
(``_segment_repr``, ``_visual_shift_at``, ``_visual_continuity``, ``_cosine``)
so the geometry is identical to the scorer's.  Imports nothing heavy at module
level — ``fastembed`` is only ever touched lazily inside the embedder.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from ..db import repos
from ..db.database import Database
from .embeddings_store import SqliteVectorStore
from .shorts_score import (
    _cosine,
    _segment_repr,
    _visual_continuity,
    _visual_shift_at,
)
from .visual_embed import FastEmbedClipTextEmbedder, TextEmbedder, visual_available

_log = logging.getLogger(__name__)

__all__ = [
    "similar_segments",
    "deduplicate_shorts",
    "visual_hook",
    "search_visual_moments",
]

# Fallbacks when the asset row carries no usable frame rate.
_FALLBACK_FPS: float = 25.0
# Opening window for the visual-hook heuristic, in seconds.
_HOOK_WINDOW_S: float = 2.0

_NO_EMBEDDINGS_REASON = "no frame embeddings; run shorts.embed_frames first"
_NO_TEXT_REASON = "visual extra not installed (text search unavailable)"


# ---------------------------------------------------------------------------
# Internal: load an asset's frame embeddings as a {frame: vector} map
# ---------------------------------------------------------------------------


def _asset_frame_embeddings(
    db: Database, asset_id: str
) -> dict[int, np.ndarray] | None:
    """Return ``{frame: vector}`` for the asset's latest analysis run, or ``None``.

    ``None`` means "no embeddings available" — either no analysis run, or a run
    with no stored frame vectors (VE1/VE2 not yet executed).  An empty map is
    collapsed to ``None`` so callers have a single "nothing to query" signal.
    """
    run = repos.get_latest_analysis_run(db, asset_id)
    if run is None:
        return None
    store = SqliteVectorStore(db)
    items = store.list_frame_embeddings(asset_id, run["id"])
    if not items:
        return None
    return {e.frame: e.vector for e in items}


def _asset_fps(db: Database, asset_id: str) -> float:
    """Frames-per-second from the asset's integer rate, falling back to 25.0."""
    asset = repos.get_asset(db, asset_id)
    if asset is None:
        return _FALLBACK_FPS
    num = int(asset["rate_num"] or 0)
    den = int(asset["rate_den"] or 0)
    if num <= 0 or den <= 0:
        return _FALLBACK_FPS
    return num / den


def _candidate_repr(
    cand: dict[str, Any],
    frames_sorted: list[int],
    embeddings: dict[int, np.ndarray],
) -> np.ndarray | None:
    """Mean segment embedding for a candidate dict via the VE4 ``_segment_repr``."""
    return _segment_repr(
        int(cand["start_frame"]),
        int(cand["end_frame_exclusive"]),
        frames_sorted,
        embeddings,
    )


# ---------------------------------------------------------------------------
# 1. similar_segments
# ---------------------------------------------------------------------------


def similar_segments(
    db: Database,
    asset_id: str,
    candidate_id: str,
    *,
    k: int = 5,
) -> dict[str, Any]:
    """Top-``k`` candidates visually closest to ``candidate_id`` within the asset.

    Uses each candidate's mean segment embedding (``_segment_repr``) and ranks the
    *other* candidates by cosine similarity to the target's, descending.

    Returns ``{"ok": True, "candidate_id", "similar": [...]}`` where each entry is
    ``{"candidate_id", "score", "start_frame", "end_frame_exclusive"}``.

    Graceful failures:
    * no frame embeddings        → ``{"ok": False, "reason": <no-embeddings>}``
    * unknown ``candidate_id``   → ``{"ok": False, "reason": "candidate not found"}``
    """
    embeddings = _asset_frame_embeddings(db, asset_id)
    if embeddings is None:
        return {"ok": False, "reason": _NO_EMBEDDINGS_REASON}

    candidates = repos.list_shorts_candidates_by_asset(db, asset_id)
    target = next((c for c in candidates if c["id"] == candidate_id), None)
    if target is None:
        return {"ok": False, "reason": "candidate not found"}

    frames_sorted = sorted(embeddings)
    target_repr = _candidate_repr(target, frames_sorted, embeddings)
    if target_repr is None:
        # No embedding lands at/after the target window — nothing comparable.
        return {"ok": True, "candidate_id": candidate_id, "similar": []}

    scored: list[dict[str, Any]] = []
    for c in candidates:
        if c["id"] == candidate_id:
            continue
        rep = _candidate_repr(c, frames_sorted, embeddings)
        if rep is None:
            continue
        scored.append(
            {
                "candidate_id": c["id"],
                "score": _cosine(target_repr, rep),
                "start_frame": int(c["start_frame"]),
                "end_frame_exclusive": int(c["end_frame_exclusive"]),
            }
        )

    scored.sort(key=lambda x: x["score"], reverse=True)
    return {
        "ok": True,
        "candidate_id": candidate_id,
        "similar": scored[:k],
    }


# ---------------------------------------------------------------------------
# 2. deduplicate_shorts
# ---------------------------------------------------------------------------


def deduplicate_shorts(
    db: Database,
    asset_id: str,
    *,
    threshold: float = 0.9,
) -> dict[str, Any]:
    """Greedily group near-identical candidates by segment-embedding similarity.

    Candidates are processed in descending ``score`` order.  Each not-yet-grouped
    candidate becomes a *keeper*; every later candidate whose segment-repr cosine to
    the keeper is ``>= threshold`` is folded in as a *duplicate*.

    Returns
    ``{"ok": True, "groups": [{"keep", "duplicates": [...]}], "kept": [...], "dropped": [...]}``.
    A candidate with no usable segment-repr is kept as its own singleton group.

    No frame embeddings → ``{"ok": False, "reason": <no-embeddings>}``.
    """
    embeddings = _asset_frame_embeddings(db, asset_id)
    if embeddings is None:
        return {"ok": False, "reason": _NO_EMBEDDINGS_REASON}

    candidates = repos.list_shorts_candidates_by_asset(db, asset_id)
    frames_sorted = sorted(embeddings)

    # Highest score first; ties keep their list order (stable sort).
    ordered = sorted(
        candidates, key=lambda c: float(c.get("score") or 0.0), reverse=True
    )
    reprs: dict[str, np.ndarray | None] = {
        c["id"]: _candidate_repr(c, frames_sorted, embeddings) for c in ordered
    }

    grouped: set[str] = set()
    groups: list[dict[str, Any]] = []
    kept: list[str] = []
    dropped: list[str] = []

    for keeper in ordered:
        kid = keeper["id"]
        if kid in grouped:
            continue
        grouped.add(kid)
        kept.append(kid)
        keeper_repr = reprs[kid]
        dupes: list[str] = []
        if keeper_repr is not None:
            for other in ordered:
                oid = other["id"]
                if oid in grouped:
                    continue
                orep = reprs[oid]
                if orep is None:
                    continue
                if _cosine(keeper_repr, orep) >= threshold:
                    grouped.add(oid)
                    dupes.append(oid)
                    dropped.append(oid)
        groups.append({"keep": kid, "duplicates": dupes})

    return {
        "ok": True,
        "groups": groups,
        "kept": kept,
        "dropped": dropped,
    }


# ---------------------------------------------------------------------------
# 3. visual_hook
# ---------------------------------------------------------------------------


def visual_hook(
    db: Database,
    asset_id: str,
    candidate_id: str,
) -> dict[str, Any]:
    """Score how strong a candidate's *opening* is, visually.

    Blends two VE4 signals at the candidate's start:
    * ``visual_shift_at_start`` — sharpness of the visual change entering the clip
      (``1 - cosine`` across the start cut); a hard cut into the clip reads as a
      strong hook.
    * ``opening_continuity``    — visual coherence of the first ``_HOOK_WINDOW_S``
      seconds; a clean, stable opening shot.

    ``hook_score`` blends two normalised signals and is guaranteed to be in
    **[0, 1]**:

    * ``shift_norm = shift / 2``  — maps ``shift ∈ [0, 2]`` to ``[0, 1]``.
    * ``cont_clamped = max(0, continuity)`` — drops anti-correlated values
      (negative cosine ≡ no coherence, treated as zero).
    * ``hook_score = clip(0.6 * shift_norm + 0.4 * cont_clamped, 0, 1)``.

    The raw ``visual_shift_at_start`` and ``opening_continuity`` diagnostic
    values are still returned as-is (they may lie outside [0, 1]).

    Returns ``{"ok": True, "candidate_id", "visual_shift_at_start",
    "opening_continuity", "hook_score", "explanation"}``.

    Graceful failures:
    * no frame embeddings      → ``{"ok": False, "reason": <no-embeddings>}``
    * unknown ``candidate_id`` → ``{"ok": False, "reason": "candidate not found"}``
    """
    embeddings = _asset_frame_embeddings(db, asset_id)
    if embeddings is None:
        return {"ok": False, "reason": _NO_EMBEDDINGS_REASON}

    cand = repos.get_short_candidate(db, candidate_id)
    if cand is None or cand.get("asset_id") != asset_id:
        return {"ok": False, "reason": "candidate not found"}

    frames_sorted = sorted(embeddings)
    start = int(cand["start_frame"])
    end_excl = int(cand["end_frame_exclusive"])

    fps = _asset_fps(db, asset_id)
    window_frames = max(1, round(fps * _HOOK_WINDOW_S))
    window_end = min(start + window_frames, end_excl)

    shift = _visual_shift_at(start, frames_sorted, embeddings)
    cont = _visual_continuity(start, window_end, frames_sorted, embeddings)
    # Normalise each signal to [0, 1] before blending:
    #   shift ∈ [0, 2]  → shift_norm ∈ [0, 1]
    #   cont  ∈ [-1, 1] → cont_clamped ∈ [0, 1]  (anti-corr treated as 0)
    shift_norm = shift / 2.0
    cont_clamped = max(0.0, cont)
    hook_score = min(1.0, max(0.0, 0.6 * shift_norm + 0.4 * cont_clamped))

    explanation = (
        f"Visual hook {hook_score:.2f}: opening cut shift {shift:.2f} "
        f"(norm {shift_norm:.2f}, 0.6w) + first-{_HOOK_WINDOW_S:g}s "
        f"continuity {cont:.2f} (clamped {cont_clamped:.2f}, 0.4w)."
    )

    return {
        "ok": True,
        "candidate_id": candidate_id,
        "visual_shift_at_start": shift,
        "opening_continuity": cont,
        "hook_score": hook_score,
        "explanation": explanation,
    }


# ---------------------------------------------------------------------------
# 4. search_visual_moments (optional text→image)
# ---------------------------------------------------------------------------


def search_visual_moments(
    db: Database,
    asset_id: str,
    query: str,
    *,
    k: int = 10,
    text_embedder: TextEmbedder | None = None,
) -> dict[str, Any]:
    """Find the frames whose visual embedding best matches a text ``query``.

    The query string is encoded into the *same* CLIP space as the frame vectors
    (vision/text share a space) and ranked by cosine.  The text encoder is the only
    part of VE5 that needs a model; it is **optional**:

    * ``text_embedder`` may be injected (tests pass a deterministic fake).
    * Otherwise a :class:`FastEmbedClipTextEmbedder` is used *iff* the visual extra
      is importable; if not, the call degrades gracefully.

    Returns ``{"ok": True, "query", "k", "moments": [{"frame", "time_s", "score"}]}``
    sorted by score descending.

    Graceful failures:
    * no frame embeddings                  → ``{"ok": False, "reason": <no-embeddings>}``
    * no embedder + extra absent           → ``{"ok": False, "reason": <no-text>}``
    """
    embeddings = _asset_frame_embeddings(db, asset_id)
    if embeddings is None:
        return {"ok": False, "reason": _NO_EMBEDDINGS_REASON}

    te: TextEmbedder | None = text_embedder
    if te is None:
        te = FastEmbedClipTextEmbedder() if visual_available() else None
    if te is None:
        return {"ok": False, "reason": _NO_TEXT_REASON}

    qv = np.asarray(te.embed_text(query), dtype=np.float32)

    # Guard: query vector must match the stored frame embedding dimension.
    first_vec = next(iter(embeddings.values()))
    if qv.shape[0] != first_vec.shape[0]:
        return {
            "ok": False,
            "reason": (
                f"query embedding dim {qv.shape[0]} does not match "
                f"frame embedding dim {first_vec.shape[0]}"
            ),
        }

    fps = _asset_fps(db, asset_id)

    scored: list[dict[str, Any]] = []
    for frame, vec in embeddings.items():
        scored.append(
            {
                "frame": int(frame),
                "time_s": frame / fps if fps > 0 else 0.0,
                "score": _cosine(qv, vec),
            }
        )

    scored.sort(key=lambda x: x["score"], reverse=True)
    return {
        "ok": True,
        "query": query,
        "k": k,
        "moments": scored[:k],
    }
