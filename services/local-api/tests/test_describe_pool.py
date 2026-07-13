"""VLM model-pool failover on the OpenRouter describe backend."""

import pytest

from laura.short_creator.describe import OpenRouterDescribeBackend


def _backend(models: list[str]) -> OpenRouterDescribeBackend:
    return OpenRouterDescribeBackend(api_key="test-key", models=models)


def test_pool_advances_on_empty_and_sticks(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _backend(["m1", "m2"])
    calls: list[str] = []

    def fake_attempt(model: str, frames: list[bytes], prompt: str) -> str:
        calls.append(model)
        return "" if model == "m1" else f"described by {model}"

    monkeypatch.setattr(backend, "_attempt", fake_attempt)
    assert backend.describe([b"x"], "what?") == "described by m2"
    assert calls == ["m1", "m2"]
    # sticky: next call starts at m2
    assert backend.describe([b"x"], "again?") == "described by m2"
    assert calls == ["m1", "m2", "m2"]


def test_pool_returns_empty_when_all_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _backend(["m1", "m2"])
    monkeypatch.setattr(backend, "_attempt", lambda model, frames, prompt: "")
    assert backend.describe([b"x"], "what?") == ""


def test_single_model_default_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = OpenRouterDescribeBackend(api_key="test-key")
    seen: list[str] = []

    def record_and_return(model: str, frames: list[bytes], prompt: str) -> str:
        seen.append(model)
        return "ok"

    monkeypatch.setattr(backend, "_attempt", record_and_return)
    assert backend.describe([b"x"], "what?") == "ok"
    assert len(seen) == 1
