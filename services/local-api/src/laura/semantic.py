"""Semantic transcript search via Qdrant + fastembed (optional ``[semantic]`` extra).

Transcript text is embedded with a small CPU ONNX model (fastembed, no GPU/token) and
stored/queried as vectors in Qdrant. The location is configurable: ``:memory:`` (tests),
or a server URL via ``QDRANT_URL`` (on-prem, shared). Degrades gracefully when the extra
is absent — callers check :func:`semantic_available` first. A process-global singleton is
shared between the indexing job (runner thread) and the search endpoint.
"""

from __future__ import annotations

import os
import threading
import uuid
from typing import Any

_MODEL = "BAAI/bge-small-en-v1.5"   # fastembed default: 384-dim, CPU, no auth
_DIM = 384

_instance: SemanticIndex | None = None
_instance_lock = threading.Lock()


def semantic_available() -> bool:
    try:
        import fastembed  # noqa: F401
        import qdrant_client  # noqa: F401
    except Exception:
        return False
    return True


def _point_id(value: str) -> str:
    """Qdrant needs an int or canonical UUID; map our uuid-hex ids to dashed UUIDs."""
    try:
        return str(uuid.UUID(hex=value))
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, value))


class SemanticIndex:
    """Thin wrapper over a Qdrant collection with fastembed inference."""

    def __init__(self, location: str = ":memory:", *, collection: str = "transcripts") -> None:
        from qdrant_client import QdrantClient, models

        self._models = models
        self._client = QdrantClient(location=location)
        self._collection = collection
        self._lock = threading.Lock()
        if not self._client.collection_exists(collection):
            self._client.create_collection(
                collection,
                vectors_config=models.VectorParams(size=_DIM, distance=models.Distance.COSINE),
            )

    def index(self, items: list[dict[str, Any]]) -> int:
        """Upsert ``items`` (each a dict with ``id``, ``text`` and a ``payload`` dict)."""
        models = self._models
        points = [
            models.PointStruct(
                id=_point_id(str(item["id"])),
                vector=models.Document(text=str(item["text"]), model=_MODEL),
                payload=item["payload"],
            )
            for item in items
            if item.get("text")
        ]
        if points:
            with self._lock:
                self._client.upsert(self._collection, points=points)
        return len(points)

    def delete_asset(self, asset_id: str) -> None:
        models = self._models
        with self._lock:
            self._client.delete(
                self._collection,
                points_selector=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="asset_id", match=models.MatchValue(value=asset_id)
                        )
                    ]
                ),
            )

    def query(self, text: str, *, project_id: str, limit: int = 10) -> list[dict[str, Any]]:
        models = self._models
        with self._lock:
            response = self._client.query_points(
                self._collection,
                query=models.Document(text=text, model=_MODEL),
                query_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="project_id", match=models.MatchValue(value=project_id)
                        )
                    ]
                ),
                limit=limit,
                with_payload=True,
            )
        return [{"score": float(hit.score), **dict(hit.payload or {})} for hit in response.points]


def get_index() -> SemanticIndex | None:
    """Return the shared index — a Qdrant server when ``QDRANT_URL`` is set, else an
    in-memory store — or ``None`` when the ``[semantic]`` extra is absent."""
    global _instance
    if not semantic_available():
        return None
    with _instance_lock:
        if _instance is None:
            _instance = SemanticIndex(os.environ.get("QDRANT_URL") or ":memory:")
        return _instance
