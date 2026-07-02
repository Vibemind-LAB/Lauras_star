"""Provider factory for the short-creator agents (Iteration 2/9).

The config resolution (env → :class:`AgentConfig`) and the client planning
(config → :class:`ClientSpec`) are PURE — tested here without autogen installed.
:func:`build_model_client` is the only autogen-touching function; its
missing-extra path and its construction wiring are tested with fake modules, so
these tests pass whether or not the optional ``autoshort`` extra is present.
"""

from __future__ import annotations

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
    cfg = p.resolve_from_env(
        {"LAURA_AGENT_PROVIDER": "9router", "LAURA_9ROUTER_API_KEY": "sk-9r"}
    )
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
    assert type(client).__name__ == "FakeOpenAI"
    kw = captured["openai"]
    assert kw["model"] == p.DEFAULT_ESCALATE_MODEL
    assert kw["base_url"] == p.DEFAULT_9ROUTER_BASE_URL
    assert kw["api_key"] == "sk-9r"
    assert "model_info" in kw
