# Analysis-Run-Reaper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An `analysis_runs` row always reaches a terminal state — even when no Python runs
(SIGKILL) or the handler thread never raises (runtime cap) — and a corpse can no longer
block shorts extraction or visual embedding.

**Architecture:** Three layers. (1) `JobRunner.reap_expired` finalizes the matching
`analysis_runs` row when it fails an `analysis.run` job, reading `analysis_run_id` from the
job payload. (2) A startup sweep heals rows already stranded, touching only runs whose job is
terminal or gone. (3) A `get_latest_succeeded_analysis_run` resolver moves the five status
gates from "the newest run, and it had better be succeeded" to "the newest run that
succeeded". Plus one hygiene fix: `start_analysis_run` clears the previous attempt's
`finished_at`/`diagnostics_json`.

**Tech Stack:** Python 3.11+, `uv`, FastAPI, SQLite (Postgres-compatible SQL), pytest, mypy
(strict, bare run — `tests/` included), ruff.

**Spec:** [`docs/superpowers/specs/2026-07-31-analysis-run-reaper-design.md`](../specs/2026-07-31-analysis-run-reaper-design.md)

## Global Constraints

- All commands run from `services/local-api`.
- Python 3.11+, managed with `uv`. Strict mypy — CI runs bare `uv run mypy`, which type-checks
  `tests/` too. `ruff` for lint/format.
- No `print` in committed code — use the module logger (`logger = logging.getLogger(__name__)`).
- SQL must work on SQLite **and** Postgres: `?` placeholders, no `json_extract`, no SQLite-only
  functions.
- Conventional Commits, English. Feature branch `fix/analysis-run-reaper` (already checked out,
  branched from `claude/vibrant-kirch-9beaea`). Never commit to `main`.
- `git add <explicit paths>` — never `git add -A` (a parallel session works this tree).
- Timeline invariants are untouched by this plan: no frame/sample arithmetic changes.

---

### Task 1: `repos.fail_stranded_analysis_run` — the shared write

Both the reaper (Task 2) and the sweep (Task 3) need the same guarded write. It lives in the
repo layer once.

**Files:**
- Modify: `src/laura/db/repos.py` (insert after `finish_analysis_run`, ~line 460)
- Test: `tests/test_stranded_run_recovery.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `repos.fail_stranded_analysis_run(db: Database, run_id: str, *, error: str,
  recovered_by: str) -> bool` — writes `status='failed'`, `finished_at=<now>`,
  `diagnostics_json={"error": error, "recovered_by": recovered_by}`, but **only** when the row
  is still `'queued'`/`'running'`. Returns whether it wrote.
- Produces (test helpers reused by Tasks 2–5): `_seed_run`, `PAST`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_stranded_run_recovery.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_stranded_run_recovery.py -v
```

Expected: FAIL — `AttributeError: module 'laura.db.repos' has no attribute 'fail_stranded_analysis_run'`.

- [ ] **Step 3: Implement**

In `src/laura/db/repos.py`, directly below `finish_analysis_run`:

```python
def fail_stranded_analysis_run(
    db: Database, run_id: str, *, error: str, recovered_by: str
) -> bool:
    """Finalize a run whose worker never came back. True when this call wrote the row.

    handle_analysis_run's try/except cannot reach a process that was SIGKILLed, and the
    runtime cap in jobs/runner.py stops the heartbeat while the handler thread is still alive
    and has raised nothing. Both leave analysis_runs on 'running' forever. The status guard
    makes this a no-op once the run reached a terminal state on its own -- the handler always
    wins over the recovery paths.
    """
    diagnostics = json.dumps({"error": error, "recovered_by": recovered_by})
    with db.transaction() as conn:
        cur = conn.execute(
            "UPDATE analysis_runs SET status='failed', finished_at=?, diagnostics_json=? "
            "WHERE id=? AND status IN ('queued','running')",
            (utcnow_iso(), diagnostics, run_id),
        )
        return int(cur.rowcount) == 1
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_stranded_run_recovery.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/laura/db/repos.py tests/test_stranded_run_recovery.py && git commit -m "feat(db): a stranded analysis run can be finalized without its handler"
```

