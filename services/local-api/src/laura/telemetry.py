"""OpenTelemetry tracing (optional ``otel`` extra) — docs/10.

Instrumentation uses the OTel API only; when the package is absent every span is a
no-op, so the desktop backend runs without the dependency. ``configure_tracing`` wires
an OTLP exporter when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set (server mode); tests install
an in-memory exporter through the SDK. The tracer is resolved per call so a provider set
after import (tests, late configure) is picked up.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

_otel_trace: Any = None
try:  # API is enough to instrument; the SDK/exporter only matter when exporting.
    from opentelemetry import trace as _otel_trace
except Exception:  # noqa: BLE001 - any import problem -> tracing disabled
    _otel_trace = None


class _NoopSpan:
    """Stand-in span so call sites never branch on whether OTel is present."""

    def set_attribute(self, _key: str, _value: Any) -> None:
        return


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    """Start a current span with ``attributes`` if OTel is available; else a no-op."""
    if _otel_trace is None:
        yield _NoopSpan()
        return
    tracer = _otel_trace.get_tracer("laura")
    with tracer.start_as_current_span(name) as active:
        for key, value in attributes.items():
            active.set_attribute(key, value)
        yield active


def configure_tracing(service_name: str = "laura-api") -> bool:
    """Wire an OTLP/HTTP exporter when OTel is installed and an endpoint is configured.

    Returns True if tracing was configured, False otherwise (no-op). Safe to call when
    the ``otel`` extra is absent — it simply returns False.
    """
    if _otel_trace is None or not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception:  # noqa: BLE001 - SDK/exporter not installed
        return False
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    _otel_trace.set_tracer_provider(provider)
    return True
