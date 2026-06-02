"""Portion 15.2 — token-bucket rate limiting.

Unit tests drive a deterministic injected clock (burst exhaustion + refill); the
integration test confirms middleware wiring, the 429 + Retry-After contract, that
infra paths are exempt, and that the limiter is off by default.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from laura.api.ratelimit import RateLimiter
from laura.config import Settings
from laura.main import create_app


class _FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def test_bucket_exhausts_then_refills() -> None:
    clock = _FakeClock()
    rl = RateLimiter(capacity=2, refill_per_sec=1.0, clock=clock.now)
    assert rl.check("a")[0] is True            # 2 -> 1
    assert rl.check("a")[0] is True            # 1 -> 0
    allowed, retry = rl.check("a")             # 0 -> denied
    assert allowed is False
    assert retry > 0.0
    clock.advance(1.0)                          # refill exactly one token
    assert rl.check("a")[0] is True            # allowed again


def test_identities_are_independent() -> None:
    clock = _FakeClock()
    rl = RateLimiter(capacity=1, refill_per_sec=1.0, clock=clock.now)
    assert rl.check("x")[0] is True
    assert rl.check("x")[0] is False           # x exhausted
    assert rl.check("y")[0] is True            # y has its own full bucket


def test_disabled_by_default(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(workspace_root=tmp_path, start_runner=False)))
    client.__enter__()
    try:
        for _ in range(20):
            assert client.get("/projects").status_code == 200
    finally:
        client.__exit__(None, None, None)


def test_429_with_retry_after(tmp_path: Path) -> None:
    settings = Settings(
        workspace_root=tmp_path, start_runner=False,
        rate_limit_rpm=6, rate_limit_burst=2,   # burst 2, slow refill (0.1/s)
    )
    client = TestClient(create_app(settings))
    client.__enter__()
    try:
        statuses = [client.get("/projects").status_code for _ in range(5)]
        assert statuses[:2] == [200, 200]       # burst allowed
        assert 429 in statuses[2:]              # then throttled

        throttled = client.get("/projects")
        assert throttled.status_code == 429
        assert int(throttled.headers["Retry-After"]) >= 1

        # infra endpoints are never rate-limited
        assert client.get("/healthz").status_code == 200
    finally:
        client.__exit__(None, None, None)
