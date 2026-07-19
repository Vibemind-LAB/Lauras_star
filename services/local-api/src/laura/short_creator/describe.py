"""Optional VLM backends that DESCRIBE frames (free text), for the Describer agent.

Mirrors ``analysis/vlm_ollama.py``'s stdlib-HTTP pattern but returns a short description
instead of a transition verdict. Opt-in & local-first: :func:`resolve_describe_backend`
returns ``None`` unless a backend is configured, so the pipeline runs without a model (the
Describer degrades gracefully). Two backends:

* Ollama (default): gated on ``LAURA_VLM_MODEL``/``LAURA_VLM`` + local model availability.
* OpenRouter: ``LAURA_VLM_PROVIDER=openrouter`` + ``LAURA_OPENROUTER_API_KEY`` — describes
  via OpenRouter's OpenAI-compatible API (free vision models exist), keeping the local GPU
  free for rendering (a 15GB VLM + a 7B text model don't fit a 12GB card together).

Real model output is manual-to-verify (no model/key in CI); tests fake the HTTP layer.
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# Injectable for tests; the OpenRouter retry pauses with this sleeper.
_sleep: Callable[[float], None] = time.sleep
_OPENROUTER_ATTEMPTS = 2
_OPENROUTER_RETRY_PAUSE_S = 2.0

DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3-vl:8b"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# A free vision model (OpenRouter's free tier rotates — override via LAURA_VLM_MODEL).
DEFAULT_OPENROUTER_MODEL = "nvidia/nemotron-nano-12b-v2-vl:free"
DESCRIBE_PROMPT = (
    "You are a film editor's assistant. In ONE concise sentence, describe what is visibly "
    "happening in these frames: the main subject, the action, and the setting. Be concrete; "
    "do not speculate beyond what is shown."
)


@runtime_checkable
class DescribeBackend(Protocol):
    """A model (or fake) that turns frame JPEGs into a short textual description."""

    def available(self) -> bool: ...
    def describe(self, frames: list[bytes], prompt: str) -> str: ...


def _http_json(
    url: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 120.0,
    headers: dict[str, str] | None = None,
) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    all_headers = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=all_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed hosts only)
        return json.loads(resp.read().decode("utf-8"))


class OllamaDescribeBackend:
    """DescribeBackend over a local Ollama server (default model ``qwen3-vl:8b``)."""

    def __init__(self, *, host: str | None = None, model: str | None = None) -> None:
        self.host = (host or os.environ.get("LAURA_OLLAMA_HOST") or DEFAULT_HOST).rstrip("/")
        self.model = model or os.environ.get("LAURA_VLM_MODEL") or DEFAULT_MODEL

    def _tags(self) -> list[dict[str, Any]]:
        try:
            data = _http_json(f"{self.host}/api/tags", timeout=10.0)
        except (urllib.error.URLError, OSError, ValueError):
            return []
        models = data.get("models") if isinstance(data, dict) else None
        return models if isinstance(models, list) else []

    def available(self) -> bool:
        return any(str(m.get("name")) == self.model for m in self._tags())

    def describe(self, frames: list[bytes], prompt: str) -> str:
        images = [base64.b64encode(f).decode("ascii") for f in frames]
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt, "images": images}],
            "stream": False,
            "keep_alive": "10m",
            "options": {
                "temperature": 0,
                "top_k": 1,
                "top_p": 1.0,
                "seed": 0,
                "num_predict": 200,
                # 8192 was NOT enough for the real workload: three frames plus the review
                # prompt overflow it, and Ollama's context handling for vision models does not
                # truncate — it crashes the runner (GGML_ASSERT ne[2]*4 == ne[0], HTTP 500).
                # Reproduced deterministically: same frames + short prompt fine, + the 936-char
                # review prompt crash, num_ctx 16384 fine. Two whole runs degraded every review
                # on this before the error body was captured.
                "num_ctx": 16384,
            },
        }
        try:
            return self._chat(payload)
        except urllib.error.HTTPError as exc:
            # A local 5xx means "this loaded instance is broken", and the observed cure is a
            # fresh load: a run once got 500 for EVERY image describe because the resident
            # instance had been loaded by a text request — all six reviews degraded, no
            # storyline, dead run. The moment the model was unloaded and reloaded by an image
            # request it answered correctly. So: force-unload, retry once. 4xx stays final —
            # a request the server rejects does not get better on a fresh load.
            if exc.code < 500:
                logger.warning("ollama describe failed: %s", exc)
                return ""
            logger.warning("ollama describe got %s — unloading the model and retrying", exc)
            # Even a failing unload does not cancel the retry — it can only help.
            with contextlib.suppress(urllib.error.URLError, OSError, ValueError):
                _http_json(
                    f"{self.host}/api/chat",
                    {"model": self.model, "messages": [], "keep_alive": 0},
                    timeout=60.0,
                )
            try:
                return self._chat(payload)
            except (urllib.error.URLError, OSError, ValueError, KeyError) as retry_exc:
                logger.warning("ollama describe failed after reload: %s", retry_exc)
                return ""
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            logger.warning("ollama describe failed: %s", exc)
            return ""  # never block the pipeline on a model hiccup

    def _chat(self, payload: dict[str, Any]) -> str:
        data = _http_json(f"{self.host}/api/chat", payload, timeout=600.0)
        content = data.get("message", {}).get("content", "")
        return str(content).strip()


class OpenRouterDescribeBackend:
    """DescribeBackend over OpenRouter's OpenAI-compatible API (free vision models exist).

    Frames go as ``image_url`` data URIs in one user message; deterministic-ish settings
    (temperature 0). Failures return ``""`` — the Describer must never block the pipeline.
    Supports multiple models with sticky failover: iterate the pool, and prefer the last
    successful model on the next call.
    """

    def __init__(
        self, *, api_key: str, model: str | None = None, models: Sequence[str] | None = None
    ) -> None:
        self.api_key = api_key

        # Always resolve the primary model unconditionally (model kwarg → env → default).
        picked = model or os.environ.get("LAURA_VLM_MODEL") or DEFAULT_OPENROUTER_MODEL
        picked_model = picked if "/" in picked else DEFAULT_OPENROUTER_MODEL

        # Build ordered list: primary model first, then pool entries (deduped in order).
        ordered = [picked_model, *(models or [])]
        deduped: list[str] = []
        for name in ordered:
            if name and name not in deduped:
                deduped.append(name)

        self._models = deduped
        self._preferred = 0  # sticky index: start at the first model

    @property
    def model(self) -> str:
        """The current model (for backward compatibility; read-only)."""
        return self._models[self._preferred] if self._models else DEFAULT_OPENROUTER_MODEL

    def available(self) -> bool:
        return bool(self.api_key)

    def _attempt(self, model: str, frames: list[bytes], prompt: str) -> str:
        """Attempt a single describe call with the given model.

        Returns "" on any failure (network, API error, or empty content).
        """
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for frame in frames:
            b64 = base64.b64encode(frame).decode("ascii")
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
            )
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            # Reasoning VLMs burn budget on hidden reasoning BEFORE the answer: 200 tokens
            # produced empty content with ok=True (live finding) — give them headroom.
            "max_tokens": 1024,
            "temperature": 0,
        }
        # Free-tier endpoints are flaky (observed live: 200 + upstream 504 error body on one
        # call, a clean answer on the next) — retry once before degrading to "".
        for attempt in range(_OPENROUTER_ATTEMPTS):
            if attempt:
                _sleep(_OPENROUTER_RETRY_PAUSE_S)
            try:
                data = _http_json(
                    OPENROUTER_URL,
                    payload,
                    timeout=120.0,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
            except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
                logger.warning("openrouter describe failed: %s", exc)
                continue
            # OpenRouter reports failures as 200 + {"error": ...} — surface them in the log
            # instead of silently degrading (free-tier rate limits look like this).
            if isinstance(data, dict) and data.get("error"):
                logger.warning("openrouter describe error: %s", str(data["error"])[:300])
                continue
            choices = data.get("choices") if isinstance(data, dict) else None
            if not isinstance(choices, list) or not choices:
                continue
            message = choices[0].get("message") or {}
            text = str(message.get("content") or "").strip()
            if text:
                return text
            logger.warning(
                "openrouter describe empty content (finish_reason=%s model=%s)",
                choices[0].get("finish_reason"),
                model,
            )
        return ""  # never block the pipeline on a model hiccup

    def describe(self, frames: list[bytes], prompt: str) -> str:
        """Describe frames using the model pool with sticky failover.

        Iterates through models starting from the last successful one.
        On success, sticks to that model for the next call.
        Returns "" if all models fail.
        """
        for offset in range(len(self._models)):
            idx = (self._preferred + offset) % len(self._models)
            model = self._models[idx]
            result = self._attempt(model, frames, prompt)
            if result:  # non-empty success
                self._preferred = idx  # stick to this model
                return result
        return ""  # all models failed


def resolve_describe_backend() -> DescribeBackend | None:
    """The configured describe backend, or ``None`` when no VLM is set up.

    ``LAURA_VLM_PROVIDER=openrouter`` (+ ``LAURA_OPENROUTER_API_KEY``) routes descriptions to
    OpenRouter — the local GPU stays free for rendering. Supports model failover via
    ``LAURA_VLM_MODEL_POOL`` (comma-separated list; the primary model or env default leads).
    Default stays local-first: Ollama, gated on ``LAURA_VLM_MODEL``/``LAURA_VLM`` and local
    model availability.
    """
    provider = (os.environ.get("LAURA_VLM_PROVIDER") or "").strip().lower()
    if provider == "openrouter":
        api_key = (os.environ.get("LAURA_OPENROUTER_API_KEY") or "").strip()
        if not api_key:
            return None
        # Parse optional model pool (comma-separated list)
        pool_raw = os.environ.get("LAURA_VLM_MODEL_POOL")
        models = None
        if pool_raw:
            models = [p.strip() for p in pool_raw.split(",") if p.strip()]
        return OpenRouterDescribeBackend(api_key=api_key, models=models)
    if not (os.environ.get("LAURA_VLM_MODEL") or os.environ.get("LAURA_VLM")):
        return None
    backend = OllamaDescribeBackend()
    return backend if backend.available() else None
