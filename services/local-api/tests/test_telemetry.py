"""Portion 17.1 — OpenTelemetry tracing: no-op safety + job-execute span (in-memory)."""

from __future__ import annotations

from pathlib import Path

import pytest

from laura.config import Settings
from laura.db.database import SqliteDatabase
from laura.jobs.runner import JobRunner, default_registry, enqueue
from laura.telemetry import span


def test_span_is_noop_safe() -> None:
    # The span context manager works and accepts attributes whether or not OTel is
    # installed/configured — call sites never branch on availability.
    with span("unit.test", **{"k": "v"}) as sp:
        sp.set_attribute("more", 1)


def test_job_execute_span_recorded(tmp_path: Path) -> None:
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)  # process-global; first real set wins

    db = SqliteDatabase(Settings(workspace_root=tmp_path, start_runner=False).db_path)
    db.migrate()
    enqueue(db, queue="analysis.cpu", kind="echo", payload={"x": 1})
    assert JobRunner(db, default_registry()).run_once() is True

    job_spans = [s for s in exporter.get_finished_spans() if s.name == "job.execute"]
    assert len(job_spans) == 1
    attrs = dict(job_spans[0].attributes or {})
    assert attrs["job.kind"] == "echo"
    assert attrs["job.queue"] == "analysis.cpu"
    assert attrs["job.status"] == "succeeded"
