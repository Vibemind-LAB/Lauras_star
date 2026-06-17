import laura.ingest.proxy as proxy


def _capture(monkeypatch):
    calls = {}
    monkeypatch.setattr(proxy, "run_ffmpeg", lambda args, **k: calls.setdefault("args", args))
    return calls


def test_build_proxy_uses_nvenc_when_available(monkeypatch, tmp_path):
    monkeypatch.delenv("LAURA_PROXY_ENCODER", raising=False)
    monkeypatch.setattr(proxy, "nvenc_available", lambda: True)
    calls = _capture(monkeypatch)
    proxy.build_proxy("in.mp4", tmp_path / "p.mp4", src_height=1080, rate_num=30, rate_den=1)
    assert "h264_nvenc" in calls["args"]
    assert "scale=-2:1080" in calls["args"]  # no downscale of a 1080p source


def test_build_proxy_falls_back_to_libx264(monkeypatch, tmp_path):
    monkeypatch.delenv("LAURA_PROXY_ENCODER", raising=False)
    monkeypatch.setattr(proxy, "nvenc_available", lambda: False)
    calls = _capture(monkeypatch)
    proxy.build_proxy("in.mp4", tmp_path / "p.mp4", src_height=1080)
    assert "libx264" in calls["args"]


def test_build_proxy_env_override_forces_libx264(monkeypatch, tmp_path):
    monkeypatch.setenv("LAURA_PROXY_ENCODER", "libx264")
    monkeypatch.setattr(proxy, "nvenc_available", lambda: True)
    calls = _capture(monkeypatch)
    proxy.build_proxy("in.mp4", tmp_path / "p.mp4", src_height=1080)
    assert "libx264" in calls["args"]
    assert "h264_nvenc" not in calls["args"]