---

### Task 2: The reaper finalizes the run it just gave up on

**Files:**
- Modify: `src/laura/jobs/runner.py:180-198` (`reap_expired`) + new module-level helpers
- Test: `tests/test_stranded_run_recovery.py` (append)

**Interfaces:**
- Consumes: `repos.fail_stranded_analysis_run` (Task 1), `_seed_run`, `PAST`.
- Produces: `laura.jobs.runner.ANALYSIS_RUN_KIND: str` (`"analysis.run"`) and
  `laura.jobs.runner.analysis_run_id_from_payload(payload_json: str | None) -> str | None` —
  both used by Task 3.
- `reap_expired() -> int` keeps its meaning: **jobs** touched, not runs.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_stranded_run_recovery.py` (and extend the imports at the top with
`from laura.jobs.queues import queue_for` and `from laura.jobs.runner import JobRunner, enqueue`):

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_stranded_run_recovery.py -k reaper -v
```

Expected: `test_reaper_fails_the_analysis_run_when_attempts_are_exhausted` FAILS with
`assert 'running' == 'failed'`. The other three already pass (they assert the *unchanged*
behaviour) — they are the regression guards for this task.

- [ ] **Step 3: Implement**

In `src/laura/jobs/runner.py`, add to the imports:

```python
from ..db import repos
```

(`db.repos` imports only `util` and `db.database`, so there is no cycle.)

Add below the `JobHandler` type alias, near the top of the module:

```python
# The one job kind that owns a row in another table (analysis_runs). jobs/queues.py already
# names every kind for routing; a finalizer registry would be more architecture than a single
# kind earns.
ANALYSIS_RUN_KIND = "analysis.run"

# One predicate for the expired-lease rows, shared by the SELECT that classifies them and the
# two UPDATEs that act on them -- so the classification can never drift from the action.
_EXPIRED_LEASE = (
    "status IN ('leased','running') AND lease_expires_at IS NOT NULL AND lease_expires_at < ?"
)


def analysis_run_id_from_payload(payload_json: str | None) -> str | None:
    """The analysis_runs row an ``analysis.run`` job owns, or None.

    analysis_runs has no job_id column; the link lives in the payload, which all three enqueue
    sites carry (api/analysis.py, ingest/handlers.py, mcp/tools.py). Parsed in Python rather
    than with json_extract so it works on SQLite and Postgres alike.
    """
    try:
        payload = json.loads(payload_json or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    run_id = payload.get("analysis_run_id")
    return str(run_id) if run_id else None
```

Replace `reap_expired` with:

```python
    def reap_expired(self) -> int:
        """Requeue or fail jobs whose lease expired. Returns count of jobs touched.

        An ``analysis.run`` job that runs out of attempts here also finalizes its
        analysis_runs row. The jobs table has always had this reaper; analysis_runs never did,
        so a SIGKILLed worker -- or the runtime cap below, which stops the heartbeat while the
        handler thread lives on and raises nothing -- left the run saying 'running' forever.
        A *requeued* job is deliberately left alone: the retry re-stamps the row through
        repos.start_analysis_run.
        """
        now = utcnow_iso()
        err = json.dumps({"error": "lease expired, max attempts reached"})
        stranded: list[tuple[str, str]] = []  # (job_id, analysis_run_id)
        with self.db.transaction(immediate=True) as conn:
            for row in conn.execute(
                f"SELECT id, kind, payload_json, attempt, max_attempts FROM jobs "
                f"WHERE {_EXPIRED_LEASE}",
                (now,),
            ).fetchall():
                if str(row["kind"]) != ANALYSIS_RUN_KIND:
                    continue
                if int(row["attempt"]) < int(row["max_attempts"]):
                    continue  # requeued below -- the retry re-stamps the run
                run_id = analysis_run_id_from_payload(row["payload_json"])
                if run_id is not None:
                    stranded.append((str(row["id"]), run_id))
            failed = conn.execute(
                f"UPDATE jobs SET status='failed', finished_at=?, updated_at=?, error_json=? "
                f"WHERE {_EXPIRED_LEASE} AND attempt >= max_attempts",
                (now, now, err, now),
            ).rowcount
            requeued = conn.execute(
                f"UPDATE jobs SET status='queued', worker_id=NULL, lease_expires_at=NULL, "
                f"updated_at=? WHERE {_EXPIRED_LEASE} AND attempt < max_attempts",
                (now, now),
            ).rowcount
        # Outside the transaction: a second write connection inside a held SQLite write lock
        # deadlocks. A crash in the gap leaves the run stranded -- which the startup sweep
        # (analysis/recovery.py) heals.
        for job_id, run_id in stranded:
            if repos.fail_stranded_analysis_run(
                self.db,
                run_id,
                error=f"job {job_id}: lease expired, max attempts reached",
                recovered_by="job reaper",
            ):
                logger.warning(
                    "analysis run %s finalized as failed: job %s ran out of attempts",
                    run_id, job_id,
                )
        return int(failed) + int(requeued)
```

