import sys
import types
from typing import Any

import pytest

import laura.analysis.asr as asr


def test_resolve_device_prefers_cuda_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAURA_ASR_DEVICE", raising=False)
    monkeypatch.setattr(asr, "asr_cuda_available", lambda: True)
    assert asr.resolve_asr_device() == "cuda"


def test_resolve_device_cpu_when_no_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAURA_ASR_DEVICE", raising=False)
    monkeypatch.setattr(asr, "asr_cuda_available", lambda: False)
    assert asr.resolve_asr_device() == "cpu"


def test_resolve_device_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAURA_ASR_DEVICE", "cpu")
    monkeypatch.setattr(asr, "asr_cuda_available", lambda: True)
    assert asr.resolve_asr_device() == "cpu"


def test_run_uses_float16_on_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeModel:
        def __init__(self, model_size: str, device: str, compute_type: str) -> None:
            captured["device"] = device
            captured["compute_type"] = compute_type

        def transcribe(self, *a: Any, **k: Any) -> tuple[list[Any], object]:
            return ([], object())

    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = FakeModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)

    asr._run("a.wav", "base", None, "cuda")
    assert captured == {"device": "cuda", "compute_type": "float16"}
    asr._run("a.wav", "base", None, "cpu")
    assert captured == {"device": "cpu", "compute_type": "int8"}


def test_transcribe_resolves_device_via_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run(audio_path: str, model_size: str, language: Any, device: str) -> list[Any]:
        seen["device"] = device
        return []

    monkeypatch.setattr(asr, "_run", fake_run)
    monkeypatch.setattr(asr, "asr_cuda_available", lambda: True)
    monkeypatch.delenv("LAURA_ASR_DEVICE", raising=False)
    asr.transcribe("a.wav")
    assert seen["device"] == "cuda"
