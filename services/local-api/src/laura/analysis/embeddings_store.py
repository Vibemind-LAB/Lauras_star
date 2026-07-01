"""Frame-embedding vector store — SQLite + numpy cosine similarity (VE2).

Local-first, model-free storage layer for pre-computed frame embeddings.
Takes already-computed float32 vectors; no embedder, no fastembed, no qdrant here.

Usage pattern::

    store = SqliteVectorStore(db)
    store.replace_frame_embeddings(asset_id, run_id, items)
    hits = store.search(query_vec, asset_id=asset_id, analysis_run_id=run_id, k=5)

The :class:`VectorStore` Protocol defines the interface so a future Qdrant backend
can be swapped in without touching call sites.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np

from ..db.base import Database
from ..util import new_id, utcnow_iso

# ---------------------------------------------------------------------------
# Data type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrameEmbedding:
    """One frame's embedding from a specific analysis run.

    ``vector`` is a 1-D float32 numpy array.  ``dims`` is derived from the
    vector length so it is always consistent with the stored data.
    """

    asset_id: str
    analysis_run_id: str
    frame: int
    model: str
    vector: np.ndarray  # dtype=float32, 1-D

    @property
    def dims(self) -> int:
        """Number of dimensions in the embedding vector."""
        return int(self.vector.shape[0])


# ---------------------------------------------------------------------------
# BLOB encode / decode helpers
# ---------------------------------------------------------------------------


def _encode_vector(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype=np.float32).tobytes()


def _decode_vector(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32).copy()


# ---------------------------------------------------------------------------
# Protocol (thin interface for future backends)
# ---------------------------------------------------------------------------


@runtime_checkable
class VectorStore(Protocol):
    """Interface contract shared by SqliteVectorStore and future Qdrant backends."""

    def replace_frame_embeddings(
        self,
        asset_id: str,
        analysis_run_id: str,
        items: Sequence[FrameEmbedding],
    ) -> None: ...

    def list_frame_embeddings(
        self,
        asset_id: str,
        analysis_run_id: str,
    ) -> list[FrameEmbedding]: ...

    def search(
        self,
        query: np.ndarray,
        *,
        asset_id: str,
        analysis_run_id: str,
        k: int = 10,
    ) -> list[tuple[FrameEmbedding, float]]: ...

    def delete(self, asset_id: str, analysis_run_id: str) -> None: ...


# ---------------------------------------------------------------------------
# SQLite implementation
# ---------------------------------------------------------------------------


class SqliteVectorStore:
    """Brute-force cosine-similarity vector store backed by SQLite BLOBs.

    Reads use ``db.connection()``; writes use ``db.transaction()`` — matching
    the pattern used by the rest of Laura's repository layer.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def replace_frame_embeddings(
        self,
        asset_id: str,
        analysis_run_id: str,
        items: Sequence[FrameEmbedding],
    ) -> None:
        """Delete all embeddings for *(asset_id, analysis_run_id)* and insert *items*.

        Runs in a single transaction so callers never see a partial state.
        """
        now = utcnow_iso()
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM frame_embeddings WHERE asset_id=? AND analysis_run_id=?",
                (asset_id, analysis_run_id),
            )
            for item in items:
                conn.execute(
                    "INSERT INTO frame_embeddings "
                    "(id, asset_id, analysis_run_id, frame, model, dims, vector, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_id(),
                        item.asset_id,
                        item.analysis_run_id,
                        int(item.frame),
                        item.model,
                        item.dims,
                        _encode_vector(item.vector),
                        now,
                    ),
                )

    def delete(self, asset_id: str, analysis_run_id: str) -> None:
        """Remove all embeddings for *(asset_id, analysis_run_id)*."""
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM frame_embeddings WHERE asset_id=? AND analysis_run_id=?",
                (asset_id, analysis_run_id),
            )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def list_frame_embeddings(
        self,
        asset_id: str,
        analysis_run_id: str,
    ) -> list[FrameEmbedding]:
        """Return all stored embeddings for *(asset_id, analysis_run_id)*, ordered by frame."""
        with self._db.connection() as conn:
            rows = conn.execute(
                "SELECT asset_id, analysis_run_id, frame, model, vector "
                "FROM frame_embeddings "
                "WHERE asset_id=? AND analysis_run_id=? "
                "ORDER BY frame",
                (asset_id, analysis_run_id),
            ).fetchall()
        return [
            FrameEmbedding(
                asset_id=row["asset_id"],
                analysis_run_id=row["analysis_run_id"],
                frame=int(row["frame"]),
                model=row["model"],
                vector=_decode_vector(row["vector"]),
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: np.ndarray,
        *,
        asset_id: str,
        analysis_run_id: str,
        k: int = 10,
    ) -> list[tuple[FrameEmbedding, float]]:
        """Brute-force cosine-similarity search.

        Returns up to *k* *(embedding, score)* pairs, sorted descending by score.
        Zero-vectors (query or stored) produce a score of 0.0 rather than NaN.
        """
        candidates = self.list_frame_embeddings(asset_id, analysis_run_id)
        if not candidates:
            return []

        q = np.asarray(query, dtype=np.float32)
        q_norm = float(np.linalg.norm(q))
        if q_norm == 0.0:
            return [(emb, 0.0) for emb in candidates[:k]]

        q_unit = q / q_norm

        scored: list[tuple[FrameEmbedding, float]] = []
        for emb in candidates:
            v = emb.vector
            v_norm = float(np.linalg.norm(v))
            if v_norm == 0.0:
                scored.append((emb, 0.0))
            else:
                sim = float(np.dot(q_unit, v / v_norm))
                scored.append((emb, sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]
