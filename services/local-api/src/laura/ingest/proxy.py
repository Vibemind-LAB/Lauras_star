"""CFR editorial proxy + poster frame generation (FFmpeg).

Proxies are all-intra (``-g 1``) for fast scrubbing and constant frame rate so the
player can seek frame-accurately (ADR-0002/0005). Target height is computed in
Python (never upscale) to avoid fragile filter expressions.
"""

from __future__ import annotations

from pathlib import Path

from .ffmpeg import run_ffmpeg

PROXY_MAX_HEIGHT = 1080


def proxy_target_height(src_height: int, max_height: int = PROXY_MAX_HEIGHT) -> int:
    """Even target height that never upscales beyond the source."""
    h = min(max_height, src_height)
    return h - (h % 2)


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
    args = ["-i", str(src), "-vf", f"scale=-2:{target_h}"]
    if rate_num and rate_den:
        args += ["-r", f"{rate_num}/{rate_den}"]  # force CFR
    args += [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-g", "1",                 # all-intra: every frame a keyframe
        "-pix_fmt", "yuv420p",
        "-an",                     # audio handled separately
        str(dest),
    ]
    run_ffmpeg(args)


def build_poster(src: Path | str, dest: Path) -> None:
    """Grab a single representative frame as a JPEG poster."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-i", str(src), "-frames:v", "1", "-q:v", "3", str(dest)])
