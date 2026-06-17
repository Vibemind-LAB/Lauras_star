import subprocess

import laura.gpu as gpu


def _clear():
    gpu.nvenc_available.cache_clear()
    gpu.asr_cuda_available.cache_clear()
    gpu.torch_cuda_available.cache_clear()


def test_nvenc_available_true_when_encoder_listed(monkeypatch):
    _clear()

    def fake_run(*a, **k):
        return subprocess.CompletedProcess(a, 0, stdout=" V..... h264_nvenc NVIDIA", stderr="")

    monkeypatch.setattr(gpu.subprocess, "run", fake_run)
    assert gpu.nvenc_available() is True


def test_nvenc_available_false_when_absent(monkeypatch):
    _clear()

    def fake_run(*a, **k):
        return subprocess.CompletedProcess(a, 0, stdout=" V..... libx264 only", stderr="")

    monkeypatch.setattr(gpu.subprocess, "run", fake_run)
    assert gpu.nvenc_available() is False


def test_nvenc_available_false_on_error(monkeypatch):
    _clear()
    def boom(*a, **k):
        raise FileNotFoundError("no ffmpeg")
    monkeypatch.setattr(gpu.subprocess, "run", boom)
    assert gpu.nvenc_available() is False