Watch the parameter order: `_EXPIRED_LEASE` contributes exactly one `?` (the `lease_expires_at`
comparison), and it comes **after** the SET-clause parameters — hence `(now, now, err, now)`
and `(now, now)`, unchanged from before.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_stranded_run_recovery.py tests/test_job_runner.py tests/test_queue_routing.py tests/test_runner_concurrency.py -v
```

Expected: all pass — the three existing reaper tests are the proof that the refactored
predicate still behaves identically.

- [ ] **Step 5: Commit**

```bash
git add src/laura/jobs/runner.py tests/test_stranded_run_recovery.py && git commit -m "fix(jobs): the reaper finalizes the analysis run it just gave up on"
```

---

### Task 3: Startup sweep for the corpses already in the DB

The reaper only heals leases that expire from now on. Rows whose job failed long ago — the
three in `workspace-livetest/laura.db` — need one pass at process start.

**Files:**
- Create: `src/laura/analysis/recovery.py`
- Modify: `src/laura/db/repos.py` (two read helpers, next to the analysis section)
- Modify: `src/laura/main.py:87-94` (lifespan)
- Test: `tests/test_stranded_run_recovery.py` (append)

**Interfaces:**
- Consumes: `repos.fail_stranded_analysis_run` (Task 1), `ANALYSIS_RUN_KIND` and
  `analysis_run_id_from_payload` (Task 2).
- Produces: `laura.analysis.recovery.recover_stranded_analysis_runs(db: Database) -> list[str]`
  (the healed run ids), `repos.list_unfinished_analysis_runs(db) -> list[dict[str, Any]]`,
  `repos.list_jobs_of_kind(db, kind: str) -> list[dict[str, Any]]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_stranded_run_recovery.py` (extend the imports with
`from fastapi.testclient import TestClient`, `from laura.analysis.recovery import
recover_stranded_analysis_runs`, `from laura.config import Settings`, and
`from laura.main import create_app`):

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_stranded_run_recovery.py -k "sweep or startup or reachable" -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'laura.analysis.recovery'`.

- [ ] **Step 3: Add the two read helpers**

In `src/laura/db/repos.py`, next to the other analysis helpers (after
`fail_stranded_analysis_run`):

```python
def list_unfinished_analysis_runs(db: Database) -> list[dict[str, Any]]:
    """Every run that never reached a terminal status. Empty in a healthy DB."""
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM analysis_runs WHERE status IN ('queued','running') "
            "ORDER BY COALESCE(started_at, '') DESC, id DESC"
        ).fetchall()
        return [dict(r) for r in rows]
```

And next to the job helpers (after `list_jobs`, ~line 118):

