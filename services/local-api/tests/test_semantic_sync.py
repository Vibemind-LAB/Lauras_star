"""semantic_sync: per-segment re-index is best-effort and upserts the same item shape."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import laura.analysis.semantic_sync as sync
from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase


class _FakeIndex:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def index(self, items: list[dict[str, Any]]) -> int:
        self.items.extend(items)
        return len(items)


def _db(tmp_path: Path) -> Database:
    settings = Settings(workspace_root=tmp_path, start_runner=False)
    database = SqliteDatabase(settings.db_path)
    database.migrate()
    return database


def _seed_one_segment(db: Database, *, text: str) -> tuple[str, str]:
    """(asset_id, segment_id) — project + asset + transcript run + one segment.

    Seed sequence lifted verbatim from ``tests/conftest.py``'s ``seeded_timeline`` fixture
    (repos.create_project / create_asset / create_analysis_run / insert_segment_with_words),
    the source of truth for the transcript_segments column names.
    """
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="",
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a.mp4", source_path="a.mp4",
    )
    run = repos.create_analysis_run(
        db, asset_id=asset["id"], pipeline_version="test", config={},
    )
    seg_id = repos.insert_segment_with_words(
        db,
        asset_id=asset["id"],
        run_id=run["id"],
        speaker_id=None,
        segment={
            "start_sample": 0, "end_sample": 48_000,
            "start_frame": 0, "end_frame": 30,
            "text": text, "confidence": 1.0,
        },
        words=[],
    )
    return asset["id"], seg_id


def test_reindex_segments_upserts_edited_segment(tmp_path: Path, monkeypatch: Any) -> None:
    db = _db(tmp_path)
    aid, seg_id = _seed_one_segment(db, text="cloud code")
    fake = _FakeIndex()
    monkeypatch.setattr(sync, "get_index", lambda: fake)
    n = sync.reindex_segments(db, aid, [seg_id])
    assert n == 1
    assert fake.items[0]["payload"]["segment_id"] == seg_id
    assert fake.items[0]["payload"]["text"] == "cloud code"


def test_reindex_is_best_effort_when_index_raises(tmp_path: Path, monkeypatch: Any) -> None:
    db = _db(tmp_path)
    aid, seg_id = _seed_one_segment(db, text="x")

    def _boom() -> Any:
        raise RuntimeError("qdrant down")

    monkeypatch.setattr(sync, "get_index", _boom)
    assert sync.reindex_segments(db, aid, [seg_id]) == 0  # never raises


def test_reindex_returns_zero_when_no_segments_match(tmp_path: Path, monkeypatch: Any) -> None:
    db = _db(tmp_path)
    aid, _seg_id = _seed_one_segment(db, text="unrelated")
    fake = _FakeIndex()
    monkeypatch.setattr(sync, "get_index", lambda: fake)
    assert sync.reindex_segments(db, aid, ["does-not-exist"]) == 0
    assert fake.items == []


def test_reindex_returns_zero_for_empty_segment_ids(tmp_path: Path, monkeypatch: Any) -> None:
    db = _db(tmp_path)
    aid, _seg_id = _seed_one_segment(db, text="unrelated")
    fake = _FakeIndex()
    monkeypatch.setattr(sync, "get_index", lambda: fake)
    assert sync.reindex_segments(db, aid, []) == 0
    assert fake.items == []
