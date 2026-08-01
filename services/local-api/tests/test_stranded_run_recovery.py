"""analysis_runs gets what the jobs table always had: a reaper, plus a sweep for the corpses.

handle_analysis_run's try/except covers a handler that RAISES. A SIGKILLed worker runs no
Python at all, and the runtime cap in jobs/runner.py stops the heartbeat while the handler
thread is still alive and has raised nothing -- both leave the run on 'running' forever, with
its segments already committed and diagnostics_json still '{}'.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from laura.analysis.recovery import recover_stranded_analysis_runs
from laura.config import Settings
from laura.db import repos
from laura.db.database import Database
from laura.jobs.queues import queue_for
from laura.jobs.runner import JobRunner, enqueue
from laura.main import create_app
from laura.mcp.tools import tool_extract_shorts
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


def _expired_job(
    db: Database,
    run_id: str,
    *,
    attempt: int,
    max_attempts: int = 2,
    kind: str = "analysis.run",
) -> str:
    """A job whose lease ran out at *attempt* of *max_attempts*, owning *run_id*."""
    job_id = enqueue(
        db,
        queue=queue_for("analysis.run"),
        kind=kind,
        payload={"asset_id": "asset-x", "analysis_run_id": run_id},
        max_attempts=max_attempts,
    )
    with db.connection() as conn:
        conn.execute(
            "UPDATE jobs SET status='running', attempt=?, lease_expires_at=? WHERE id=?",
            (attempt, PAST, job_id),
        )
    return job_id


def test_reaper_fails_the_analysis_run_when_attempts_are_exhausted(
    db: Database, tmp_path: Path
) -> None:
    """The SIGKILL path: no Python ran, the lease simply expired. The jobs row is reaped --
    the analysis_runs row has to be reaped with it."""
    _asset_id, run_id = _seed_run(db, tmp_path)
    job_id = _expired_job(db, run_id, attempt=2, max_attempts=2)

    assert JobRunner(db, {}).reap_expired() == 1

    job = repos.get_job(db, job_id)
    assert job is not None and job["status"] == "failed"
    run = repos.get_analysis_run(db, run_id)
    assert run is not None
    assert run["status"] == "failed"
    diagnostics = json.loads(run["diagnostics_json"] or "{}")
    assert diagnostics["recovered_by"] == "job reaper"
    assert job_id in diagnostics["error"]


def test_reaper_leaves_the_run_running_when_the_job_is_requeued(
    db: Database, tmp_path: Path
) -> None:
    """A requeued job runs again and re-stamps the row through start_analysis_run. Writing a
    terminal status here would fight the retry -- and, under the runtime cap, the zombie
    handler thread that is still alive."""
    _asset_id, run_id = _seed_run(db, tmp_path)
    job_id = _expired_job(db, run_id, attempt=1, max_attempts=2)

    assert JobRunner(db, {}).reap_expired() == 1

    job = repos.get_job(db, job_id)
    assert job is not None and job["status"] == "queued"
    run = repos.get_analysis_run(db, run_id)
    assert run is not None and run["status"] == "running"


def test_reaper_ignores_jobs_of_other_kinds(db: Database, tmp_path: Path) -> None:
    """Only analysis.run owns an analysis_runs row. A same-shaped payload on another kind
    must not reach into that table."""
    _asset_id, run_id = _seed_run(db, tmp_path)
    _expired_job(db, run_id, attempt=1, max_attempts=1, kind="echo")

    assert JobRunner(db, {}).reap_expired() == 1

    run = repos.get_analysis_run(db, run_id)
    assert run is not None and run["status"] == "running"


def test_reaper_does_not_overwrite_an_already_finished_run(
    db: Database, tmp_path: Path
) -> None:
    _asset_id, run_id = _seed_run(db, tmp_path, status="succeeded")
    _expired_job(db, run_id, attempt=2, max_attempts=2)

    JobRunner(db, {}).reap_expired()

    run = repos.get_analysis_run(db, run_id)
    assert run is not None and run["status"] == "succeeded"


def _job_for_run(db: Database, run_id: str, *, status: str) -> str:
    """An analysis.run job owning *run_id*, forced to *status*."""
    job_id = enqueue(
        db,
        queue=queue_for("analysis.run"),
        kind="analysis.run",
        payload={"asset_id": "asset-x", "analysis_run_id": run_id},
    )
    with db.connection() as conn:
        conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
    return job_id


def test_sweep_finalizes_a_run_whose_job_already_failed(
    db: Database, tmp_path: Path
) -> None:
    """The live shape in workspace-livetest: the job carries a real error, the run says
    'running' and always will -- the reaper never revisits a job it already failed."""
    _asset_id, run_id = _seed_run(db, tmp_path)
    job_id = _job_for_run(db, run_id, status="failed")

    assert recover_stranded_analysis_runs(db) == [run_id]

    run = repos.get_analysis_run(db, run_id)
    assert run is not None and run["status"] == "failed"
    diagnostics = json.loads(run["diagnostics_json"] or "{}")
    assert diagnostics["recovered_by"] == "startup sweep"
    assert job_id in diagnostics["error"]


@pytest.mark.parametrize("job_status", ["succeeded", "failed", "canceled", "cancelled"])
def test_sweep_finalizes_a_run_for_every_terminal_job_status(
    db: Database, tmp_path: Path, job_status: str
) -> None:
    """_TERMINAL_JOB_STATUS must accept every spelling that actually lands in jobs.status.

    repos.cancel_job writes 'cancelled' (two Ls); the schema comment on the column says
    'canceled' (one L). A frozenset with only one of those spellings leaves the run of a job
    that ended in the other stranded on 'queued'/'running' forever -- the sweep's else branch
    treats an unrecognised status as "a live job still owns it" and skips it for good."""
    _asset_id, run_id = _seed_run(db, tmp_path)
    job_id = _job_for_run(db, run_id, status=job_status)

    assert recover_stranded_analysis_runs(db) == [run_id]

    run = repos.get_analysis_run(db, run_id)
    assert run is not None and run["status"] == "failed"
    diagnostics = json.loads(run["diagnostics_json"] or "{}")
    assert diagnostics["recovered_by"] == "startup sweep"
    assert job_id in diagnostics["error"]


def test_sweep_finalizes_a_run_without_any_job(db: Database, tmp_path: Path) -> None:
    """enqueue() deletes a failed job row when its idempotency key is reused, and a throw
    between create_analysis_run and enqueue leaves a run with no job at all."""
    _asset_id, run_id = _seed_run(db, tmp_path, status="queued")

    assert recover_stranded_analysis_runs(db) == [run_id]

    run = repos.get_analysis_run(db, run_id)
    assert run is not None and run["status"] == "failed"


def test_sweep_leaves_a_run_with_a_live_job_alone(db: Database, tmp_path: Path) -> None:
    """After a SIGKILL restart the job is still 'running' with a stale lease. That belongs to
    the reaper (which requeues it seconds later), not to the sweep."""
    _asset_id, queued_run = _seed_run(db, tmp_path, status="queued")
    _job_for_run(db, queued_run, status="queued")
    _asset_id2, running_run = _seed_run(db, tmp_path, status="running")
    _job_for_run(db, running_run, status="running")

    assert recover_stranded_analysis_runs(db) == []

    for run_id, expected in ((queued_run, "queued"), (running_run, "running")):
        run = repos.get_analysis_run(db, run_id)
        assert run is not None and run["status"] == expected


def test_sweep_is_idempotent(db: Database, tmp_path: Path) -> None:
    _asset_id, run_id = _seed_run(db, tmp_path)
    _job_for_run(db, run_id, status="failed")

    assert recover_stranded_analysis_runs(db) == [run_id]
    assert recover_stranded_analysis_runs(db) == []


def test_startup_runs_the_sweep(tmp_path: Path) -> None:
    settings = Settings(workspace_root=tmp_path / "ws", token=None, start_runner=False)
    app = create_app(settings)
    db: Database = app.state.db
    _asset_id, run_id = _seed_run(db, tmp_path)
    _job_for_run(db, run_id, status="failed")

    with TestClient(app):  # entering the context runs the lifespan
        pass

    run = repos.get_analysis_run(db, run_id)
    assert run is not None and run["status"] == "failed"


def test_finalized_run_keeps_its_transcript_reachable(
    db: Database, tmp_path: Path
) -> None:
    """The livetest shape: the corpse carries the ONLY transcript, and a newer scene-only run
    succeeded with zero segments. get_latest_transcript_run ranks succeeded first, but the
    EXISTS clause drops segment-less runs before that -- so making the corpse honest must not
    move the resolver."""
    asset_id, corpse = _seed_run(db, tmp_path)
    repos.insert_segment_with_words(
        db,
        asset_id=asset_id,
        run_id=corpse,
        speaker_id=None,
        segment={
            "start_sample": 0, "end_sample": 16000,
            "start_frame": 0, "end_frame": 25,
            "text": "the only transcript this asset has",
        },
        words=[],
    )
    newer = repos.create_analysis_run(
        db, asset_id=asset_id, pipeline_version="t", config={}
    )
    repos.finish_analysis_run(db, str(newer["id"]), status="succeeded", diagnostics={})
    _job_for_run(db, corpse, status="failed")

    assert recover_stranded_analysis_runs(db) == [corpse]

    resolved = repos.get_latest_transcript_run(db, asset_id)
    assert resolved is not None and resolved["id"] == corpse


def test_restart_clears_the_previous_attempts_diagnostics(
    db: Database, tmp_path: Path
) -> None:
    """analysis.run retries once (max_attempts=2). The second attempt must not present the
    first one's finished_at and error to the UI while it is still running."""
    _asset_id, run_id = _seed_run(db, tmp_path)
    repos.finish_analysis_run(
        db, run_id, status="failed", diagnostics={"error": "attempt 1 died"}
    )

    repos.start_analysis_run(db, run_id)

    run = repos.get_analysis_run(db, run_id)
    assert run is not None
    assert run["status"] == "running"
    assert run["started_at"] is not None
    assert run["finished_at"] is None
    assert json.loads(run["diagnostics_json"] or "{}") == {}


