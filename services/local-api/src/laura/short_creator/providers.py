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

import asyncio
import logging
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

logger = logging.getLogger(__name__)
_log = logger

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
    model_pool: tuple[str, ...] = ()
    # The raw LAURA_AGENT_PROVIDER value when it was not a known provider and the default was
    # substituted. Kept so preflight can say so: "openai" is a plausible typo for
    # "openai-compat", and silently running ollama instead is a wrong answer delivered quietly.
    unknown_provider: str | None = None
    unknown_escalate_provider: str | None = None


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


def _unknown_provider(raw: str | None) -> str | None:
    """The raw value when it was set but is not a provider — unset is a choice, not a mistake."""
    value = (raw or "").strip()
    if not value or value.lower() in _KNOWN_PROVIDERS:
        return None
    return value


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
    model_pool = _parse_model_pool(e.get("LAURA_AGENT_MODEL_POOL"), first=agent_model)
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
        model_pool=model_pool,
        unknown_provider=_unknown_provider(e.get("LAURA_AGENT_PROVIDER")),
        unknown_escalate_provider=_unknown_provider(e.get("LAURA_AGENT_ESCALATE_PROVIDER")),
    )


_PROVIDER_KEY_ENV: dict[str, str] = {
    "9router": "LAURA_9ROUTER_API_KEY",
    "openai-compat": "LAURA_AGENT_API_KEY",
}


def _provider_problem(provider: Provider, api_key: str | None, *, where: str) -> str | None:
    """The reason this provider cannot be reached, or None if it can."""
    env_name = _PROVIDER_KEY_ENV.get(provider)
    if env_name is None or api_key:  # ollama needs no credential
        return None
    return f"{where} provider {provider!r} needs {env_name}, which is not set"


def config_problems(config: AgentConfig) -> list[str]:
    """Everything about this config that makes a run impossible, named in the operator's terms.

    Live incident: a run started against ``openai-compat`` with no ``LAURA_AGENT_API_KEY``. It
    created a board, spent both escalation stages, and came back "Connection error." — a
    configuration mistake wearing a transport error's clothes, then invisible for 55 minutes.
    Checking first makes the distinction structural: a config that cannot reach a model never
    reaches a model call, so a transport error afterwards means what it says.

    Empty list means usable. It cannot promise the credential is VALID — only the provider knows
    that — but it catches every case where nothing was configured at all.
    """
    problems: list[str] = []
    if config.unknown_provider is not None:
        problems.append(
            f"LAURA_AGENT_PROVIDER={config.unknown_provider!r} is not a known provider "
            f"({', '.join(sorted(_KNOWN_PROVIDERS))}); fell back to {config.provider!r} — "
            "the run would use a different backend than configured"
        )
    if config.unknown_escalate_provider is not None and config.auto_escalate:
        problems.append(
            f"LAURA_AGENT_ESCALATE_PROVIDER={config.unknown_escalate_provider!r} is not a known "
            f"provider; escalation fell back to {config.escalate_provider!r}"
        )

    primary = _provider_problem(config.provider, _key_for(config, config.provider), where="agent")
    if primary is not None:
        problems.append(primary)

    # Stage B only matters when it can fire unasked. A manual escalation is the operator's
    # decision at the time, not a precondition of starting.
    if config.auto_escalate:
        escalation = _provider_problem(
            config.escalate_provider,
            _key_for(config, config.escalate_provider),
            where="escalation",
        )
        if escalation is not None:
            problems.append(escalation)

    if not config.agent_model.strip():
        problems.append("LAURA_AGENT_MODEL is empty")
    return problems


def config_warnings(config: AgentConfig) -> list[str]:
    """Advisory (non-fatal) findings about the resolved agent config.

    Parallel to :func:`config_problems`: problems block an enqueue (503), warnings are only
    surfaced — in the enqueue response and as one run-log line. Live incident 2026-07-20:
    three production runs silently ran their text agents on local qwen2.5:7b (the provider
    DEFAULT is ollama), which emits tool calls as prose and invents schemas; nothing said so
    anywhere. Local-first stays intact: warning, never a gate.
    """
    warnings: list[str] = []
    if config.provider == "ollama":
        warnings.append(
            f"text agents run on local ollama model {config.agent_model!r}: small local "
            "models are known to fail Magentic tool-calling (tool calls as prose, invented "
            "schemas); for production runs set LAURA_AGENT_PROVIDER=openai-compat and a "
            "hosted LAURA_AGENT_MODEL"
        )
    return warnings


