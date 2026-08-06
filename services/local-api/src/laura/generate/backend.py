"""Video-generation backend (Axis 2, Slice 1) — the injectable model boundary.

The real model (ComfyUI / LTX-Video, local RTX) plugs in behind ``VideoGenerateBackend`` later
(Slice 2). v1 ships :class:`StubVideoGenerateBackend`, a model-free placeholder that renders a
solid-colour clip of the requested length via ffmpeg — so the pipeline works end-to-end without
any GPU or model. Tests inject their own fake backend, so ffmpeg is never required for TDD.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..ingest.ffmpeg import run_ffmpeg
from .comfyui_client import ComfyUIClient

logger = logging.getLogger(__name__)


@runtime_checkable
class VideoGenerateBackend(Protocol):
    """Produce a video file at *out_path* for *prompt* of ``duration_frames`` frames."""

    def generate(
        self, *, prompt: str, out_path: Path, duration_frames: int, fps_num: int, fps_den: int
    ) -> None: ...


class StubVideoGenerateBackend:
    """Model-free placeholder: a solid dark-grey clip of the requested length (no GPU, no model).

    Lets the generate → asset pipeline run end-to-end before a real text-to-video backend is
    wired. The *prompt* is ignored beyond the asset's display name.
    """

    def generate(
        self, *, prompt: str, out_path: Path, duration_frames: int, fps_num: int, fps_den: int
    ) -> None:
        fps = (fps_num / fps_den) if fps_den else 30.0
        duration_s = max(duration_frames, 1) / (fps or 30.0)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        run_ffmpeg(
            [
                "-f", "lavfi",
                "-i", f"color=c=0x202020:s=1280x720:r={fps_num}/{fps_den}:d={duration_s:.3f}",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                str(out_path),
            ]
        )


def _inject(node: Any, prompt: str, frames: int) -> Any:
    """Deep-replace workflow placeholders: ``%FRAMES%`` (whole string) → int, ``%PROMPT%``
    (substring) → *prompt*. Everything else is passed through unchanged."""
    if isinstance(node, dict):
        return {k: _inject(v, prompt, frames) for k, v in node.items()}
    if isinstance(node, list):
        return [_inject(v, prompt, frames) for v in node]
    if isinstance(node, str):
        if node == "%FRAMES%":
            return frames
        return node.replace("%PROMPT%", prompt)
    return node


def _first_output_file(outputs: dict[str, Any]) -> tuple[str, str, str]:
    """First downloadable file across the workflow's output nodes → (filename, subfolder, type)."""
    for node_out in outputs.values():
        if isinstance(node_out, dict):
            for key in ("videos", "gifs", "images"):
                files = node_out.get(key)
                if files:
                    entry = files[0]
                    return (
                        str(entry["filename"]),
                        str(entry.get("subfolder", "")),
                        str(entry.get("type", "output")),
                    )
    raise RuntimeError("ComfyUI result has no downloadable output file")


class ComfyUIVideoGenerateBackend:
    """Run a text-to-video (LTX) workflow on a local ComfyUI and download the result.

    The workflow template is supplied by the user (their exact LTX graph, API format); this only
    injects the prompt / frame-count via placeholders, submits, waits, and downloads — so it works
    with any ComfyUI workflow. The real round-trip is manual-to-verify (needs a running ComfyUI).
    """

    def __init__(
        self,
        client: ComfyUIClient,
        workflow_template: dict[str, Any],
        *,
        wait_timeout: float = 600.0,
    ) -> None:
        self._client = client
        self._template = workflow_template
        self._wait_timeout = wait_timeout

    def generate(
        self, *, prompt: str, out_path: Path, duration_frames: int, fps_num: int, fps_den: int
    ) -> None:
        workflow = _inject(self._template, prompt, duration_frames)
        prompt_id = self._client.submit(workflow)
        outputs = self._client.wait(prompt_id, timeout=self._wait_timeout)
        filename, subfolder, type_ = _first_output_file(outputs)
        self._client.download(
            filename=filename, subfolder=subfolder, type_=type_, out_path=out_path
        )


def resolve_video_generate_backend() -> VideoGenerateBackend:
    """The configured backend: ComfyUI when ``LAURA_COMFYUI_URL`` + ``LAURA_COMFYUI_WORKFLOW`` are
    set and readable, else the model-free stub. Never hard-fails — a missing/unreadable workflow
    falls back to the stub with a warning, so the pipeline always runs."""
    url = os.environ.get("LAURA_COMFYUI_URL")
    if not url:
        return StubVideoGenerateBackend()
    workflow_path = os.environ.get("LAURA_COMFYUI_WORKFLOW")
    if not workflow_path or not Path(workflow_path).is_file():
        logger.warning(
            "LAURA_COMFYUI_URL is set but LAURA_COMFYUI_WORKFLOW is missing/unreadable; "
            "falling back to the stub video backend."
        )
        return StubVideoGenerateBackend()
    template: dict[str, Any] = json.loads(Path(workflow_path).read_text(encoding="utf-8"))
    return ComfyUIVideoGenerateBackend(ComfyUIClient(url), template)
