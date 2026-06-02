"""Prometheus metrics + HTTP middleware (docs/10, docs/14).

Labels are kept low-cardinality (method/status/kind) so the series count stays bounded.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

HTTP_REQUESTS = Counter(
    "laura_http_requests_total", "HTTP requests", ["method", "status"]
)
HTTP_LATENCY = Histogram(
    "laura_http_request_seconds", "HTTP request latency (s)", ["method"]
)
JOBS = Counter("laura_jobs_total", "Jobs processed", ["kind", "status"])


async def metrics_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    start = time.perf_counter()
    response = await call_next(request)
    HTTP_LATENCY.labels(request.method).observe(time.perf_counter() - start)
    HTTP_REQUESTS.labels(request.method, str(response.status_code)).inc()
    return response


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