def _key_for(config: AgentConfig, provider: Provider) -> str | None:
    if provider == "9router":
        return config.nine_router_api_key
    if provider == "openai-compat":
        return config.openai_api_key
    return None


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


# Free-tier gateways intermittently answer 200 with an error body and NO ``choices`` — the
# OpenAI SDK parses that into ``ChatCompletion(choices=None)`` and autogen crashes on
# ``choices[0]`` (TypeError). One such flake killed a whole team run (live finding).
# Exponential backoff: free-tier RATE limits need real waiting — observed 429s carry
# "reset after 2m 8s", so the ladder must be able to outlast ~2-minute buckets.
_RETRY_PAUSES: tuple[float, ...] = (5.0, 20.0, 60.0, 120.0)
_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
_RESET_HINT = re.compile(r"reset after (?:(\d+)m\s*)?(\d+)s")
_RESET_PAUSE_CAP_S = 150.0


def _parse_model_pool(raw: str | None, first: str) -> tuple[str, ...]:
    """Comma-separated model pool; the active model always leads, duplicates dropped."""
    names = [first] + [part.strip() for part in (raw or "").split(",")]
    out: list[str] = []
    for name in names:
        if name and name not in out:
            out.append(name)
    return tuple(out)


def _is_flaky(exc: BaseException) -> bool:
    """The failure classes RetryingChatClient retries — i.e. 'this model/day is bad',
    not 'this request is wrong'. Used by the pool to decide whether to fail over."""
    if isinstance(exc, TypeError):
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status in _RETRYABLE_STATUS:
        return True
    return isinstance(exc, RuntimeError) and "empty completion content" in str(exc)


class RotatingChatClient:
    """Sticky failover across a pool of per-model clients.

    Each pool entry is expected to be a RetryingChatClient (its own retries
    exhausted before an exception reaches us).  On a flaky-class failure the
    pool advances to the next model and retries the SAME request; the index is
    sticky for the rest of the process (free-tier serving variance is per
    model per day — a bad model stays skipped).  Non-flaky errors re-raise
    immediately; an exhausted pool re-raises the last flaky error.
    """

    def __init__(self, clients: Sequence[Any]) -> None:
        if not clients:
            raise ValueError("model pool must not be empty")
        self._clients = list(clients)
        self._index = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._clients[self._index], name)

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        while True:
            try:
                return await self._clients[self._index].create(*args, **kwargs)
            except Exception as exc:  # classify, then advance or re-raise
                if not _is_flaky(exc) or self._index >= len(self._clients) - 1:
                    raise
                self._index += 1
                _log.warning(
                    "model pool: advancing to client %d/%d after flaky failure: %s",
                    self._index + 1,
                    len(self._clients),
                    exc,
                )


def _pause_from_error(error_text: str, fallback: float) -> float:
    """The pause before the next retry: the server's own reset hint when present.

    OpenRouter 429 bodies say "reset after 2m 8s" — waiting exactly that (+2s, capped)
    beats any fixed schedule. Without a hint, *fallback* (the exponential step) is used.
    """
    m = _RESET_HINT.search(error_text)
    if m is None:
        return fallback
    minutes = int(m.group(1) or 0)
    seconds = int(m.group(2))
    return min(float(minutes * 60 + seconds) + 2.0, _RESET_PAUSE_CAP_S)


