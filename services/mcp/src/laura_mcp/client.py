"""HTTP client for the running Laura app. Pure client — never imports backend code."""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "http://127.0.0.1:8765"  # deliberately hardwired: local app only
BACKEND_DOWN = "Laura app is not running — start the Laura desktop app, then retry."
DEFAULT_TIMEOUT = 30.0


class LauraError(Exception):
    """One human-readable sentence per failure; MCP tools surface str(exc) verbatim."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class LauraClient:
    def __init__(self, token: str, transport: httpx.BaseTransport | None = None) -> None:
        self._http = httpx.Client(
            base_url=BASE_URL,
            headers={"X-Laura-Token": token},
            timeout=DEFAULT_TIMEOUT,
            transport=transport,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        response = self._send(method, path, json=json, params=params, timeout=timeout)
        if not response.content:
            return None
        return response.json()

    def get_bytes(self, path: str, *, timeout: float | None = None) -> bytes:
        return self._send("GET", path, json=None, params=None, timeout=timeout).content

    def _send(
        self,
        method: str,
        path: str,
        *,
        json: Any,
        params: dict[str, Any] | None,
        timeout: float | None,
    ) -> httpx.Response:
        try:
            response = self._http.request(
                method, path, json=json, params=params,
                timeout=timeout if timeout is not None else DEFAULT_TIMEOUT,
            )
        except httpx.ConnectError as exc:
            raise LauraError(BACKEND_DOWN) from exc
        except httpx.HTTPError as exc:  # timeouts, protocol errors
            raise LauraError(f"Laura request failed: {exc}") from exc
        if response.is_success:
            return response
        raise LauraError(_detail_sentence(response))


def _detail_sentence(response: httpx.Response) -> str:
    """The backend's detail sentence, never the raw JSON (same rule as the desktop client)."""
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str) and detail:
            return detail
        if detail is not None:
            return str(detail)
    return f"HTTP {response.status_code} from the Laura backend"
