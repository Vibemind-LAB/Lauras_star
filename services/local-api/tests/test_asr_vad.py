"""Tests that vad_filter is forwarded to model.transcribe (Silero VAD, S1)."""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest

import laura.analysis.asr as asr


def _inject_fake_whisper(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Inject a fake faster_whisper module; return the mock transcribe method."""
    transcribe_mock = MagicMock(return_value=([], object()))

    class FakeModel:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        transcribe = transcribe_mock

    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = FakeModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    return transcribe_mock


def test_transcribe_passes_vad_filter_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """vad_filter=True must reach model.transcribe (default VAD_FILTER=True)."""
    transcribe_mock = _inject_fake_whisper(monkeypatch)
    monkeypatch.setattr(asr, "VAD_FILTER", True)

    asr._run("dummy.wav", model_size="tiny", language=None, device="cpu")

    transcribe_mock.assert_called_once()
    assert transcribe_mock.call_args.kwargs.get("vad_filter") is True


def test_transcribe_passes_vad_filter_false_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """When VAD_FILTER is patched to False, model.transcribe receives vad_filter=False."""
    transcribe_mock = _inject_fake_whisper(monkeypatch)
    monkeypatch.setattr(asr, "VAD_FILTER", False)

    asr._run("dummy.wav", model_size="tiny", language=None, device="cpu")

    transcribe_mock.assert_called_once()
    assert transcribe_mock.call_args.kwargs.get("vad_filter") is False
