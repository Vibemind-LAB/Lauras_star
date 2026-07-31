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
            reason = f"job {job['id']} ended as {job['status']} without finalizing the run"
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
