"""Token-bucket rate limiting (docs/09-security.md).

Per-identity buckets: the API key (sha256, never the raw secret) when a Bearer token
is present, otherwise the client host. Disabled by default (``rpm=0``) so the desktop's
loopback traffic is unaffected; server-mode enables it via env. State is in-memory and
per-process — distributed scale-out would back this with Redis (noted, not built).
"""

from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from threading import Lock

from fastapi import Request, Response
from fastapi.responses import JSONResponse

_EXEMPT_PATHS = frozenset({"/healthz", "/metrics"})
Clock = Callable[[], float]
CallNext = Callable[[Request], Awaitable[Response]]
Middleware = Callable[[Request, CallNext], Awaitable[Response]]


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    """A per-identity token bucket: ``capacity`` tokens refilled at ``refill_per_sec``;
    each request costs one token. The clock is injectable for deterministic tests."""

    def __init__(
        self, *, capacity: int, refill_per_sec: float, clock: Clock = time.monotonic
    ) -> None:
        self._capacity = float(capacity)
        self._refill = refill_per_sec
        self._clock = clock
        self._buckets: dict[str, _Bucket] = {}
        self._lock = Lock()

    def check(self, identity: str) -> tuple[bool, float]:
        """Consume a token for ``identity``. Returns ``(allowed, retry_after_seconds)``."""
        now = self._clock()
        with self._lock:
            bucket = self._buckets.get(identity)
            if bucket is None:
                bucket = _Bucket(tokens=self._capacity, updated=now)
                self._buckets[identity] = bucket
            elapsed = max(0.0, now - bucket.updated)
            bucket.tokens = min(self._capacity, bucket.tokens + elapsed * self._refill)
            bucket.updated = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True, 0.0
            deficit = 1.0 - bucket.tokens
            return False, (deficit / self._refill if self._refill > 0 else 1.0)


def identity_for(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        digest = hashlib.sha256(auth[7:].strip().encode()).hexdigest()
        return f"key:{digest[:32]}"
    client = request.client
    return f"host:{client.host if client else 'unknown'}"


def make_rate_limit_middleware(limiter: RateLimiter) -> Middleware:
    """Build an HTTP middleware that enforces ``limiter`` (infra paths exempt)."""

    async def rate_limit(request: Request, call_next: CallNext) -> Response:
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)
        allowed, retry = limiter.check(identity_for(request))
        if not allowed:
            response = JSONResponse({"detail": "rate limit exceeded"}, status_code=429)
            response.headers["Retry-After"] = str(max(1, math.ceil(retry)))
            return response
        return await call_next(request)

    return rate_limit