class RetryingChatClient:
    """Delegating wrapper around a ChatCompletionClient that retries transient failures.

    Retries ``create()`` on: TypeError (the choices=None flake above) and HTTP 408/429/5xx
    (via the exception's ``status_code``), pausing per ``pauses`` between attempts
    (exponential by default). Every retry is logged so rate-limit storms are visible in the
    backend log instead of surfacing as a bare TypeError. Everything else raises
    immediately. All other attributes delegate to the wrapped client, so it stays a drop-in
    for AssistantAgent.
    """

    def __init__(self, inner: Any, *, pauses: tuple[float, ...] = _RETRY_PAUSES) -> None:
        self._inner = inner
        self._pauses = pauses

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        attempts = len(self._pauses) + 1
        last: BaseException | None = None
        for attempt in range(attempts):
            if attempt:
                pause = _pause_from_error(str(last), self._pauses[attempt - 1])
                logger.warning(
                    "model call flaked (attempt %d/%d): %s: %s — retrying in %.0fs",
                    attempt,
                    attempts,
                    type(last).__name__,
                    str(last)[:200],
                    pause,
                )
                await asyncio.sleep(pause)
            try:
                result = await self._inner.create(*args, **kwargs)
                content = getattr(result, "content", None)
                if isinstance(content, str) and not content.strip():
                    # Reasoning models sometimes put EVERYTHING into hidden reasoning and
                    # return empty content (live finding: a whole graph run of empty agent
                    # replies — no VOICEOVER, no EDITED, weak). Treat as a flake.
                    last = RuntimeError("empty completion content (reasoning-only reply)")
                    continue
                return result
            except TypeError as exc:  # choices=None error body from a flaky free tier
                last = exc
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                if status not in _RETRYABLE_STATUS:
                    raise
                last = exc
        assert last is not None  # attempts >= 1 → a failure landed here
        logger.warning("model call failed after %d attempts: %s", attempts, str(last)[:200])
        raise last

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def build_model_client(
    config: AgentConfig, *, role: Role = "agent", stage: Stage = "A"
) -> ChatCompletionClient:
    """Build the AutoGen model client for *role*/*stage*.

    Lazily imports the optional ``autoshort`` extra; raises a clear
    :class:`RuntimeError` (not ``ImportError``) if it is not installed. Remote
    (OpenAI-compatible) clients are wrapped in :class:`RetryingChatClient`.
    At stage A with a pool of multiple models, wraps all per-model clients
    in a :class:`RotatingChatClient` for sticky failover.
    """
    spec = plan_client(config, role=role, stage=stage)
    try:
        if spec.kind == "ollama":
            from autogen_ext.models.ollama import OllamaChatCompletionClient

            return cast("ChatCompletionClient", OllamaChatCompletionClient(model=spec.model))

        from autogen_core.models import ModelInfo
        from autogen_ext.models.openai import OpenAIChatCompletionClient

        kwargs: dict[str, Any] = {
            "api_key": spec.api_key or "not-needed",
            "model_info": ModelInfo(
                vision=False,
                function_calling=True,
                json_output=True,
                family="unknown",
                structured_output=True,
            ),
        }
        if spec.base_url is not None:
            kwargs["base_url"] = spec.base_url

        pool = (
            config.model_pool
            if stage == "A" and role == "agent" and len(config.model_pool) > 1
            else (spec.model,)
        )
        clients = []
        for pool_model in pool:
            client_kwargs = dict(kwargs)
            client_kwargs["model"] = pool_model
            # gpt-5.6-family models refuse function tools on /v1/chat/completions unless
            # reasoning_effort is explicitly "none" (live 400, 2026-08-07: "To use function
            # tools, use /v1/responses or set reasoning_effort to 'none'"). Sent only for
            # models that demand it — other models would reject the unknown parameter.
            if pool_model.startswith("gpt-5.6"):
                client_kwargs["reasoning_effort"] = "none"
            clients.append(RetryingChatClient(OpenAIChatCompletionClient(**client_kwargs)))
        if len(clients) == 1:
            return cast("ChatCompletionClient", clients[0])
        return cast("ChatCompletionClient", RotatingChatClient(clients))
    except ImportError as exc:  # optional extra missing
        raise RuntimeError(
            "The short-creator needs the optional 'autoshort' extra. "
            "Install it with: uv sync --extra autoshort"
        ) from exc
