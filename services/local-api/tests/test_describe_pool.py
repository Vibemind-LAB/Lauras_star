"""VLM model-pool failover on the OpenRouter describe backend."""

import pytest

from laura.short_creator.describe import OpenRouterDescribeBackend


def _backend(models: list[str]) -> OpenRouterDescribeBackend:
    return OpenRouterDescribeBackend(api_key="test-key", model="vendor/m1", models=models)


def test_pool_advances_on_empty_and_sticks(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _backend(["vendor/m2"])
    calls: list[str] = []

    def fake_attempt(model: str, frames: list[bytes], prompt: str) -> str:
        calls.append(model)
        return "" if model == "vendor/m1" else f"described by {model}"

    monkeypatch.setattr(backend, "_attempt", fake_attempt)
    assert backend.describe([b"x"], "what?") == "described by vendor/m2"
    assert calls == ["vendor/m1", "vendor/m2"]
    # sticky: next call starts at m2
    assert backend.describe([b"x"], "again?") == "described by vendor/m2"
    assert calls == ["vendor/m1", "vendor/m2", "vendor/m2"]


def test_pool_returns_empty_when_all_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _backend(["vendor/m1", "vendor/m2"])
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


def test_explicit_model_leads_the_pool() -> None:
    backend = OpenRouterDescribeBackend(
        api_key="k", model="vendor/explicit", models=["vendor/a", "vendor/explicit", "vendor/b"]
    )
    assert backend._models == ["vendor/explicit", "vendor/a", "vendor/b"]


def test_resolve_backend_reads_pool_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAURA_VLM_PROVIDER", "openrouter")
    monkeypatch.setenv("LAURA_OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("LAURA_VLM_MODEL", "vendor/primary")
    monkeypatch.setenv("LAURA_VLM_MODEL_POOL", "vendor/a,vendor/primary , vendor/b")
    from laura.short_creator.describe import resolve_describe_backend

    backend = resolve_describe_backend()
    assert isinstance(backend, OpenRouterDescribeBackend)
    assert backend._models == ["vendor/primary", "vendor/a", "vendor/b"]
