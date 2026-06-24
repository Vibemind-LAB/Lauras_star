"""S5b — /assets/{id}/shorts-candidates API: extract (enqueue) + list, 409 guard.

The extract endpoint enqueues a ``shorts.extract`` job; we drain it inline via the
app's runner (started with ``start_runner=False`` so nothing runs in the background),
then assert the GET lists the persisted, ranked candidates.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.jobs.runner import JobRunner
from laura.main import create_app

_TOKEN = "test-token"
_H = {"X-Laura-Token": _TOKEN}
_WORD_FRAMES = 15


def _app(tmp_path: Path) -> tuple[TestClient, SqliteDatabase, JobRunner]:
    settings = Settings(workspace_root=tmp_path / "ws", token=_TOKEN, start_runner=False)
    app = create_app(settings)
    db: SqliteDatabase = app.state.db
    runner: JobRunner = app.state.runner
    return TestClient(app), db, runner


def _drain(runner: JobRunner) -> None:
    for _ in range(50):
        if not runner.run_once():
            break


def _seed_asset(db: SqliteDatabase, *, succeeded: bool) -> str:
    """Project + 30 fps asset (+ a succeeded run with transcript when ``succeeded``)."""
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="a",
        source_path="/tmp/a.mp4",
    )
    with db.transaction() as conn:
        conn.execute(
            "UPDATE media_assets SET rate_num=30, rate_den=1, duration_frames=? WHERE id=?",
            (30 * _WORD_FRAMES, asset["id"]),
        )
    run = repos.create_analysis_run(
        db, asset_id=asset["id"], pipeline_version="t", config={}
    )
    repos.start_analysis_run(db, run["id"])
    if not succeeded:
        return asset["id"]

    spk = repos.insert_speaker(db, asset_id=asset["id"], run_id=run["id"], label="A")
    words: list[dict[str, Any]] = []
    for i in range(30):
        sf = i * _WORD_FRAMES
        ef = sf + _WORD_FRAMES
        ends = (i + 1) % 5 == 0
        words.append(
            {
                "idx": i,
                "start_sample": sf * 1600,
                "end_sample": ef * 1600,
                "start_frame": sf,
                "end_frame": ef,
                "text": f"word{i}." if ends else f"word{i}",
                "confidence": 1.0,
                "is_punctuation": False,
            }
        )
    repos.insert_segment_with_words(
        db,
        asset_id=asset["id"],
        run_id=run["id"],
        speaker_id=spk,
        segment={
            "start_sample": 0,
            "end_sample": 30 * _WORD_FRAMES * 1600,
            "start_frame": 0,
            "end_frame": 30 * _WORD_FRAMES,
            "text": "a",
            "confidence": 1.0,
        },
        words=words,
    )
    repos.finish_analysis_run(db, run["id"], status="succeeded", diagnostics={})
    return asset["id"]


def test_extract_then_list(tmp_path: Path) -> None:
    client, db, runner = _app(tmp_path)
    asset_id = _seed_asset(db, succeeded=True)

    r = client.post(
        f"/assets/{asset_id}/shorts-candidates:extract",
        json={"min_duration_s": 1.0, "max_duration_s": 8.0},
        headers=_H,
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["job_id"]
    assert body["analysis_run_id"]

    _drain(runner)

    listed = client.get(f"/assets/{asset_id}/shorts-candidates", headers=_H)
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert len(rows) > 0
    # Ranked best-first by order_index; flattening contract holds in the response model.
    assert [row["order_index"] for row in rows] == sorted(row["order_index"] for row in rows)
    scores = [row["score"] for row in rows]
    assert scores == sorted(scores, reverse=True)
    first = rows[0]
    assert first["asset_id"] == asset_id
    assert first["rejected"] is False
    assert isinstance(first["qa_issues"], list)
    assert first["end_frame_exclusive"] > first["start_frame"]


def test_extract_without_succeeded_run_is_409(tmp_path: Path) -> None:
    client, db, _runner = _app(tmp_path)
    asset_id = _seed_asset(db, succeeded=False)

    r = client.post(
        f"/assets/{asset_id}/shorts-candidates:extract",
        json={},
        headers=_H,
    )
    assert r.status_code == 409, r.text


def test_extract_unknown_asset_is_404(tmp_path: Path) -> None:
    client, _db, _runner = _app(tmp_path)
    r = client.post(
        "/assets/does-not-exist/shorts-candidates:extract",
        json={},
        headers=_H,
    )
    assert r.status_code == 404, r.text


def test_list_empty_for_asset_without_candidates(tmp_path: Path) -> None:
    client, db, _runner = _app(tmp_path)
    asset_id = _seed_asset(db, succeeded=True)
    r = client.get(f"/assets/{asset_id}/shorts-candidates", headers=_H)
    assert r.status_code == 200, r.text
    assert r.json() == []
