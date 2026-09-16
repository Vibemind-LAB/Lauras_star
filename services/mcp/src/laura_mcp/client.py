"""HTTP client for the running Laura app. Pure client — never imports backend code."""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:8765"
BACKEND_DOWN = "Laura app is not running — start the Laura desktop app, then retry."
DEFAULT_TIMEOUT = 30.0


def base_url() -> str:
    """Adresse der laufenden Laura-API.

    Stand bis 16.09.2026 als Konstante fest auf 127.0.0.1, mit dem Kommentar
    "deliberately hardwired: local app only". Das war richtig, solange Laura eine
    Desktop-App war. Sie laeuft als Container und soll auf den Mini-PC; der
    MCP-Server bleibt dabei auf der Windows-Kiste (stdio unter OpenFang) und muss
    dann ueber das LAN sprechen.

    Der Variablenname ist der, den der Marketing-Space fuer dieselbe API schon
    benutzt (`spaces/marketing/claw/laura.py`) -- eine zweite Schreibweise fuer
    dieselbe Adresse waere eine Fehlerquelle.

    Bewusst eine Funktion und keine Modulkonstante: eine Konstante liesse sich im
    Test nur per `importlib.reload` umbiegen, und das vergiftet jeden anderen Test,
    der das Modul schon importiert hat.
    """
    return (os.environ.get("LAURA_API_URL") or DEFAULT_BASE_URL).rstrip("/")


class LauraError(Exception):
    """One human-readable sentence per failure; MCP tools surface str(exc) verbatim."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class LauraClient:
    def __init__(self, token: str, transport: httpx.BaseTransport | None = None) -> None:
        self._http = httpx.Client(
            base_url=base_url(),
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
