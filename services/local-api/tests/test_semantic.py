"""Qdrant + fastembed semantic transcript search (optional ``[semantic]`` extra).

Uses an in-memory Qdrant so no server is needed; fastembed downloads a small CPU model
on first run. Skips when the extra is absent (e.g. CI without it).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastembed")
pytest.importorskip("qdrant_client")

from fastapi.testclient import TestClient  # noqa: E402

from laura.api import search as search_api  # noqa: E402
from laura.config import Settings  # noqa: E402
from laura.db.database import SqliteDatabase  # noqa: E402
from laura.main import create_app  # noqa: E402
from laura.semantic import SemanticIndex, get_index  # noqa: E402
from laura.util import new_id  # noqa: E402


def test_semantic_search_ranks_and_filters() -> None:
    idx = SemanticIndex(":memory:")
    s1, s2, s3 = new_id(), new_id(), new_id()
    idx.index([
        {"id": s1, "text": "the audio is completely out of sync on the interview clip",
         "payload": {"project_id": "p1", "asset_id": "a1", "segment_id": s1}},
        {"id": s2, "text": "lighting setup for the dramatic night scene on the rooftop",
         "payload": {"project_id": "p1", "asset_id": "a1", "segment_id": s2}},
        {"id": s3, "text": "the audio is completely out of sync on the interview clip",
         "payload": {"project_id": "p2", "asset_id": "a2", "segment_id": s3}},
    ])

    hits = idx.query("sound synchronization problem in the recording", project_id="p1", limit=5)
    assert hits, "expected semantic hits"
    assert hits[0]["segment_id"] == s1                 # audio-sync segment ranks first
    assert all(h["project_id"] == "p1" for h in hits)  # project filter excludes p2/s3


def test_delete_asset_removes_points() -> None:
    idx = SemanticIndex(":memory:")
    sid = new_id()
    idx.index([{"id": sid, "text": "some spoken words about editing the rough cut",
                "payload": {"project_id": "p", "asset_id": "ax", "segment_id": sid}}])
    assert idx.query("editing", project_id="p", limit=5)
    idx.delete_asset("ax")
    assert idx.query("editing", project_id="p", limit=5) == []


def test_search_endpoint_semantic_mode(tmp_path: Path) -> None:
    settings = Settings(workspace_root=tmp_path, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    client = TestClient(create_app(settings))
    client.__enter__()
    try:
        pid = client.post(
            "/projects", json={"name": "P", "sequence_rate_num": 30, "sequence_rate_den": 1}
        ).json()["id"]
        index = get_index()
        assert index is not None
        s1, s2 = new_id(), new_id()
        index.index([
            {"id": s1, "text": "the camera operator framed the wide establishing shot",
             "payload": {"project_id": pid, "asset_id": "a1", "segment_id": s1,
                         "asset_name": "a.mov", "text": "wide establishing shot",
                         "start_frame": 0, "end_frame": 30, "speaker_label": None}},
            {"id": s2, "text": "we really should fix the loud background noise in the audio",
             "payload": {"project_id": pid, "asset_id": "a1", "segment_id": s2,
                         "asset_name": "a.mov", "text": "loud background noise",
                         "start_frame": 30, "end_frame": 60, "speaker_label": None}},
        ])

        resp = client.post(
            "/search",
            json={"project_id": pid, "query": "noisy hiss in the sound recording",
                  "mode": "semantic"},
        )
        assert resp.status_code == 200, resp.text
        hits = resp.json()
        assert hits and hits[0]["segment_id"] == s2  # noise segment ranks first
        assert hits[0]["score"] is not None
    finally:
        client.__exit__(None, None, None)


def test_search_endpoint_semantic_mode_falls_back_when_index_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_index() itself can raise (e.g. a down Qdrant server refuses the connection during
    client/collection construction) — mode=semantic must degrade to lexical results, not 500."""

    def _raise() -> None:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(search_api, "get_index", _raise)

    settings = Settings(workspace_root=tmp_path, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    client = TestClient(create_app(settings))
    client.__enter__()
    try:
        pid = client.post(
            "/projects", json={"name": "P", "sequence_rate_num": 30, "sequence_rate_den": 1}
        ).json()["id"]

        resp = client.post(
            "/search",
            json={"project_id": pid, "query": "hallo", "mode": "semantic"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == []  # lexical fallback: nothing indexed, but no 500
    finally:
        client.__exit__(None, None, None)
