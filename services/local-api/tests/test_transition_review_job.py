"""Plan B / Task B7 — run_transition_review (enumerate -> cache -> review -> persist)."""

from __future__ import annotations

import json
from pathlib import Path

from laura.analysis.transition_review import StubVlmBackend, run_transition_review
from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase


def _db(tmp_path: Path) -> SqliteDatabase:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    return db


def _seed_3boundaries(db: SqliteDatabase) -> str:
    project = repos.create_project(
        db, name="P", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/ws"
    )
    a = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a.mov", source_path="/a.mov"
    )
    b = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="b.mov", source_path="/b.mov"
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")

    def add(asset: str, si: int, so: int, qi: int, qo: int) -> None:
        repos.add_timeline_clip(
            db,
            timeline_id=tl["id"],
            asset_id=asset,
            src_in_frame=si,
            src_out_frame_exclusive=so,
            seq_in_frame=qi,
            seq_out_frame_exclusive=qo,
        )

    add(a["id"], 0, 100, 0, 100)
    add(a["id"], 100, 160, 100, 160)  # boundary0: contiguous same-source -> jump_cut
    add(a["id"], 200, 260, 160, 220)  # boundary1: same asset, src gap
    add(b["id"], 0, 50, 220, 270)  # boundary2: distinct asset
    return str(tl["id"])


def _fake_extract(
    proxy_paths: dict[str, str], refs: list[tuple[str, int]], **kw: int
) -> list[bytes]:
    return [b"x"]


class _CountingStub(StubVlmBackend):
    def __init__(self) -> None:
        self.calls = 0

    def review(self, frames: list[bytes], meta: dict[str, object]):  # type: ignore[override]
        self.calls += 1
        return StubVlmBackend.review(self, frames, meta)


def test_run_review_persists_verdicts(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tl_id = _seed_3boundaries(db)
    backend = _CountingStub()
    result = run_transition_review(db, tl_id, backend=backend, frame_extractor=_fake_extract)
    assert result == {"total": 3, "reviewed": 3, "inferences": 3}
    assert backend.calls == 3
    reviews = repos.list_transition_reviews(db, tl_id)
    assert len(reviews) == 3
    jc = next(rv for rv in reviews if rv["label"] == "jump_cut")
    fix = json.loads(jc["suggested_fix_json"])
    assert fix["kind"] == "transition" and fix["transition_style"] == "crossfade"


def test_run_review_second_run_is_all_cache(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tl_id = _seed_3boundaries(db)
    backend = _CountingStub()
    run_transition_review(db, tl_id, backend=backend, frame_extractor=_fake_extract)
    assert backend.calls == 3
    again = run_transition_review(db, tl_id, backend=backend, frame_extractor=_fake_extract)
    assert again["inferences"] == 0 and backend.calls == 3  # no new model calls


def test_run_review_reports_progress(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tl_id = _seed_3boundaries(db)
    seen: list[tuple[int, int]] = []
    run_transition_review(
        db,
        tl_id,
        backend=StubVlmBackend(),
        frame_extractor=_fake_extract,
        progress=lambda done, total: seen.append((done, total)),
    )
    assert seen[-1] == (3, 3) and len(seen) == 3


def test_run_review_empty_timeline(tmp_path: Path) -> None:
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="P", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/ws"
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    result = run_transition_review(
        db, str(tl["id"]), backend=StubVlmBackend(), frame_extractor=_fake_extract
    )
    assert result == {"total": 0, "reviewed": 0, "inferences": 0}
