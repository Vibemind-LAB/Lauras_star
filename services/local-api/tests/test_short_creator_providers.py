"""Provider factory for the short-creator agents (Iteration 2/9).

The config resolution (env → :class:`AgentConfig`) and the client planning
(config → :class:`ClientSpec`) are PURE — tested here without autogen installed.
:func:`build_model_client` is the only autogen-touching function; its
missing-extra path and its construction wiring are tested with fake modules, so
these tests pass whether or not the optional ``autoshort`` extra is present.
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from laura.short_creator import providers as p

# --- resolve_from_env: zero-config defaults -------------------------------------------------


def test_resolve_defaults_are_local_free_manual() -> None:
    cfg = p.resolve_from_env({})
    assert cfg.provider == "ollama"
    assert cfg.agent_model == p.DEFAULT_AGENT_MODEL
    assert cfg.orchestrator_model == p.DEFAULT_AGENT_MODEL  # falls back to agent_model
    assert cfg.orchestration == "magentic"
    assert cfg.escalate_provider == "9router"
    assert cfg.escalate_model == p.DEFAULT_ESCALATE_MODEL
    assert cfg.auto_escalate is False
    assert cfg.qa_max_rounds == p.DEFAULT_QA_MAX_ROUNDS
    assert cfg.nine_router_base_url == p.DEFAULT_9ROUTER_BASE_URL
    assert cfg.nine_router_api_key is None
    assert cfg.openai_base_url is None
    assert cfg.openai_api_key is None


def test_resolve_orchestrator_model_defaults_to_agent_model() -> None:
    cfg = p.resolve_from_env({"LAURA_AGENT_MODEL": "llama3.1"})
    assert cfg.agent_model == "llama3.1"
    assert cfg.orchestrator_model == "llama3.1"


def test_resolve_orchestrator_model_explicit_overrides() -> None:
    cfg = p.resolve_from_env(
        {"LAURA_AGENT_MODEL": "llama3.1", "LAURA_ORCHESTRATOR_MODEL": "qwen2.5:32b"}
    )
    assert cfg.orchestrator_model == "qwen2.5:32b"


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "on", "yes"])
def test_resolve_auto_escalate_truthy(raw: str) -> None:
    assert p.resolve_from_env({"LAURA_AGENT_AUTO_ESCALATE": raw}).auto_escalate is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "", "nonsense"])
def test_resolve_auto_escalate_falsy(raw: str) -> None:
    assert p.resolve_from_env({"LAURA_AGENT_AUTO_ESCALATE": raw}).auto_escalate is False


def test_resolve_qa_max_rounds_valid() -> None:
    assert p.resolve_from_env({"LAURA_AGENT_QA_MAX_ROUNDS": "5"}).qa_max_rounds == 5


@pytest.mark.parametrize("raw", ["0", "-1", "abc", ""])
def test_resolve_qa_max_rounds_invalid_falls_back(raw: str) -> None:
    assert p.resolve_from_env({"LAURA_AGENT_QA_MAX_ROUNDS": raw}).qa_max_rounds == (
        p.DEFAULT_QA_MAX_ROUNDS
    )


def test_resolve_orchestration_graph_override() -> None:
    assert p.resolve_from_env({"LAURA_AGENT_ORCHESTRATION": "graph"}).orchestration == "graph"


def test_resolve_orchestration_unknown_stays_magentic() -> None:
    assert p.resolve_from_env({"LAURA_AGENT_ORCHESTRATION": "swarm"}).orchestration == "magentic"


@pytest.mark.parametrize("value", ["9router", "openai-compat", "ollama"])
def test_resolve_provider_accepts_known(value: str) -> None:
    assert p.resolve_from_env({"LAURA_AGENT_PROVIDER": value}).provider == value


def test_resolve_provider_unknown_falls_back_to_ollama() -> None:
    assert p.resolve_from_env({"LAURA_AGENT_PROVIDER": "bogus"}).provider == "ollama"


def test_resolve_endpoints_and_keys() -> None:
    cfg = p.resolve_from_env(
        {
            "LAURA_9ROUTER_BASE_URL": "http://box:20128/v1",
            "LAURA_9ROUTER_API_KEY": "  sk-9r  ",
            "LAURA_AGENT_BASE_URL": "http://lan:8000/v1",
            "LAURA_AGENT_API_KEY": "sk-oc",
        }
    )
    assert cfg.nine_router_base_url == "http://box:20128/v1"
    assert cfg.nine_router_api_key == "sk-9r"  # trimmed
    assert cfg.openai_base_url == "http://lan:8000/v1"
    assert cfg.openai_api_key == "sk-oc"


# --- plan_client: pure provider/model/endpoint selection ------------------------------------


def test_plan_stage_a_ollama_agent() -> None:
    spec = p.plan_client(p.resolve_from_env({}), role="agent", stage="A")
    assert spec == p.ClientSpec(kind="ollama", model="qwen2.5", base_url=None, api_key=None)


def test_plan_stage_a_ollama_orchestrator_uses_orchestrator_model() -> None:
    cfg = p.resolve_from_env({"LAURA_ORCHESTRATOR_MODEL": "qwen2.5:32b"})
    spec = p.plan_client(cfg, role="orchestrator", stage="A")
    assert spec.kind == "ollama"
    assert spec.model == "qwen2.5:32b"


def test_plan_stage_b_escalates_to_9router() -> None:
    spec = p.plan_client(p.resolve_from_env({}), role="orchestrator", stage="B")
    assert spec.kind == "openai"
    assert spec.model == p.DEFAULT_ESCALATE_MODEL
    assert spec.base_url == p.DEFAULT_9ROUTER_BASE_URL


def test_plan_stage_a_openai_compat_uses_generic_endpoint() -> None:
    cfg = p.resolve_from_env(
        {
            "LAURA_AGENT_PROVIDER": "openai-compat",
            "LAURA_AGENT_BASE_URL": "http://lan:8000/v1",
            "LAURA_AGENT_API_KEY": "sk-oc",
        }
    )
    spec = p.plan_client(cfg, role="agent", stage="A")
    assert spec == p.ClientSpec(
        kind="openai", model="qwen2.5", base_url="http://lan:8000/v1", api_key="sk-oc"
    )


def test_plan_stage_a_provider_9router_directly() -> None:
    cfg = p.resolve_from_env({"LAURA_AGENT_PROVIDER": "9router", "LAURA_9ROUTER_API_KEY": "sk-9r"})
    spec = p.plan_client(cfg, role="agent", stage="A")
    assert spec.kind == "openai"
    assert spec.base_url == p.DEFAULT_9ROUTER_BASE_URL
    assert spec.api_key == "sk-9r"


# --- build_model_client: lazy import of the optional extra ----------------------------------


def _install_fake_autogen(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, object]]:
    """Inject fake autogen modules so build_model_client constructs without the real extra.

    Returns a dict capturing the kwargs each fake client was constructed with.
    """
    captured: dict[str, dict[str, object]] = {}

    class FakeOllama:
        def __init__(self, **kw: object) -> None:
            captured["ollama"] = kw

    class FakeOpenAI:
        def __init__(self, **kw: object) -> None:
            captured["openai"] = kw

    ext = types.ModuleType("autogen_ext")
    ext_models = types.ModuleType("autogen_ext.models")
    ext_ollama = types.ModuleType("autogen_ext.models.ollama")
    ext_openai = types.ModuleType("autogen_ext.models.openai")
    core = types.ModuleType("autogen_core")
    core_models = types.ModuleType("autogen_core.models")
    ext_ollama.OllamaChatCompletionClient = FakeOllama  # type: ignore[attr-defined]
    ext_openai.OpenAIChatCompletionClient = FakeOpenAI  # type: ignore[attr-defined]
    core_models.ModelInfo = lambda **kw: dict(kw)  # type: ignore[attr-defined]
    for name, mod in {
        "autogen_ext": ext,
        "autogen_ext.models": ext_models,
        "autogen_ext.models.ollama": ext_ollama,
        "autogen_ext.models.openai": ext_openai,
        "autogen_core": core,
        "autogen_core.models": core_models,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return captured


def test_build_missing_extra_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force the import to fail regardless of whether autogen is installed.
    monkeypatch.setitem(sys.modules, "autogen_ext", None)
    monkeypatch.setitem(sys.modules, "autogen_core", None)
    with pytest.raises(RuntimeError, match="autoshort"):
        p.build_model_client(p.resolve_from_env({}), role="agent", stage="A")


def test_build_ollama_passes_model(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _install_fake_autogen(monkeypatch)
    client = p.build_model_client(p.resolve_from_env({}), role="agent", stage="A")
    assert type(client).__name__ == "FakeOllama"
    assert captured["ollama"]["model"] == "qwen2.5"


def test_build_9router_passes_base_url_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _install_fake_autogen(monkeypatch)
    cfg = p.resolve_from_env({"LAURA_9ROUTER_API_KEY": "sk-9r"})
    client = p.build_model_client(cfg, role="orchestrator", stage="B")
    # Remote clients come wrapped in the transient-failure retry shim.
    assert isinstance(client, p.RetryingChatClient)
    assert type(client._inner).__name__ == "FakeOpenAI"
    kw = captured["openai"]
    assert kw["model"] == p.DEFAULT_ESCALATE_MODEL
    assert kw["base_url"] == p.DEFAULT_9ROUTER_BASE_URL
    assert kw["api_key"] == "sk-9r"
    assert "model_info" in kw


# --- RetryingChatClient: free-tier flakes must not kill a team run ---------------------------


class _FlakyInner:
    """Fake ChatCompletionClient whose create() raises the scripted errors, then succeeds."""

    def __init__(self, errors: list[BaseException]) -> None:
        self.errors = list(errors)
        self.calls = 0
        self.model_info = {"family": "fake"}

    async def create(self, *args: object, **kwargs: object) -> str:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "ok"


def _status_error(status: int) -> Exception:
    exc = Exception(f"http {status}")
    exc.status_code = status  # type: ignore[attr-defined]  # mirrors openai.APIStatusError
    return exc


def test_retrying_client_recovers_choices_none_typeerror() -> None:
    # Live finding: 200 + {"error": ...} parses to choices=None -> TypeError in autogen.
    inner = _FlakyInner([TypeError("'NoneType' object is not subscriptable")])
    client = p.RetryingChatClient(inner, pauses=(0,))
    assert asyncio.run(client.create()) == "ok"
    assert inner.calls == 2


def test_retrying_client_retries_transient_status() -> None:
    inner = _FlakyInner([_status_error(429), _status_error(503)])
    client = p.RetryingChatClient(inner, pauses=(0, 0))
    assert asyncio.run(client.create()) == "ok"
    assert inner.calls == 3


def test_retrying_client_raises_non_transient_immediately() -> None:
    inner = _FlakyInner([_status_error(404)])
    client = p.RetryingChatClient(inner, pauses=(0,))
    with pytest.raises(Exception, match="http 404"):
        asyncio.run(client.create())
    assert inner.calls == 1


def test_retrying_client_exhausts_attempts_and_reraises() -> None:
    inner = _FlakyInner([TypeError("x"), TypeError("x"), TypeError("x")])
    client = p.RetryingChatClient(inner, pauses=(0, 0))
    with pytest.raises(TypeError):
        asyncio.run(client.create())
    assert inner.calls == 3


def test_retrying_client_delegates_attributes() -> None:
    inner = _FlakyInner([])
    client = p.RetryingChatClient(inner, pauses=(0,))
    assert client.model_info == {"family": "fake"}


def test_retrying_client_backs_off_exponentially(monkeypatch: pytest.MonkeyPatch) -> None:
    # Rate-limit buckets need real waiting: default pauses grow up to 2 minutes (observed
    # 429s carry "reset after 2m 8s" — shorter schedules never left the closed bucket).
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(p.asyncio, "sleep", fake_sleep)
    inner = _FlakyInner([TypeError("x") for _ in range(5)])
    client = p.RetryingChatClient(inner)  # default pauses

    with pytest.raises(TypeError):
        asyncio.run(client.create())

    assert slept == [5.0, 20.0, 60.0, 120.0]
    assert inner.calls == 5


def test_retrying_client_uses_server_reset_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    # The 429 body says exactly when the bucket reopens — wait THAT (+2s) instead of the
    # fixed schedule.
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(p.asyncio, "sleep", fake_sleep)
    inner = _FlakyInner([_status_error(429)])
    inner.errors[0] = Exception("Rate limit exceeded (reset after 1m 4s)")
    inner.errors[0].status_code = 429  # type: ignore[attr-defined]
    client = p.RetryingChatClient(inner, pauses=(5.0,))

    assert asyncio.run(client.create()) == "ok"
    assert slept == [66.0]  # 64s hint + 2s margin


def test_retrying_client_retries_empty_content_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Reasoning models sometimes return EVERYTHING in hidden reasoning with empty content —
    # a whole graph run of empty agent replies (live finding). Empty content = flake.
    from types import SimpleNamespace

    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(p.asyncio, "sleep", fake_sleep)

    class _EmptyThenText:
        def __init__(self) -> None:
            self.calls = 0

        async def create(self, *args: object, **kwargs: object) -> Any:
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(content="   ")
            return SimpleNamespace(content="echte Antwort")

    inner = _EmptyThenText()
    client = p.RetryingChatClient(inner, pauses=(0.0,))

    result = asyncio.run(client.create())
    assert result.content == "echte Antwort"
    assert inner.calls == 2


def test_pause_from_error_parses_hints() -> None:
    assert p._pause_from_error("reset after 2m 8s", 5.0) == 130.0
    assert p._pause_from_error("reset after 45s", 5.0) == 47.0
    assert p._pause_from_error("reset after 10m 0s", 5.0) == p._RESET_PAUSE_CAP_S  # capped
    assert p._pause_from_error("no hint here", 5.0) == 5.0
