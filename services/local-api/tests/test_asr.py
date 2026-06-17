import sys
import types

import laura.analysis.asr as asr


def test_resolve_device_prefers_cuda_when_available(monkeypatch):
    monkeypatch.delenv("LAURA_ASR_DEVICE", raising=False)
    monkeypatch.setattr(asr, "asr_cuda_available", lambda: True)
    assert asr.resolve_asr_device() == "cuda"


def test_resolve_device_cpu_when_no_cuda(monkeypatch):
    monkeypatch.delenv("LAURA_ASR_DEVICE", raising=False)
    monkeypatch.setattr(asr, "asr_cuda_available", lambda: False)
    assert asr.resolve_asr_device() == "cpu"


def test_resolve_device_env_override(monkeypatch):
    monkeypatch.setenv("LAURA_ASR_DEVICE", "cpu")
    monkeypatch.setattr(asr, "asr_cuda_available", lambda: True)
    assert asr.resolve_asr_device() == "cpu"


def test_run_uses_float16_on_cuda(monkeypatch):
    captured = {}

    class FakeModel:
        def __init__(self, model_size, device, compute_type):
            captured["device"] = device
            captured["compute_type"] = compute_type

        def transcribe(self, *a, **k):
            return ([], object())

    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)

    asr._run("a.wav", "base", None, "cuda")
    assert captured == {"device": "cuda", "compute_type": "float16"}
    asr._run("a.wav", "base", None, "cpu")
    assert captured == {"device": "cpu", "compute_type": "int8"}


def test_transcribe_resolves_device_via_resolver(monkeypatch):
    seen = {}

    def fake_run(audio_path, model_size, language, device):
        seen["device"] = device
        return []

    monkeypatch.setattr(asr, "_run", fake_run)
    monkeypatch.setattr(asr, "asr_cuda_available", lambda: True)
    monkeypatch.delenv("LAURA_ASR_DEVICE", raising=False)
    asr.transcribe("a.wav")
    assert seen["device"] == "cuda"
