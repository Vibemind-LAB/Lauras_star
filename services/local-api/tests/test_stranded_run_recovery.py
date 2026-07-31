"""analysis_runs gets what the jobs table always had: a reaper, plus a sweep for the corpses.

handle_analysis_run's try/except covers a handler that RAISES. A SIGKILLed worker runs no
Python at all, and the runtime cap in jobs/runner.py stops the heartbeat while the handler
thread is still alive and has raised nothing -- both leave the run on 'running' forever, with
its segments already committed and diagnostics_json still '{}'.
"""

from __future__ import annotations

import json
from pathlib import Path

from laura.db import repos
from laura.db.database import Database
from laura.util import utcnow_iso

PAST = "2000-01-01T00:00:00.000000Z"


def _seed_run(
    db: Database, tmp_path: Path, *, status: str = "running"
) -> tuple[str, str]:
    """(asset_id, run_id) for a fresh project/asset, with the run forced to *status*."""
    project = repos.create_project(
        db, name="p", rate_num=25, rate_den=1, drop_frame=False,
        workspace_root=str(tmp_path / "ws"),
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a.mp4",
        source_path=str(tmp_path / "a.mp4"),
    )
    run = repos.create_analysis_run(
        db, asset_id=str(asset["id"]), pipeline_version="t", config={}
    )
    with db.connection() as conn:
        conn.execute(
            "UPDATE analysis_runs SET status=?, started_at=? WHERE id=?",
            (status, utcnow_iso(), run["id"]),
        )
    return str(asset["id"]), str(run["id"])


def test_fail_stranded_analysis_run_finalizes_a_running_row(
    db: Database, tmp_path: Path
) -> None:
    _asset_id, run_id = _seed_run(db, tmp_path)

    wrote = repos.fail_stranded_analysis_run(
        db, run_id, error="worker never came back", recovered_by="test"
    )

    assert wrote is True
    run = repos.get_analysis_run(db, run_id)
    assert run is not None
    assert run["status"] == "failed"
    assert run["finished_at"] is not None
    diagnostics = json.loads(run["diagnostics_json"] or "{}")
    assert diagnostics == {"error": "worker never came back", "recovered_by": "test"}


def test_fail_stranded_analysis_run_leaves_a_finished_row_alone(
    db: Database, tmp_path: Path
) -> None:
    """The handler always wins over the recovery paths: a run that finalized itself between
    the reaper's read and its write must not be overwritten."""
    _asset_id, run_id = _seed_run(db, tmp_path, status="running")
    repos.finish_analysis_run(db, run_id, status="succeeded", diagnostics={"scene": "ok"})

    wrote = repos.fail_stranded_analysis_run(
        db, run_id, error="too late", recovered_by="test"
    )

    assert wrote is False
    run = repos.get_analysis_run(db, run_id)
    assert run is not None
    assert run["status"] == "succeeded"
    assert json.loads(run["diagnostics_json"] or "{}") == {"scene": "ok"}