def _asset_with_a_corpse_on_top(db: Database, tmp_path: Path) -> tuple[str, str]:
    """(asset_id, succeeded_run_id) — a good run shadowed by a newer failed one."""
    asset_id, good = _seed_run(db, tmp_path, status="succeeded")
    corpse = repos.create_analysis_run(
        db, asset_id=asset_id, pipeline_version="t", config={}
    )
    repos.start_analysis_run(db, str(corpse["id"]))
    wrote = repos.fail_stranded_analysis_run(
        db, str(corpse["id"]), error="worker died", recovered_by="test"
    )
    assert wrote is True  # pin the fixture: a silently-failing write must fail this helper too
    return asset_id, good


def test_latest_succeeded_run_ignores_a_newer_failed_run(
    db: Database, tmp_path: Path
) -> None:
    asset_id, good = _asset_with_a_corpse_on_top(db, tmp_path)

    resolved = repos.get_latest_succeeded_analysis_run(db, asset_id)

    assert resolved is not None and resolved["id"] == good


def test_latest_succeeded_run_is_none_without_one(db: Database, tmp_path: Path) -> None:
    asset_id, _run_id = _seed_run(db, tmp_path, status="running")

    assert repos.get_latest_succeeded_analysis_run(db, asset_id) is None


def test_extract_shorts_tool_uses_the_latest_succeeded_run(
    db: Database, tmp_path: Path
) -> None:
    """A newer corpse used to lock the asset out of shorts extraction for good."""
    asset_id, good = _asset_with_a_corpse_on_top(db, tmp_path)

    result = tool_extract_shorts(db, asset_id)

    assert result["ok"] is True
    assert result["analysis_run_id"] == good


