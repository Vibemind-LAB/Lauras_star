"""OllamaVlmBackend — a real local VLM (Qwen3-VL via Ollama) for transition review (Plan C).

Optional ``[vlm]`` path: talks to a local Ollama server over HTTP (stdlib urllib — no new
dependency). Deterministic by construction (``temperature=0, top_k=1, top_p=1, seed=0`` + forced
JSON schema), so a re-review of the same frames yields the same verdict; the model's content digest
pins the cache identity (spec §3/§4.5). The JSON→verdict parse is pure and defensively coerces the
model output to Laura's enums, so a malformed reply degrades to a safe ``smooth``/``none`` verdict
rather than raising.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

from .transition_review import (
    FixKind,
    SmoothnessLabel,
    SuggestedFix,
    TransitionStyle,
    TransitionVerdict,
)

logger = logging.getLogger(__name__)

DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3-vl:8b"

_LABELS: tuple[SmoothnessLabel, ...] = ("smooth", "jump_cut", "hard_jolt", "motion_break")
_FIX_KINDS: tuple[FixKind, ...] = ("none", "resnap", "transition")
_STYLES: tuple[TransitionStyle, ...] = ("crossfade", "fade")

_REVIEW_PROMPT = (
    "You are a film editor judging a single CUT between two video clips. The first images are the "
    "last frames of clip A; the rest are the first frames of clip B. Judge how FLUID the "
    "transition feels (1.0 = seamless, 0.0 = jarring). If A and B are the same continuous shot "
    "with a time "
    "jump (a 'dead-air jump cut'), prefer a short crossfade. Reply ONLY as JSON."
)

# JSON schema handed to Ollama's `format` so the reply is structured + deterministic.
_VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "smoothness": {"type": "number"},
        "label": {"type": "string", "enum": list(_LABELS)},
        "reason": {"type": "string"},
        "suggested_fix": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": list(_FIX_KINDS)},
                "resnap_delta_frames": {"type": "integer"},
                "transition_style": {"type": "string", "enum": list(_STYLES)},
                "transition_frames": {"type": "integer"},
            },
            "required": ["kind"],
        },
    },
    "required": ["smoothness", "label", "reason", "suggested_fix"],
}


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_verdict(obj: dict[str, Any]) -> TransitionVerdict:
    """Coerce a model JSON object to a TransitionVerdict (clamped + enum-coerced; never raises)."""
    smoothness = min(1.0, max(0.0, _as_float(obj.get("smoothness"), 0.5)))
    raw_label = obj.get("label")
    label: SmoothnessLabel = raw_label if raw_label in _LABELS else "smooth"
    reason = str(obj.get("reason") or "")

    fix_obj = obj.get("suggested_fix")
    fix_obj = fix_obj if isinstance(fix_obj, dict) else {}
    raw_kind = fix_obj.get("kind")
    kind: FixKind = raw_kind if raw_kind in _FIX_KINDS else "none"
    raw_style = fix_obj.get("transition_style")
    style: TransitionStyle = raw_style if raw_style in _STYLES else "crossfade"
    fix = SuggestedFix(
        kind=kind,
        resnap_delta_frames=_as_int(fix_obj.get("resnap_delta_frames"), 0),
        transition_style=style,
        transition_frames=_as_int(fix_obj.get("transition_frames"), 0),
    )
    return TransitionVerdict(smoothness=smoothness, label=label, reason=reason, suggested_fix=fix)


def _http_json(url: str, payload: dict[str, Any] | None = None, *, timeout: float = 120.0) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (local Ollama only)
        return json.loads(resp.read().decode("utf-8"))


class OllamaVlmBackend:
    """VlmBackend over a local Ollama server (default model ``qwen3-vl:8b``)."""

    def __init__(self, *, host: str | None = None, model: str | None = None) -> None:
        self.host = (host or os.environ.get("LAURA_OLLAMA_HOST") or DEFAULT_HOST).rstrip("/")
        self.model = model or os.environ.get("LAURA_VLM_MODEL") or DEFAULT_MODEL
        self._digest: str | None = None

    def _tags(self) -> list[dict[str, Any]]:
        try:
            data = _http_json(f"{self.host}/api/tags", timeout=10.0)
        except (urllib.error.URLError, OSError, ValueError):
            return []
        models = data.get("models") if isinstance(data, dict) else None
        return models if isinstance(models, list) else []

    def available(self) -> bool:
        return any(str(m.get("name")) == self.model for m in self._tags())

    def model_id(self) -> str:
        return self.model

    def model_digest(self) -> str:
        if self._digest is None:
            for m in self._tags():
                if str(m.get("name")) == self.model:
                    self._digest = str(m.get("digest") or self.model)
                    break
            self._digest = self._digest or self.model
        return self._digest

    def review(self, frames: list[bytes], meta: dict[str, object]) -> TransitionVerdict:
        images = [base64.b64encode(f).decode("ascii") for f in frames]
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": _REVIEW_PROMPT, "images": images}],
            "stream": False,
            "format": _VERDICT_SCHEMA,
            "options": {"temperature": 0, "top_k": 1, "top_p": 1.0, "seed": 0},
        }
        try:
            data = _http_json(f"{self.host}/api/chat", payload)
            content = data.get("message", {}).get("content", "{}")
            return _parse_verdict(json.loads(content))
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            logger.warning("ollama review failed: %s", exc)
            # Safe degraded verdict — never block the pipeline on a model hiccup.
            return _parse_verdict({})