```python
def list_jobs_of_kind(db: Database, kind: str) -> list[dict[str, Any]]:
    """All jobs of one kind, newest first. Used to walk back from a run to its job."""
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT id, status, payload_json FROM jobs WHERE kind=? "
            "ORDER BY created_order DESC",
            (kind,),
        ).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 4: Create the sweep module**

Create `src/laura/analysis/recovery.py`:

```python
"""One-time repair for analysis_runs rows whose worker never came back.

jobs/runner.py's reaper finalizes an analysis run when its job's lease expires -- from now on.
It never revisits a job that failed long ago, so the corpses already in the DB stay 'running'
forever (workspace-livetest holds three). This sweep closes that gap once per process start,
and catches two shapes the reaper structurally cannot see: a run whose job row was deleted
(enqueue drops a failed row when its idempotency key is reused) and a run that never got a job.

Safety rule: a run is finalized only when its job is terminal or gone. A job still queued,
leased or running -- including one whose worker was SIGKILLed and whose lease has not expired
yet -- belongs to the reaper, not here.
"""

from __future__ import annotations

import logging
from typing import Any

from ..db import repos
from ..db.database import Database
from ..jobs.runner import ANALYSIS_RUN_KIND, analysis_run_id_from_payload

logger = logging.getLogger(__name__)

_TERMINAL_JOB_STATUS = frozenset({"succeeded", "failed", "canceled"})


def recover_stranded_analysis_runs(db: Database) -> list[str]:
    """Finalize every unfinished run whose job is terminal or absent. Returns healed ids."""
    unfinished = repos.list_unfinished_analysis_runs(db)
    if not unfinished:
        return []
    jobs_by_run = _analysis_jobs_by_run(db)
    healed: list[str] = []
    for run in unfinished:
        run_id = str(run["id"])
        job = jobs_by_run.get(run_id)
        if job is None:
            reason = "no analysis.run job owns this run"
        elif str(job["status"]) in _TERMINAL_JOB_STATUS:
            reason = (
                f"job {job['id']} ended as {job['status']} without finalizing the run"
            )
        else:
            continue  # a live job still owns it
        if repos.fail_stranded_analysis_run(
            db, run_id, error=reason, recovered_by="startup sweep"
        ):
            logger.warning("analysis run %s finalized as failed: %s", run_id, reason)
            healed.append(run_id)
    return healed


def _analysis_jobs_by_run(db: Database) -> dict[str, dict[str, Any]]:
    """analysis_run_id -> its newest analysis.run job row."""
    out: dict[str, dict[str, Any]] = {}
    for row in repos.list_jobs_of_kind(db, ANALYSIS_RUN_KIND):
        run_id = analysis_run_id_from_payload(row["payload_json"])
        if run_id is not None:
            out.setdefault(run_id, row)  # list_jobs_of_kind is newest-first
    return out
```

- [ ] **Step 5: Wire it into startup**

In `src/laura/main.py`, add the import next to the other analysis imports:

```python
from .analysis.recovery import recover_stranded_analysis_runs
```

and call it at the top of the lifespan, before the runner starts:

```python
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # A worker that was SIGKILLed leaves its analysis run saying 'running' forever, and
        # the job reaper only ever sees leases that expire from now on. Heal what the last
        # process left behind before this one starts working.
        recover_stranded_analysis_runs(db)
        if settings.start_runner:
            runner.start()
        try:
            yield
        finally:
            runner.stop()
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
uv run pytest tests/test_stranded_run_recovery.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/laura/analysis/recovery.py src/laura/db/repos.py src/laura/main.py tests/test_stranded_run_recovery.py && git commit -m "feat(analysis): startup sweep finalizes runs whose worker never came back"
```

---

### Task 4: A retried run stops carrying the previous attempt

`analysis.run` is enqueued with `max_attempts=2`. After Task 2, attempt 1 can end as `failed`
with diagnostics — and attempt 2 would then sit at `status='running'` **with** a `finished_at`
and a stale `{"error": ...}`, both of which `api/analysis.py:47` hands to the UI.

**Files:**
- Modify: `src/laura/db/repos.py:445-450` (`start_analysis_run`)
- Test: `tests/test_stranded_run_recovery.py` (append)

**Interfaces:**
- Consumes: `_seed_run` (Task 1).
- Produces: nothing new — same signature, additional columns written.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/test_stranded_run_recovery.py -k restart_clears -v
```

Expected: FAIL — `assert '2026-...Z' is None` on `finished_at`.

- [ ] **Step 3: Implement**

