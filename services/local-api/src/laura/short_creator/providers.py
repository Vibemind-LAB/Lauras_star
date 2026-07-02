"""Env-driven model-client factory for the short-creator agents.

Everything is configured via env (see the design spec). Two concerns are kept
separate so the decision logic is pure and testable without autogen:

* :func:`resolve_from_env` — env → :class:`AgentConfig` (pure).
* :func:`plan_client` — config + role/stage → :class:`ClientSpec` (pure).
* :func:`build_model_client` — the ONLY autogen-touching function; lazily
  imports the optional ``autoshort`` extra and constructs the real client.

The module imports nothing from autogen at load time, so the backend starts
without the extra installed.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:  # annotations only — never imported at runtime
    from autogen_core.models import ChatCompletionClient

Provider = Literal["ollama", "9router", "openai-compat"]
Orchestration = Literal["magentic", "graph"]
Role = Literal["agent", "orchestrator"]
Stage = Literal["A", "B"]

DEFAULT_AGENT_MODEL = "qwen2.5"
DEFAULT_ESCALATE_PROVIDER: Provider = "9router"
DEFAULT_ESCALATE_MODEL = "cc/claude-sonnet-4-5"
DEFAULT_9ROUTER_BASE_URL = "http://localhost:20128/v1"
DEFAULT_QA_MAX_ROUNDS = 2

_KNOWN_PROVIDERS: frozenset[str] = frozenset({"ollama", "9router", "openai-compat"})
_TRUE: frozenset[str] = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class AgentConfig:
    """Fully-resolved short-creator config. Pure data; holds no autogen types."""

    provider: Provider
    agent_model: str
    orchestrator_model: str
    orchestration: Orchestration
    escalate_provider: Provider
    escalate_model: str
    auto_escalate: bool
    qa_max_rounds: int
    nine_router_base_url: str
    nine_router_api_key: str | None
    openai_base_url: str | None
    openai_api_key: str | None


@dataclass(frozen=True)
class ClientSpec:
    """Provider-agnostic description of one model client to build."""

    kind: Literal["ollama", "openai"]
    model: str
    base_url: str | None
    api_key: str | None


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _provider(raw: str | None, default: Provider) -> Provider:
    value = (raw or "").strip().lower()
    if value in _KNOWN_PROVIDERS:
        return value  # type: ignore[return-value]  # narrowed by membership check
    return default


def _positive_int(raw: str | None, default: int) -> int:
    try:
        value = int((raw or "").strip())
    except ValueError:
        return default
    return value if value >= 1 else default


def resolve_from_env(env: Mapping[str, str] | None = None) -> AgentConfig:
    """Read the full config from *env* (defaults to ``os.environ``).

    Zero env set → local (ollama), free, manual escalation.
    """
    e = os.environ if env is None else env
    agent_model = _clean(e.get("LAURA_AGENT_MODEL")) or DEFAULT_AGENT_MODEL
    orchestration_raw = (e.get("LAURA_AGENT_ORCHESTRATION") or "").strip().lower()
    return AgentConfig(
        provider=_provider(e.get("LAURA_AGENT_PROVIDER"), "ollama"),
        agent_model=agent_model,
        orchestrator_model=_clean(e.get("LAURA_ORCHESTRATOR_MODEL")) or agent_model,
        orchestration="graph" if orchestration_raw == "graph" else "magentic",
        escalate_provider=_provider(
            e.get("LAURA_AGENT_ESCALATE_PROVIDER"), DEFAULT_ESCALATE_PROVIDER
        ),
        escalate_model=_clean(e.get("LAURA_AGENT_ESCALATE_MODEL")) or DEFAULT_ESCALATE_MODEL,
        auto_escalate=(e.get("LAURA_AGENT_AUTO_ESCALATE") or "").strip().lower() in _TRUE,
        qa_max_rounds=_positive_int(e.get("LAURA_AGENT_QA_MAX_ROUNDS"), DEFAULT_QA_MAX_ROUNDS),
        nine_router_base_url=_clean(e.get("LAURA_9ROUTER_BASE_URL")) or DEFAULT_9ROUTER_BASE_URL,
        nine_router_api_key=_clean(e.get("LAURA_9ROUTER_API_KEY")),
        openai_base_url=_clean(e.get("LAURA_AGENT_BASE_URL")),
        openai_api_key=_clean(e.get("LAURA_AGENT_API_KEY")),
    )


def plan_client(config: AgentConfig, *, role: Role = "agent", stage: Stage = "A") -> ClientSpec:
    """Decide which client to build for *role* at escalation *stage* (pure).

    Stage A uses the primary provider (per-role model); stage B escalates the
    whole team to the escalate provider + model.
    """
    if stage == "B":
        provider: Provider = config.escalate_provider
        model = config.escalate_model
    else:
        provider = config.provider
        model = config.orchestrator_model if role == "orchestrator" else config.agent_model

    if provider == "ollama":
        return ClientSpec(kind="ollama", model=model, base_url=None, api_key=None)
    if provider == "9router":
        return ClientSpec(
            kind="openai",
            model=model,
            base_url=config.nine_router_base_url,
            api_key=config.nine_router_api_key,
        )
    return ClientSpec(  # openai-compat
        kind="openai", model=model, base_url=config.openai_base_url, api_key=config.openai_api_key
    )


def build_model_client(
    config: AgentConfig, *, role: Role = "agent", stage: Stage = "A"
) -> ChatCompletionClient:
    """Build the AutoGen model client for *role*/*stage*.

    Lazily imports the optional ``autoshort`` extra; raises a clear
    :class:`RuntimeError` (not ``ImportError``) if it is not installed.
    """
    spec = plan_client(config, role=role, stage=stage)
    try:
        if spec.kind == "ollama":
            from autogen_ext.models.ollama import OllamaChatCompletionClient

            return OllamaChatCompletionClient(model=spec.model)

        from autogen_core.models import ModelInfo
        from autogen_ext.models.openai import OpenAIChatCompletionClient

        return OpenAIChatCompletionClient(
            model=spec.model,
            base_url=spec.base_url,
            api_key=spec.api_key or "not-needed",
            model_info=ModelInfo(
                vision=False,
                function_calling=True,
                json_output=True,
                family="unknown",
                structured_output=True,
            ),
        )
    except ImportError as exc:  # optional extra missing
        raise RuntimeError(
            "The short-creator needs the optional 'autoshort' extra. "
            "Install it with: uv sync --extra autoshort"
        ) from exc
