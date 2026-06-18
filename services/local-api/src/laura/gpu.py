"""GPU capability detection (cached, tolerant). Every probe returns False on any
error so the backend runs unchanged without a GPU or the heavy extras."""
from __future__ import annotations

import logging
import subprocess
from functools import lru_cache

from .ingest.ffmpeg import ffmpeg_bin

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def nvenc_available() -> bool:
    """True if this ffmpeg build exposes the h264_nvenc encoder."""
    try:
        proc = subprocess.run(
            [ffmpeg_bin(), "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=15,
        )  # noqa: S603
    except Exception:  # noqa: BLE001 - ffmpeg missing / timeout -> no NVENC
        return False
    ok = "h264_nvenc" in (proc.stdout or "")
    logger.info("nvenc_available=%s", ok)
    return ok


@lru_cache(maxsize=1)
def asr_cuda_available() -> bool:
    """True if ctranslate2 (faster-whisper backend) sees a CUDA device."""
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count()) > 0
    except Exception:  # noqa: BLE001
        return False


@lru_cache(maxsize=1)
def torch_cuda_available() -> bool:
    """True if torch reports an available CUDA device (TransNet scene path)."""
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001
        return False