def test_shorts_candidates_accepts_an_asset_whose_newest_run_failed(
    tmp_path: Path,
) -> None:
    settings = Settings(workspace_root=tmp_path / "ws", token=None, start_runner=False)
    app = create_app(settings)
    db: Database = app.state.db
    asset_id, good = _asset_with_a_corpse_on_top(db, tmp_path)

    with TestClient(app) as client:
        res = client.post(f"/assets/{asset_id}/shorts-candidates:extract", json={})

    assert res.status_code == 202
    assert res.json()["analysis_run_id"] == good


def _seed_kept_shots(db: Database, *, asset_id: str, run_id: str) -> None:
    """Two contiguous kept shots on *run_id* -- the corpse in
    _asset_with_a_corpse_on_top seeds none, so a build that reaches for the corpse's
    shots instead of the succeeded run's comes back empty."""
    repos.insert_shots(
        db,
        asset_id=asset_id,
        run_id=run_id,
        shots=[
            {"src_in_frame": 0, "src_out_frame_exclusive": 50, "keep": True},
            {"src_in_frame": 50, "src_out_frame_exclusive": 100, "keep": True},
        ],
    )


def test_scene_generation_accepts_an_asset_whose_newest_run_failed(
    tmp_path: Path,
) -> None:
    """A newer corpse used to make scenes:generate 422 with 'no kept shots to build a
    rough cut from' even though the succeeded run underneath it already has kept shots."""
    settings = Settings(workspace_root=tmp_path / "ws", token=None, start_runner=False)
    app = create_app(settings)
    db: Database = app.state.db
    asset_id, good = _asset_with_a_corpse_on_top(db, tmp_path)
    _seed_kept_shots(db, asset_id=asset_id, run_id=good)
    asset = repos.get_asset(db, asset_id)
    assert asset is not None
    timeline = repos.create_timeline(
        db, project_id=asset["project_id"], name="Rough Cut", kind="rough_cut"
    )

    with TestClient(app) as client:
        res = client.post(
            f"/timelines/{timeline['id']}/scenes:generate",
            json={"asset_id": asset_id},
        )

    assert res.status_code == 200
    scenes = res.json()
    # Non-empty, and tiling the full 0..100 range, is only possible if the clips were
    # built from the succeeded run's shots -- the corpse has none, so resolving to it
    # would have 422'd before any scene existed.
    assert scenes
    assert scenes[0]["seq_in_frame"] == 0
    assert scenes[-1]["seq_out_frame_exclusive"] == 100


