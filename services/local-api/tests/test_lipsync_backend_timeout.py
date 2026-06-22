from __future__ import annotations

import pytest

from laura.ai.lipsync_backend import VibeVideoLipsyncBackend


def test_vibevideo_backend_default_timeout_allows_slow_gpu_sidecars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LAURA_LIPSYNC_TIMEOUT", raising=False)

    backend = VibeVideoLipsyncBackend(base_url="http://127.0.0.1:8901")

    assert backend.timeout_seconds == 1800.0


def test_vibevideo_backend_timeout_can_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAURA_LIPSYNC_TIMEOUT", "42")

    backend = VibeVideoLipsyncBackend(base_url="http://127.0.0.1:8901")

    assert backend.timeout_seconds == 42.0
