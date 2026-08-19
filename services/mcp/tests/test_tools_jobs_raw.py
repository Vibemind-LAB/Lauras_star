from __future__ import annotations

from typing import Any

import httpx

from laura_mcp import tools_jobs

from .conftest import make_client


def call(tool: str, handler: Any, /, **kwargs: Any) -> Any:
    client = make_client(handler)
    return tools_jobs.TOOLS[tool](client, **kwargs)


def test_job_status_no_wait_single_get() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/jobs/j1"
        return httpx.Response(200, json={"status": "running"})

    result = call("job_status", handler, job_id="j1")
    assert result["status"] == "running"


def test_job_status_wait_polls_until_terminal(monkeypatch: Any) -> None:
    call_count = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/jobs/j1"
        call_count[0] += 1
        if call_count[0] == 1:
            return httpx.Response(200, json={"status": "running"})
        elif call_count[0] == 2:
            return httpx.Response(200, json={"status": "succeeded"})
        else:
            raise AssertionError(f"Unexpected call #{call_count[0]}")

    monkeypatch.setattr(tools_jobs.time, "sleep", lambda s: None)  # type: ignore[attr-defined]

    result = call("job_status", handler, job_id="j1", wait_s=5)
    assert result["status"] == "succeeded"
    assert call_count[0] == 2


def test_job_status_wait_caps_at_60(monkeypatch: Any) -> None:
    fake_time = [0.0]
    call_count = [0]

    def fake_monotonic() -> float:
        result = fake_time[0]
        fake_time[0] += 61  # Advance by 61 seconds each call
        return result

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        call_count[0] += 1
        # Never return a terminal status
        return httpx.Response(200, json={"status": "running"})

    monkeypatch.setattr(tools_jobs.time, "monotonic", fake_monotonic)  # type: ignore[attr-defined]
    monkeypatch.setattr(tools_jobs.time, "sleep", lambda s: None)  # type: ignore[attr-defined]

    # Call with wait_s=600, but the cap should be 60
    result = call("job_status", handler, job_id="j1", wait_s=600)
    # After the initial GET, fake_time is at 61.0, which is > start + 60
    # So polling should stop after 2 GETs total
    assert result["status"] == "running"
    assert call_count[0] == 2  # One initial GET, one more after sleep
