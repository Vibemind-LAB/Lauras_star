"""FastAPI application factory and entrypoint.

Binds to loopback only (local-first). Migrates the DB and starts the background
job runner on startup; stops it cleanly on shutdown.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response

from . import PIPELINE_VERSION, __version__
from .analysis.handlers import register_analysis_handlers
from .api import admin, analysis, assets, jobs, projects, search, timelines
from .api.models import HealthOut
from .api.ratelimit import RateLimiter, make_rate_limit_middleware
from .config import Settings, ensure_workspace
from .db.database import create_database
from .ingest.handlers import register_ingest_handlers
from .jobs import JobRunner, default_registry
from .metrics import metrics_middleware, metrics_response


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load()
    ensure_workspace(settings)
    db = create_database(settings)
    db.migrate()
    registry = default_registry()
    register_ingest_handlers(registry)
    register_analysis_handlers(registry)
    runner = JobRunner(db, registry, lease_seconds=settings.lease_seconds)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if settings.start_runner:
            runner.start()
        try:
            yield
        finally:
            runner.stop()

    app = FastAPI(title="Laura Local API", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.runner = runner
    app.middleware("http")(metrics_middleware)
    if settings.rate_limit_rpm > 0:
        capacity = settings.rate_limit_burst or settings.rate_limit_rpm
        limiter = RateLimiter(capacity=capacity, refill_per_sec=settings.rate_limit_rpm / 60.0)
        app.state.rate_limiter = limiter
        app.middleware("http")(make_rate_limit_middleware(limiter))

    @app.get("/healthz", response_model=HealthOut, tags=["health"])
    def healthz() -> HealthOut:
        return HealthOut(
            status="ok",
            version=__version__,
            pipeline_version=PIPELINE_VERSION,
            schema_version=db.schema_version(),
        )

    @app.get("/metrics", tags=["observability"])
    def metrics() -> Response:
        return metrics_response()

    app.include_router(projects.router)
    app.include_router(assets.router)
    app.include_router(jobs.router)
    app.include_router(analysis.router)
    app.include_router(timelines.router)
    app.include_router(admin.router)
    app.include_router(search.router)
    return app


def run() -> None:
    """Console entrypoint (`laura-api`)."""
    import uvicorn

    settings = Settings.load()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    run()
