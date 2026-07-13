"""Model-pool rotation: sticky failover across per-model clients."""

from typing import Any

import pytest

from laura.short_creator.providers import (
    RotatingChatClient,
    _is_flaky,
    _parse_model_pool,
)


class _Exhausted(Exception):
    """Stands in for the error a RetryingChatClient re-raises after its retries."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


class _FakeClient:
    def __init__(self, name: str, fail_times: int = 0) -> None:
        self.name = name
        self.fail_times = fail_times
        self.calls = 0

    async def create(self, *args: Any, **kwargs: Any) -> str:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise _Exhausted(429)
        return f"answer from {self.name}"


def test_parse_model_pool_dedupes_and_prepends_first() -> None:
    pool = _parse_model_pool(" b , a,c ,a", first="a")
    assert pool == ("a", "b", "c")
    assert _parse_model_pool(None, first="a") == ("a",)
    assert _parse_model_pool("  ", first="a") == ("a",)


def test_is_flaky_classification() -> None:
    assert _is_flaky(TypeError("choices=None"))
    assert _is_flaky(_Exhausted(503))
    assert _is_flaky(RuntimeError("empty completion content (reasoning-only reply)"))
    assert not _is_flaky(RuntimeError("boom"))
    assert not _is_flaky(_Exhausted(401))


def test_rotating_client_advances_and_sticks() -> None:
    first = _FakeClient("m1", fail_times=99)
    second = _FakeClient("m2")
    client = RotatingChatClient([first, second])
    import asyncio

    assert asyncio.run(client.create()) == "answer from m2"
    assert (first.calls, second.calls) == (1, 1)
    # sticky: the next call goes straight to m2
    assert asyncio.run(client.create()) == "answer from m2"
    assert (first.calls, second.calls) == (1, 2)


def test_rotating_client_reraises_when_pool_exhausted() -> None:
    import asyncio

    client = RotatingChatClient([_FakeClient("m1", fail_times=9), _FakeClient("m2", fail_times=9)])
    with pytest.raises(_Exhausted):
        asyncio.run(client.create())


def test_rotating_client_reraises_non_flaky_immediately() -> None:
    import asyncio

    class _Auth(_FakeClient):
        async def create(self, *args: Any, **kwargs: Any) -> str:
            raise _Exhausted(401)

    second = _FakeClient("m2")
    client = RotatingChatClient([_Auth("m1"), second])
    with pytest.raises(_Exhausted):
        asyncio.run(client.create())
    assert second.calls == 0


def test_rotating_client_rejects_empty_pool() -> None:
    with pytest.raises(ValueError):
        RotatingChatClient([])
