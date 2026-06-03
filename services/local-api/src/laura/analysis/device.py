"""Device selection for optional ML stages.

Honours ``LAURA_ASR_DEVICE`` (e.g. force ``"cpu"`` on a half-configured GPU host);
otherwise uses CUDA when torch reports it available, else CPU. Importing torch is
guarded so this works even without the ML extras installed.
"""

from __future__ import annotations

import os


def torch_device(preferred: str | None = None) -> str:
    choice = preferred or os.environ.get("LAURA_ASR_DEVICE")
    if choice:
        return choice
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"
