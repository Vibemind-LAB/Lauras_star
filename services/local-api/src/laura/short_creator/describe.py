"""Optional local VLM backend that DESCRIBES frames (free text), for the Describer agent.

Mirrors ``analysis/vlm_ollama.py``'s Ollama HTTP pattern but returns a short description
instead of a transition verdict. Opt-in & local-first: :func:`resolve_describe_backend`
returns ``None`` unless ``LAURA_VLM_MODEL`` (or ``LAURA_VLM=1``) is set and the model is
locally available, so the backend runs without a model (the Describer degrades gracefully).
The real Ollama output is manual-to-verify (no model in CI); tests inject a fake backend.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3-vl:8b"
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


def _http_json(url: str, payload: dict[str, Any] | None = None, *, timeout: float = 120.0) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (local Ollama only)
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
                "num_ctx": 8192,
            },
        }
        try:
            data = _http_json(f"{self.host}/api/chat", payload, timeout=600.0)
            content = data.get("message", {}).get("content", "")
            return str(content).strip()
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            logger.warning("ollama describe failed: %s", exc)
            return ""  # never block the pipeline on a model hiccup


def resolve_describe_backend() -> DescribeBackend | None:
    """The configured describe backend, or ``None`` when the ``[vlm]`` model isn't set up."""
    if not (os.environ.get("LAURA_VLM_MODEL") or os.environ.get("LAURA_VLM")):
        return None
    backend = OllamaDescribeBackend()
    return backend if backend.available() else None
