from __future__ import annotations

import httpx
import pytest

from laura_mcp.client import (
    BACKEND_DOWN,
    DEFAULT_BASE_URL,
    LauraClient,
    LauraError,
    base_url,
)

from .conftest import make_client


def test_request_sends_token_and_parses_json() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["token"] = request.headers.get("X-Laura-Token", "")
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler)
    assert client.request("GET", "/projects") == {"ok": True}
    assert seen["token"] == "test-token"
    assert seen["url"] == "http://127.0.0.1:8765/projects"


def test_http_error_surfaces_detail_sentence_not_raw_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "session not found"})

    client = make_client(handler)
    with pytest.raises(LauraError) as exc:
        client.request("GET", "/production/nope")
    assert "session not found" in str(exc.value)
    assert "{" not in str(exc.value)  # never the raw JSON body


def test_connect_error_becomes_backend_down_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = make_client(handler)
    with pytest.raises(LauraError) as exc:
        client.request("GET", "/projects")
    assert str(exc.value) == BACKEND_DOWN
    assert exc.value.message == BACKEND_DOWN


def test_get_bytes_returns_raw_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x89PNG...")

    client = make_client(handler)
    assert client.get_bytes("/assets/a/frame/10").startswith(b"\x89PNG")


def test_die_adresse_kommt_aus_der_umgebung(monkeypatch: pytest.MonkeyPatch) -> None:
    """Der MCP-Server muss eine Laura auf einem ANDEREN Rechner erreichen koennen.

    Bis 16.09.2026 war sie fest auf 127.0.0.1 verdrahtet. Fuer den Umzug auf den
    Mini-PC muss sie aus der Umgebung kommen -- der MCP-Server selbst bleibt auf der
    Windows-Kiste und spricht dann ueber das LAN.
    """
    monkeypatch.setenv("LAURA_API_URL", "http://192.168.178.65:8765/")
    assert base_url() == "http://192.168.178.65:8765"  # Schraegstrich getrimmt


def test_ohne_umgebung_bleibt_es_bei_der_lokalen_adresse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Die Desktop-Installation darf von der Aenderung nichts merken."""
    monkeypatch.delenv("LAURA_API_URL", raising=False)
    assert base_url() == DEFAULT_BASE_URL == "http://127.0.0.1:8765"


def test_der_client_benutzt_die_konfigurierte_adresse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Die Naht: es reicht nicht, dass die Funktion stimmt -- der Client muss sie nehmen.

    Ohne diese Zusicherung koennte `base_url()` richtig antworten, waehrend
    `LauraClient` weiter auf die Konstante zeigt, und der Mini-PC bliebe unerreichbar.
    """
    monkeypatch.setenv("LAURA_API_URL", "http://192.168.178.65:8765")
    client = LauraClient("t")
    try:
        assert str(client._http.base_url) == "http://192.168.178.65:8765"
    finally:
        client._http.close()
