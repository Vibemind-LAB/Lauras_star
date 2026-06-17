"""CFR editorial proxy + poster frame generation (FFmpeg).

Proxies are all-intra (``-g 1``) for fast scrubbing and constant frame rate so the
player can seek frame-accurately (ADR-0002/0005). Target height is computed in
Python (never upscale) to avoid fragile filter expressions.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from ..gpu import nvenc_available
from .ffmpeg import run_ffmpeg

logger = logging.getLogger(__name__)

PROXY_MAX_HEIGHT = 1080


def proxy_target_height(src_height: int, max_height: int = PROXY_MAX_HEIGHT) -> int:
    """Even target height that never upscales beyond the source."""
    h = min(max_height, src_height)
    return h - (h % 2)


def _video_encoder_args() -> tuple[str, list[str]]:
    """(name, ffmpeg video-codec args). NVENC keeps full resolution but encodes on the
    GPU (the slow CPU step otherwise). Override via LAURA_PROXY_ENCODER=auto|nvenc|libx264."""
    choice = (os.environ.get("LAURA_PROXY_ENCODER") or "auto").strip().lower()
    use_nvenc = choice == "nvenc" or (choice == "auto" and nvenc_available())
    if use_nvenc:
        return "h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "23"]
    return "libx264", ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]


def build_proxy(
    src: Path | str,
    dest: Path,
    *,
    src_height: int,
    rate_num: int | None = None,
    rate_den: int | None = None,
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    target_h = proxy_target_height(src_height)
    enc_name, venc = _video_encoder_args()
    logger.info("proxy encoder=%s target_h=%d", enc_name, target_h)
    args = ["-i", str(src), "-vf", f"scale=-2:{target_h}"]
    if rate_num and rate_den:
        args += ["-r", f"{rate_num}/{rate_den}"]  # force CFR
    args += [
        *venc,
        "-g", "1",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        str(dest),
    ]
    run_ffmpeg(args)


def build_poster(src: Path | str, dest: Path) -> None:
    """Grab a single representative frame as a JPEG poster."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-i", str(src), "-frames:v", "1", "-q:v", "3", str(dest)])


def build_thumbnail(src: Path | str, dest: Path, *, at_seconds: float) -> None:
    """Grab a single JPEG frame at ``at_seconds`` (fast input seek)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        ["-ss", f"{max(0.0, at_seconds):.3f}", "-i", str(src), "-frames:v", "1",
         "-q:v", "5", str(dest)]
    )