def test_timeline_from_shots_accepts_an_asset_whose_newest_run_failed(
    tmp_path: Path,
) -> None:
    """A newer corpse used to make from-shots build an empty timeline even though the
    succeeded run underneath it already has kept shots to build from."""
    settings = Settings(workspace_root=tmp_path / "ws", token=None, start_runner=False)
    app = create_app(settings)
    db: Database = app.state.db
    asset_id, good = _asset_with_a_corpse_on_top(db, tmp_path)
    _seed_kept_shots(db, asset_id=asset_id, run_id=good)
    asset = repos.get_asset(db, asset_id)
    assert asset is not None

    with TestClient(app) as client:
        res = client.post(
            f"/projects/{asset['project_id']}/timelines/from-shots",
            json={"asset_id": asset_id, "align_editorial": False},
        )

    assert res.status_code == 201
    clips = res.json()["timeline"]["clips"]
    # The corpse has no shots, so an empty (or partial) result here would mean the
    # build reached for the corpse's shots instead of the succeeded run's.
    assert [(c["src_in_frame"], c["src_out_frame_exclusive"]) for c in clips] == [
        (0, 50),
        (50, 100),
    ]


def test_shots_run_resolves_an_unfinished_run_that_has_shots(
    db: Database, tmp_path: Path
) -> None:
    """get_latest_shots_run answers "which run holds the shots I'm about to read", not the
    succeeded-gate question -- a single run left 'queued' (never finished) that already wrote
    its shots must still resolve. This is the property the reverted fe04544 attempt got wrong:
    switching the two build-from-shots call sites to get_latest_succeeded_analysis_run broke
    15 pre-existing tests that seed exactly this shape (a queued run with shots)."""
    asset_id, run_id = _seed_run(db, tmp_path, status="queued")
    _seed_kept_shots(db, asset_id=asset_id, run_id=run_id)

    resolved = repos.get_latest_shots_run(db, asset_id)

    assert resolved is not None and resolved["id"] == run_id
