"""Optional Celery worker for server mode (ADR-0003). Requires the ``server`` extra.

Strategy: Celery schedules draining; the existing DB job runner does the work against
the configured backend. On Postgres the claim uses ``FOR UPDATE SKIP LOCKED``, so many
Celery workers can drain concurrently without double-processing. This is the scale-out
path; the in-process runner remains the default (and the only path on the desktop).
"""

from __future__ import annotations

import os

from celery import Celery

celery_app = Celery(
    "laura",
    broker=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    backend=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
)

# Celery Beat periodically reaps expired leases so crashed workers' jobs are requeued
# without an in-process runner thread (the server-mode equivalent of the desktop reaper).
celery_app.conf.beat_schedule = {
    "reap-expired-leases": {
        "task": "laura.reap_expired",
        "schedule": float(os.environ.get("LAURA_REAP_INTERVAL", "30")),
    }
}


@celery_app.task(name="laura.drain_jobs")  # type: ignore[untyped-decorator]
def drain_jobs() -> int:
    """Run all currently-queued jobs once; returns how many ran."""
    from ..ai.handlers import register_ai_handlers
    from ..analysis.handlers import register_analysis_handlers
    from ..config import Settings
    from ..db.database import create_database
    from ..demo.handlers import register_demo_handlers
    from ..ingest.handlers import register_ingest_handlers
    from ..render.handlers import register_render_handlers
    from .runner import JobRunner, default_registry

    settings = Settings.load()
    db = create_database(settings)
    db.migrate()
    registry = default_registry()
    register_ingest_handlers(registry)
    register_analysis_handlers(registry)
    register_render_handlers(registry)
    register_ai_handlers(registry)
    register_demo_handlers(registry)
    runner = JobRunner(db, registry, lease_seconds=settings.lease_seconds)

    ran = 0
    while runner.run_once():
        ran += 1
    return ran


@celery_app.task(name="laura.reap_expired")  # type: ignore[untyped-decorator]
def reap_expired() -> int:
    """Requeue or fail jobs whose lease expired (Celery Beat). Returns count touched."""
    from ..config import Settings
    from ..db.database import create_database
    from .runner import JobRunner

    settings = Settings.load()
    db = create_database(settings)
    db.migrate()
    runner = JobRunner(db, {}, lease_seconds=settings.lease_seconds)
    return runner.reap_expired()
