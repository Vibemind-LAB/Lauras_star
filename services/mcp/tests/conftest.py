"""MockTransport plumbing: every test builds a LauraClient whose HTTP layer is a local handler."""
from __future__ import annotations

from collections.abc import Callable

import httpx

from laura_mcp.client import LauraClient


def make_client(handler: Callable[[httpx.Request], httpx.Response]) -> LauraClient:
    return LauraClient(token="test-token", transport=httpx.MockTransport(handler))
