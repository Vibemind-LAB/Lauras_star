"""Tests for the frame-embedding vector store (VE2).

Covers:
- schema_version() == 32 after migrate()
- FrameEmbedding round-trip: replace + list returns vectors bit-exact, ordered by frame, dims ok
- replace-not-accumulate: second call replaces, does not append
- search: nearest vector is top-1; identical → ~1.0, orthogonal → ~0.0
- empty search for unknown asset returns []
- zero-query vector returns 0.0 scores (no NaN)
- delete clears all rows for (asset_id, analysis_run_id)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from laura.analysis.embeddings_store import FrameEmbedding, SqliteVectorStore, VectorStore
from laura.config import Settings
from laura.db.database import SqliteDatabase

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _db(tmp_path: Path) -> SqliteDatabase:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    return db


def _vec(*values: float) -> np.ndarray:
    """Build a float32 ndarray from positional floats."""
    return np.array(values, dtype=np.float32)


_ASSET = "asset-abc"
_RUN = "run-xyz"
_MODEL = "test-clip-v0"


def _make_emb(frame: int, *values: float) -> FrameEmbedding:
    return FrameEmbedding(
        asset_id=_ASSET,
        analysis_run_id=_RUN,
        frame=frame,
        model=_MODEL,
        vector=_vec(*values),
    )


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------


def test_schema_version_is_33_after_migrate(tmp_path: Path) -> None:
    db = _db(tmp_path)
    assert db.schema_version() == 33


# ---------------------------------------------------------------------------
# Round-trip: insert 3 embeddings, read back
# ---------------------------------------------------------------------------


def test_replace_and_list_roundtrip(tmp_path: Path) -> None:
    db = _db(tmp_path)
    store = SqliteVectorStore(db)

    items = [
        _make_emb(0, 1.0, 0.0, 0.0),
        _make_emb(5, 0.0, 1.0, 0.0),
        _make_emb(10, 0.0, 0.0, 1.0),
    ]
    store.replace_frame_embeddings(_ASSET, _RUN, items)

    result = store.list_frame_embeddings(_ASSET, _RUN)

    assert len(result) == 3
    # ordered by frame
    assert [r.frame for r in result] == [0, 5, 10]
    # metadata preserved
    for r in result:
        assert r.asset_id == _ASSET
        assert r.analysis_run_id == _RUN
        assert r.model == _MODEL
    # dims correct
    assert all(r.dims == 3 for r in result)
    # vectors bit-exact (float32 round-trip)
    for original, stored in zip(items, result, strict=True):
        np.testing.assert_array_equal(original.vector, stored.vector)
        assert stored.vector.dtype == np.float32


# ---------------------------------------------------------------------------
# Replace-not-accumulate
# ---------------------------------------------------------------------------


def test_replace_does_not_accumulate(tmp_path: Path) -> None:
    db = _db(tmp_path)
    store = SqliteVectorStore(db)

    store.replace_frame_embeddings(_ASSET, _RUN, [_make_emb(0, 1.0, 0.0)])
    store.replace_frame_embeddings(_ASSET, _RUN, [_make_emb(1, 0.0, 1.0), _make_emb(2, 0.5, 0.5)])

    result = store.list_frame_embeddings(_ASSET, _RUN)
    assert len(result) == 2
    assert [r.frame for r in result] == [1, 2]


# ---------------------------------------------------------------------------
# search: nearest is top-1; identical → ~1.0; orthogonal → ~0.0
# ---------------------------------------------------------------------------


def test_search_nearest_is_top1(tmp_path: Path) -> None:
    db = _db(tmp_path)
    store = SqliteVectorStore(db)

    # Three orthogonal basis vectors
    e0 = _make_emb(0, 1.0, 0.0, 0.0)
    e1 = _make_emb(1, 0.0, 1.0, 0.0)
    e2 = _make_emb(2, 0.0, 0.0, 1.0)
    store.replace_frame_embeddings(_ASSET, _RUN, [e0, e1, e2])

    # Query closest to e1
    query = _vec(0.1, 0.9, 0.05)
    hits = store.search(query, asset_id=_ASSET, analysis_run_id=_RUN, k=3)

    assert len(hits) == 3
    top_emb, top_score = hits[0]
    assert top_emb.frame == 1, f"Expected frame 1 at top, got {top_emb.frame}"
    assert top_score > 0.9, f"Expected high cosine score, got {top_score}"


def test_search_identical_vector_scores_near_one(tmp_path: Path) -> None:
    db = _db(tmp_path)
    store = SqliteVectorStore(db)

    v = _vec(0.6, 0.8)
    store.replace_frame_embeddings(_ASSET, _RUN, [_make_emb(0, 0.6, 0.8)])

    hits = store.search(v, asset_id=_ASSET, analysis_run_id=_RUN, k=1)
    assert len(hits) == 1
    _, score = hits[0]
    assert abs(score - 1.0) < 1e-5, f"Identical vector should score ~1.0, got {score}"


def test_search_orthogonal_vector_scores_near_zero(tmp_path: Path) -> None:
    db = _db(tmp_path)
    store = SqliteVectorStore(db)

    store.replace_frame_embeddings(_ASSET, _RUN, [_make_emb(0, 1.0, 0.0)])

    query = _vec(0.0, 1.0)  # orthogonal to stored
    hits = store.search(query, asset_id=_ASSET, analysis_run_id=_RUN, k=1)
    assert len(hits) == 1
    _, score = hits[0]
    assert abs(score) < 1e-5, f"Orthogonal vector should score ~0.0, got {score}"


def test_search_results_are_sorted_descending(tmp_path: Path) -> None:
    db = _db(tmp_path)
    store = SqliteVectorStore(db)

    store.replace_frame_embeddings(
        _ASSET,
        _RUN,
        [
            _make_emb(0, 1.0, 0.0),
            _make_emb(1, 0.7, 0.7),
            _make_emb(2, 0.0, 1.0),
        ],
    )
    query = _vec(1.0, 0.0)  # closest to frame 0
    hits = store.search(query, asset_id=_ASSET, analysis_run_id=_RUN, k=3)

    scores = [s for _, s in hits]
    assert scores == sorted(scores, reverse=True), "search results not sorted descending"


# ---------------------------------------------------------------------------
# Empty / unknown asset
# ---------------------------------------------------------------------------


def test_search_unknown_asset_returns_empty(tmp_path: Path) -> None:
    db = _db(tmp_path)
    store = SqliteVectorStore(db)

    hits = store.search(_vec(1.0, 0.0), asset_id="no-such-asset", analysis_run_id="no-run", k=5)
    assert hits == []


def test_list_unknown_asset_returns_empty(tmp_path: Path) -> None:
    db = _db(tmp_path)
    store = SqliteVectorStore(db)

    result = store.list_frame_embeddings("no-such-asset", "no-run")
    assert result == []


# ---------------------------------------------------------------------------
# Zero-vector query — no NaN
# ---------------------------------------------------------------------------


def test_search_zero_query_returns_zero_scores(tmp_path: Path) -> None:
    db = _db(tmp_path)
    store = SqliteVectorStore(db)

    store.replace_frame_embeddings(_ASSET, _RUN, [_make_emb(0, 1.0, 0.0)])

    query = _vec(0.0, 0.0)
    hits = store.search(query, asset_id=_ASSET, analysis_run_id=_RUN, k=1)
    assert len(hits) == 1
    _, score = hits[0]
    assert score == 0.0
    assert not np.isnan(score)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def test_delete_removes_all_embeddings(tmp_path: Path) -> None:
    db = _db(tmp_path)
    store = SqliteVectorStore(db)

    store.replace_frame_embeddings(_ASSET, _RUN, [_make_emb(0, 1.0, 0.0), _make_emb(1, 0.0, 1.0)])
    store.delete(_ASSET, _RUN)

    assert store.list_frame_embeddings(_ASSET, _RUN) == []


def test_delete_only_affects_target_run(tmp_path: Path) -> None:
    db = _db(tmp_path)
    store = SqliteVectorStore(db)

    other_run = "run-other"
    other_emb = FrameEmbedding(
        asset_id=_ASSET,
        analysis_run_id=other_run,
        frame=0,
        model=_MODEL,
        vector=_vec(0.0, 1.0),
    )
    store.replace_frame_embeddings(_ASSET, _RUN, [_make_emb(0, 1.0, 0.0)])
    store.replace_frame_embeddings(_ASSET, other_run, [other_emb])

    store.delete(_ASSET, _RUN)

    assert store.list_frame_embeddings(_ASSET, _RUN) == []
    assert len(store.list_frame_embeddings(_ASSET, other_run)) == 1


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_sqlite_vector_store_satisfies_protocol(tmp_path: Path) -> None:
    db = _db(tmp_path)
    store = SqliteVectorStore(db)
    assert isinstance(store, VectorStore)
