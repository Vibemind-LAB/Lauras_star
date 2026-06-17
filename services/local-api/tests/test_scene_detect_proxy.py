"""Unit tests for the small same-fps scene-detection proxy in ``analysis.handlers``.

These exercise the pure helpers (``_build_detection_proxy`` / ``_resolve_detect_video``)
without needing real ffmpeg or PySceneDetect: ``run_ffmpeg`` and the proxy builder are
monkeypatched so the tests run fast and deterministically.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from laura.analysis import handlers
from laura.db import repos


def test_build_detection_proxy_builds_downscale_command(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The builder emits a same-fps downscale-to-360p command and reports success."""
    captured: dict[str, list[str]] = {}
    dest = tmp_path / "scene-detect.mp4"

    def fake_run_ffmpeg(args: list[str], **_: Any) -> None:
        captured["args"] = args
        dest.write_bytes(b"\x00\x01\x02\x03")  # non-empty file -> success check passes

    monkeypatch.setattr("laura.ingest.ffmpeg.run_ffmpeg", fake_run_ffmpeg)

    ok = handlers._build_detection_proxy("input.mp4", dest, height=360)

    assert ok is True
    args = captured["args"]
    assert "scale=-2:360" in args
    assert "-fps_mode" in args
    assert "passthrough" in args
    assert "libx264" in args
    assert str(dest) in args


def test_build_detection_proxy_returns_false_on_ffmpeg_error(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from laura.ingest.ffmpeg import FFmpegError

    def fake_run_ffmpeg(_args: list[str], **_: Any) -> None:
        raise FFmpegError("boom")

    monkeypatch.setattr("laura.ingest.ffmpeg.run_ffmpeg", fake_run_ffmpeg)

    ok = handlers._build_detection_proxy("input.mp4", tmp_path / "x.mp4", height=360)
    assert ok is False


def _asset(height: int, *, project_id: str = "p1", asset_id: str = "a1") -> dict[str, Any]:
    return {"id": asset_id, "project_id": project_id, "height": height}


def test_resolve_detect_video_uses_temp_when_taller(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A 1080p source taller than the target builds and returns the small temp proxy."""

    def fake_build(_src: str, dest: Path, *, height: int) -> bool:
        dest.write_bytes(b"\x00")
        return True

    monkeypatch.setattr(handlers, "_build_detection_proxy", fake_build)
    monkeypatch.setattr(
        repos, "get_project", lambda _db, _pid: {"workspace_root": str(tmp_path)}
    )

    path, tmp = handlers._resolve_detect_video(object(), _asset(1080), "proxy.mp4")  # type: ignore[arg-type]

    assert tmp is not None
    assert path == str(tmp)
    assert tmp.exists()


def test_resolve_detect_video_skips_when_small_enough(monkeypatch: Any) -> None:
    """A source at/under the target height is detected on directly (no temp build)."""

    def fail_build(*_a: Any, **_k: Any) -> bool:
        raise AssertionError("should not build a proxy for a small source")

    monkeypatch.setattr(handlers, "_build_detection_proxy", fail_build)

    path, tmp = handlers._resolve_detect_video(object(), _asset(240), "proxy.mp4")  # type: ignore[arg-type]

    assert path == "proxy.mp4"
    assert tmp is None


def test_resolve_detect_video_falls_back_when_build_fails(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """If building the small proxy fails, detection falls back to the full proxy."""
    monkeypatch.setattr(handlers, "_build_detection_proxy", lambda *_a, **_k: False)
    monkeypatch.setattr(
        repos, "get_project", lambda _db, _pid: {"workspace_root": str(tmp_path)}
    )

    path, tmp = handlers._resolve_detect_video(object(), _asset(1080), "proxy.mp4")  # type: ignore[arg-type]

    assert path == "proxy.mp4"
    assert tmp is None
