"""Video-generation backend (Axis 2, Slice 1) — the injectable model boundary.

The real model (ComfyUI / LTX-Video, local RTX) plugs in behind ``VideoGenerateBackend`` later
(Slice 2). v1 ships :class:`StubVideoGenerateBackend`, a model-free placeholder that renders a
solid-colour clip of the requested length via ffmpeg — so the pipeline works end-to-end without
any GPU or model. Tests inject their own fake backend, so ffmpeg is never required for TDD.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ..ingest.ffmpeg import run_ffmpeg


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
