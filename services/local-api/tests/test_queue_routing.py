"""Portion 15.7 — queue routing: CPU/GPU queues, priority claim, GPU stage stubs, reaper."""

from __future__ import annotations

import json
from pathlib import Path

from laura.analysis.handlers import register_analysis_handlers
from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.jobs.queues import (
    CPU_QUEUES,
    GPU_QUEUES,
    QUEUE_ANALYSIS_CPU,
    QUEUE_ANALYSIS_GPU,
    queue_for,
)
from laura.jobs.runner import JobRunner, default_registry, enqueue


def _db(tmp_path: Path) -> SqliteDatabase:
    settings = Settings(workspace_root=tmp_path, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    return db


def test_queue_for_routes_gpu_stages() -> None:
    assert queue_for("analysis.run") == QUEUE_ANALYSIS_CPU
    assert queue_for("analysis.align") == QUEUE_ANALYSIS_GPU
    assert queue_for("analysis.embed") == QUEUE_ANALYSIS_GPU
    assert QUEUE_ANALYSIS_GPU in GPU_QUEUES
    assert QUEUE_ANALYSIS_GPU not in CPU_QUEUES


def test_claim_prefers_higher_priority(tmp_path: Path) -> None:
    db = _db(tmp_path)
    low = enqueue(db, queue=QUEUE_ANALYSIS_CPU, kind="echo", priority=0)
    high = enqueue(db, queue=QUEUE_ANALYSIS_CPU, kind="echo", priority=10)
    claimed = db.claim_job(worker_id="w", lease_seconds=60, queues=None)
    assert claimed is not None
    assert claimed["id"] == high          # priority beats insertion order
    assert claimed["id"] != low


def test_queue_filter_isolates_gpu_work(tmp_path: Path) -> None:
    db = _db(tmp_path)
    gpu_job = enqueue(db, queue=QUEUE_ANALYSIS_GPU, kind="analysis.embed")
    # a CPU-only worker never sees the GPU job
    assert db.claim_job(worker_id="cpu", lease_seconds=60, queues=CPU_QUEUES) is None
    # a GPU worker claims it
    claimed = db.claim_job(worker_id="gpu", lease_seconds=60, queues=GPU_QUEUES)
    assert claimed is not None and claimed["id"] == gpu_job


def test_gpu_stage_stubs_skip_gracefully(tmp_path: Path) -> None:
    db = _db(tmp_path)
    registry = default_registry()
    register_analysis_handlers(registry)
    runner = JobRunner(db, registry)
    for kind in ("analysis.align", "analysis.embed"):
        job_id = enqueue(db, queue=QUEUE_ANALYSIS_GPU, kind=kind)
        assert runner.run_once() is True
        job = repos.get_job(db, job_id)
        assert job is not None
        assert job["status"] == "succeeded"
        assert json.loads(job["result_json"])["status"] == "skipped"


def test_reaper_requeues_expired_lease(tmp_path: Path) -> None:
    db = _db(tmp_path)
    job_id = enqueue(db, queue=QUEUE_ANALYSIS_CPU, kind="echo")
    # claim with an already-expired lease (negative duration)
    claimed = db.claim_job(worker_id="w", lease_seconds=-10, queues=None)
    assert claimed is not None and claimed["id"] == job_id

    runner = JobRunner(db, default_registry())
    assert runner.reap_expired() == 1
    job = repos.get_job(db, job_id)
    assert job is not None
    assert job["status"] == "queued"      # requeued (attempt 1 < max_attempts)
    assert job["worker_id"] is None