```python
def start_analysis_run(db: Database, run_id: str) -> None:
    """Mark the run in flight, clearing whatever a previous attempt left behind.

    analysis.run is enqueued with max_attempts=2, so a retry re-enters here. Without the
    reset, the retry runs with the first attempt's finished_at and its {"error": ...} still
    in place -- and api/analysis.py hands both straight to the UI. started_at is re-stamped,
    never nulled: every run resolver orders on COALESCE(started_at, '') DESC.
    """
    with db.transaction() as conn:
        conn.execute(
            "UPDATE analysis_runs SET status='running', started_at=?, finished_at=NULL, "
            "diagnostics_json='{}' WHERE id=?",
            (utcnow_iso(), run_id),
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_stranded_run_recovery.py tests/test_analysis_run_finalization.py tests/test_analysis_api.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/laura/db/repos.py tests/test_stranded_run_recovery.py && git commit -m "fix(db): a retried analysis run drops the previous attempt's verdict"
```

---

### Task 5: `get_latest_succeeded_analysis_run` — the five gates stop being blockable

**Files:**
- Modify: `src/laura/db/repos.py` (new resolver, beside `get_latest_transcript_run`)
- Modify: `src/laura/api/shorts_candidates.py:57-59`
- Modify: `src/laura/analysis/shorts_handlers.py:114-119`
- Modify: `src/laura/mcp/tools.py:195-197` and `:283-290`
- Modify: `src/laura/analysis/visual_embed.py:353-355`
- Test: `tests/test_stranded_run_recovery.py` (append)

**Interfaces:**
- Consumes: `_seed_run` (Task 1).
- Produces: `repos.get_latest_succeeded_analysis_run(db: Database, asset_id: str) ->
  dict[str, Any] | None`.

- [ ] **Step 1: Write the failing tests**

Append (extend the imports with `from laura.mcp.tools import tool_extract_shorts`):

```python
def _asset_with_a_corpse_on_top(db: Database, tmp_path: Path) -> tuple[str, str]:
    """(asset_id, succeeded_run_id) — a good run shadowed by a newer failed one."""
    asset_id, good = _seed_run(db, tmp_path, status="succeeded")
    corpse = repos.create_analysis_run(
        db, asset_id=asset_id, pipeline_version="t", config={}
    )
    repos.start_analysis_run(db, str(corpse["id"]))
    repos.fail_stranded_analysis_run(
        db, str(corpse["id"]), error="worker died", recovered_by="test"
    )
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
    tmp_path: Path
) -> None:
    settings = Settings(workspace_root=tmp_path / "ws", token=None, start_runner=False)
    app = create_app(settings)
    db: Database = app.state.db
    asset_id, good = _asset_with_a_corpse_on_top(db, tmp_path)

    with TestClient(app) as client:
        res = client.post(f"/assets/{asset_id}/shorts-candidates:extract", json={})

    assert res.status_code == 202
    assert res.json()["analysis_run_id"] == good
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_stranded_run_recovery.py -k "succeeded or shorts" -v
```

Expected: `AttributeError: ... has no attribute 'get_latest_succeeded_analysis_run'`, and the
two gate tests fail on `ok is False` / `409`.

- [ ] **Step 3: Add the resolver**

In `src/laura/db/repos.py`, directly below `get_latest_transcript_run`:

```python
def get_latest_succeeded_analysis_run(db: Database, asset_id: str) -> dict[str, Any] | None:
    """The newest run that SUCCEEDED, or None.

    Sibling of :func:`get_latest_transcript_run` for the artifact-free gates. Asking
    :func:`get_latest_analysis_run` and then testing ``status == 'succeeded'`` is a different
    question: it lets a newer failed or stranded run shadow a perfectly good one, which locked
    assets out of shorts extraction and visual embedding until someone re-analysed by hand.
    Ordering mirrors get_latest_analysis_run.
    """
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM analysis_runs WHERE asset_id=? AND status='succeeded' "
            "ORDER BY COALESCE(started_at, '') DESC, id DESC LIMIT 1",
            (asset_id,),
        ).fetchone()
        return dict(row) if row is not None else None
```

- [ ] **Step 4: Move the five gates**

