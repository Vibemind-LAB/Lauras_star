"""Jobs family: job status with bounded polling."""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import LauraClient


def _job_status(client: LauraClient, *, job_id: str, wait_s: int = 0) -> Any:
    # Cap wait_s to 60 seconds max
    wait_s = min(wait_s, 60)

    deadline = time.monotonic() + wait_s
    while True:
        response = client.request("GET", f"/jobs/{job_id}")
        status = response.get("status") if isinstance(response, dict) else None

        # Check if terminal
        if status in ("succeeded", "failed", "cancelled"):
            return response

        # If no wait requested, return immediately
        if wait_s == 0:
            return response

        # Sleep and check deadline after sleep
        time.sleep(1.0)

        # Check if deadline reached after sleep
        if time.monotonic() >= deadline:
            response = client.request("GET", f"/jobs/{job_id}")
            return response


TOOLS: dict[str, Callable[..., Any]] = {
    "job_status": _job_status,
}


def register(mcp: FastMCP, client: LauraClient) -> None:
    @mcp.tool()
    def job_status(job_id: str, wait_s: int = 0) -> Any:
        """Status of an async Laura job. wait_s > 0 polls up to that many seconds (max 60) until the job reaches succeeded/failed/cancelled."""
        return _job_status(client, job_id=job_id, wait_s=wait_s)
