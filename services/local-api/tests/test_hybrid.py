"""Hybrid detector wiring in :mod:`laura.analysis.shots`.

These tests are pure-Python: they monkeypatch the adaptive detector and the TransNetV2
engine so no ffmpeg, PySceneDetect, weights download, or GPU inference is needed. They cover
(a) the fusion path when TransNetV2 contributes and (b) graceful degradation to the adaptive
shots alone when TransNetV2 is absent or raises.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from laura.analysis import shots as shots_mod
from laura.analysis.shots import detect_shots, detect_shots_hybrid
from laura.analysis.types import ShotResult

_FAKE = Path("does-not-exist.mp4")


def _adaptive_shots() -> list[ShotResult]:
    return [
        ShotResult(0, 100, method="pyscenedetect:adaptive"),
        ShotResult(100, 300, method="pyscenedetect:adaptive"),
    ]


def _transnet_shots() -> list[ShotResult]:
    # Agrees on the cut at 100, adds an extra (gradual) boundary at 200.
    return [
        ShotResult(0, 101, method="transnetv2"),
        ShotResult(101, 200, method="transnetv2"),
        ShotResult(200, 300, method="transnetv2"),
    ]


def _patch_adaptive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``detect_shots(detector='adaptive')`` return fixed shots without scenedetect."""
    real = shots_mod.detect_shots

    def fake_detect(
        video_path: Path | str,
        *,
        detector: str = "adaptive",
        threshold: float = shots_mod.DEFAULT_THRESHOLD,
    ) -> list[ShotResult]:
        if detector == "adaptive":
            return _adaptive_shots()
        return real(video_path, detector=detector, threshold=threshold)

    monkeypatch.setattr(shots_mod, "detect_shots", fake_detect)


def test_hybrid_fuses_when_transnet_available(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_adaptive(monkeypatch)
    monkeypatch.setattr("laura.analysis.transnet.transnetv2_available", lambda: True)
    monkeypatch.setattr(
        "laura.analysis.transnet.detect_shots_transnet", lambda _p: _transnet_shots()
    )

    fused, diag = detect_shots_hybrid(_FAKE)

    assert diag["adaptive_count"] == 2
    assert diag["transnet_count"] == 3
    assert "hybrid" not in diag  # not degraded
    assert all(s.method == "hybrid" for s in fused)
    # Contiguous, end-exclusive cover of [0, 300).
    assert fused[0].src_in_frame == 0
    assert fused[-1].src_out_frame_exclusive == 300
    for a, b in zip(fused, fused[1:], strict=False):
        assert a.src_out_frame_exclusive == b.src_in_frame
    by_start = {s.src_in_frame: s for s in fused}
    assert by_start[101].confidence == 1.0   # both engines agreed (~100/101)
    assert by_start[200].confidence == 0.75  # transnet-only extra boundary


def test_detect_shots_hybrid_via_public_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """``detect_shots(detector='hybrid')`` returns the fused list."""
    _patch_adaptive(monkeypatch)
    monkeypatch.setattr("laura.analysis.transnet.transnetv2_available", lambda: True)
    monkeypatch.setattr(
        "laura.analysis.transnet.detect_shots_transnet", lambda _p: _transnet_shots()
    )

    fused = detect_shots(_FAKE, detector="hybrid")

    assert fused
    assert all(s.method == "hybrid" for s in fused)


def test_hybrid_degrades_when_transnet_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_adaptive(monkeypatch)
    monkeypatch.setattr("laura.analysis.transnet.transnetv2_available", lambda: False)

    shots, diag = detect_shots_hybrid(_FAKE)

    assert shots == _adaptive_shots()  # adaptive alone, unchanged
    assert diag["adaptive_count"] == 2
    assert "transnet_count" not in diag
    assert diag["hybrid"].startswith("degraded:")


def test_hybrid_degrades_when_transnet_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_adaptive(monkeypatch)
    monkeypatch.setattr("laura.analysis.transnet.transnetv2_available", lambda: True)

    def _boom(_p: Path | str) -> list[ShotResult]:
        raise RuntimeError("TransNetV2 inference unavailable: forced for test")

    monkeypatch.setattr("laura.analysis.transnet.detect_shots_transnet", _boom)

    shots, diag = detect_shots_hybrid(_FAKE)

    assert shots == _adaptive_shots()  # graceful fallback to adaptive
    assert diag["adaptive_count"] == 2
    assert "transnet_count" not in diag
    assert diag["hybrid"].startswith("degraded:")
    assert "RuntimeError" in diag["hybrid"]


def test_hybrid_is_a_valid_detector() -> None:
    assert "hybrid" in shots_mod._DETECTORS