`src/laura/api/shorts_candidates.py:57-59`:

```python
    run = repos.get_latest_succeeded_analysis_run(db, asset_id)
    if run is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "analyze the asset first")
```

`src/laura/analysis/shorts_handlers.py:114-119` — keep the diagnostic message by looking up
the newest run only when the gate closes:

```python
    run = repos.get_latest_succeeded_analysis_run(db, asset_id)
    if run is None:
        latest = repos.get_latest_analysis_run(db, asset_id)
        status = "none" if latest is None else str(latest["status"])
        raise ValueError(
            f"no succeeded analysis run for asset {asset_id} (latest status: {status})"
        )
```

`src/laura/mcp/tools.py:195-197`:

```python
    run = repos.get_latest_succeeded_analysis_run(db, asset_id)
    if run is None:
        return {"ok": False, "error": "no succeeded analysis run", "asset_id": asset_id}
```

`src/laura/mcp/tools.py:283-290`:

```python
    run = repos.get_latest_succeeded_analysis_run(db, asset_id)
    if run is None:
        logger.debug("tool_extract_shorts: asset_id=%r has no succeeded analysis run", asset_id)
        return {
            "ok": False,
            "error": "analyze the asset first (no succeeded analysis run)",
            "asset_id": asset_id,
        }
```

`src/laura/analysis/visual_embed.py:353-355`:

```python
    run = repos.get_latest_succeeded_analysis_run(db, asset_id)
    if run is None:
        return {"ok": False, "error": "no succeeded analysis run", "asset_id": asset_id}
```

Do **not** touch `api/analysis.py:87`/`:96`, `api/scenes.py:56`, `api/shorts.py:66`,
`demo/drafts.py:41`, `ingest/handlers.py:190` — "what did the last analysis do" is a
legitimate recency question there (spec § 3).

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest tests/test_stranded_run_recovery.py tests/test_shorts_candidates_api.py tests/test_mcp_tools.py tests/test_shorts_extract_handler.py tests/test_visual_embed.py -v
```

Expected: all pass — those four existing files are the regression guards on the moved gates.

- [ ] **Step 6: Commit**

```bash
git add src/laura/db/repos.py src/laura/api/shorts_candidates.py src/laura/analysis/shorts_handlers.py src/laura/mcp/tools.py src/laura/analysis/visual_embed.py tests/test_stranded_run_recovery.py && git commit -m "fix(analysis): a newer corpse no longer shadows the run that succeeded"
```

---

### Task 6: Full verification + the live check

**Files:**
- Create: none (a throwaway script under the scratchpad, not committed)
- Modify: `lessons.md` only if the user corrected something during the run

- [ ] **Step 1: Full test suite**

```bash
uv run pytest
```

Expected: green. Note the count against the pre-change baseline; nothing may regress.

- [ ] **Step 2: Bare mypy (CI parity — this type-checks `tests/` too)**

```bash
uv run mypy
```

Expected: no errors. `uv run mypy src` is **not** the CI command and has hidden errors before.

- [ ] **Step 3: Lint**

```bash
uv run ruff check src tests
```

Expected: no findings.

- [ ] **Step 4: Live check against a COPY of the livetest DB**

Never touch the original. Copy `workspace-livetest/laura.db` (plus its `-wal`/`-shm` sidecars
— `verify_stranded_transcripts.py` shows why: without them the copy is stale) into the
scratchpad, then run the sweep against the copy and assert:

1. the three runs that were `'running'` are now `'failed'` with `recovered_by == "startup sweep"`,
2. `repos.get_latest_transcript_run` still resolves AgentFarm's 165-segment run and n8n's
   8-segment run — the transcripts stay reachable,
3. `repos.get_latest_succeeded_analysis_run` returns a run for both assets, i.e. shorts
   extraction is no longer locked out.

Record the actual numbers in the completion report. If the copy cannot be made (livetest
workspace absent), say so explicitly rather than claiming the check passed.

- [ ] **Step 5: Report**

Summarize: what changed, the three verification command outputs, and the live-check numbers.
Do not claim "done" for any step whose command was not actually run.
