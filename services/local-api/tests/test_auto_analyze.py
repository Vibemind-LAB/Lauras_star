"""Smart handling: auto-start analysis after import completes.

After the import chain finishes (proxy + audio + waveform), ``handle_waveform`` calls
``_maybe_auto_analyze`` so the user does not have to click "Analysieren". Opt out via
``LAURA_AUTO_ANALYZE=0``. Idempotent: never enqueues a second run for the same asset.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from laura.db import repos
from laura.db.database import Database
from laura.ingest import handlers
from laura.jobs.runner import JobContext


def _seed(db: Database) -> str:
    """Create a project + asset and return the asset id."""
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
    return str(asset["id"])


def _ctx(db: Database) -> JobContext:
    return JobContext(
        job_id="j1", kind="waveform.build", queue="proxy.cpu", payload={}, db=db
    )


def _analysis_jobs(db: Database) -> list[dict[str, Any]]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE kind='analysis.run'"
        ).fetchall()
        return [dict(r) for r in rows]


def test_auto_analyze_enqueues_when_enabled(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LAURA_AUTO_ANALYZE", raising=False)
    asset_id = _seed(db)

    job_id = handlers._maybe_auto_analyze(_ctx(db), asset_id)

    assert job_id is not None
    assert repos.get_latest_analysis_run(db, asset_id) is not None
    jobs = _analysis_jobs(db)
    assert len(jobs) == 1
    assert jobs[0]["max_attempts"] == 2
    # Regression: model must be a real size, not None — config.get("model", "base") returns
    # None for a present-but-None key, and WhisperModel(None) crashes (stat(None)). Caught live.
    payload = json.loads(jobs[0]["payload_json"])
    assert payload["config"]["model"] == "base"
    assert payload["config"]["stages"]["asr"] is True


def test_auto_analyze_skips_when_disabled(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAURA_AUTO_ANALYZE", "0")
    asset_id = _seed(db)

    job_id = handlers._maybe_auto_analyze(_ctx(db), asset_id)

    assert job_id is None
    assert repos.get_latest_analysis_run(db, asset_id) is None
    assert _analysis_jobs(db) == []


def test_auto_analyze_idempotent(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LAURA_AUTO_ANALYZE", raising=False)
    asset_id = _seed(db)
    # An analysis run already exists -> the helper must skip.
    repos.create_analysis_run(db, asset_id=asset_id, pipeline_version="t", config={})

    job_id = handlers._maybe_auto_analyze(_ctx(db), asset_id)

    assert job_id is None
    # No second run created.
    with db.connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM analysis_runs WHERE asset_id=?", (asset_id,)
        ).fetchone()["c"]
    assert count == 1
    # No analysis.run job enqueued.
    assert _analysis_jobs(db) == []
