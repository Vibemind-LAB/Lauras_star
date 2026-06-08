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
from laura.analysis.shots import _snap_fused_shots, detect_shots, detect_shots_hybrid
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


# --- snapping (final hybrid step) ------------------------------------------------------


def _identity_snap(_video: Path | str, boundaries: list[int], **_kw: object) -> list[int]:
    """A snap that returns boundaries unchanged (stand-in for hard cuts / no movement)."""
    return list(boundaries)


def test_hybrid_snaps_fused_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fused boundary is replaced by its snapped (peak-diff) frame in the final shots."""
    _patch_adaptive(monkeypatch)
    monkeypatch.setattr("laura.analysis.transnet.transnetv2_available", lambda: True)
    monkeypatch.setattr(
        "laura.analysis.transnet.detect_shots_transnet", lambda _p: _transnet_shots()
    )
    # Move the transnet-only boundary 200 -> 205 (simulating snap onto the true peak),
    # and leave the agreed boundary 101 put (hard cut, no movement).
    def fake_snap(_video: Path | str, boundaries: list[int], **_kw: object) -> list[int]:
        return [205 if b == 200 else b for b in boundaries]

    monkeypatch.setattr("laura.analysis.refine.snap_boundaries", fake_snap)

    fused, diag = detect_shots_hybrid(_FAKE)

    assert "snap" not in diag  # snapping succeeded
    by_start = {s.src_in_frame: s for s in fused}
    assert 200 not in by_start and 205 in by_start  # boundary moved to the peak
    assert by_start[205].confidence == 0.75  # confidence carried over from the fusion
    assert by_start[101].confidence == 1.0   # agreed boundary unchanged
    # Still a contiguous, end-exclusive cover of [0, 300).
    assert fused[0].src_in_frame == 0
    assert fused[-1].src_out_frame_exclusive == 300
    for a, b in zip(fused, fused[1:], strict=False):
        assert a.src_out_frame_exclusive == b.src_in_frame
        assert a.src_out_frame_exclusive > a.src_in_frame
    assert all(s.method == "hybrid" for s in fused)


def test_hybrid_snap_failure_keeps_fusion(monkeypatch: pytest.MonkeyPatch) -> None:
    """If snapping blows up, the unsnapped fusion is returned with a degrade note."""
    _patch_adaptive(monkeypatch)
    monkeypatch.setattr("laura.analysis.transnet.transnetv2_available", lambda: True)
    monkeypatch.setattr(
        "laura.analysis.transnet.detect_shots_transnet", lambda _p: _transnet_shots()
    )

    def boom(*_a: object, **_k: object) -> list[int]:
        raise RuntimeError("snap exploded")

    monkeypatch.setattr("laura.analysis.refine.snap_boundaries", boom)

    fused, diag = detect_shots_hybrid(_FAKE)

    assert diag["snap"].startswith("degraded:")
    by_start = {s.src_in_frame: s for s in fused}
    assert set(by_start) == {0, 101, 200}  # unsnapped fusion preserved
    assert all(s.method == "hybrid" for s in fused)


def test_snap_fused_shots_rebuilds_contiguous(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_snap_fused_shots`` keeps the fuse invariants when a boundary moves."""
    fused = [
        ShotResult(0, 100, method="hybrid", confidence=1.0),
        ShotResult(100, 200, method="hybrid", confidence=0.75),
        ShotResult(200, 300, method="hybrid", confidence=0.6),
    ]
    # Snap each single-boundary call: move 100 -> 104, keep 200.
    def snap(_video: Path | str, boundaries: list[int], **_kw: object) -> list[int]:
        return [104 if b == 100 else b for b in boundaries]

    monkeypatch.setattr("laura.analysis.refine.snap_boundaries", snap)
    out = _snap_fused_shots(_FAKE, fused)

    starts = [s.src_in_frame for s in out]
    assert starts == [0, 104, 200]
    assert out[-1].src_out_frame_exclusive == 300
    for a, b in zip(out, out[1:], strict=False):
        assert a.src_out_frame_exclusive == b.src_in_frame
        assert a.src_out_frame_exclusive > a.src_in_frame
    by_start = {s.src_in_frame: s for s in out}
    assert by_start[104].confidence == 0.75  # confidence carried from the 100-boundary shot
    assert by_start[200].confidence == 0.6


def test_snap_fused_shots_dedups_collision(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two boundaries snapping onto the same frame collapse to one contiguous shot."""
    fused = [
        ShotResult(0, 100, method="hybrid", confidence=1.0),
        ShotResult(100, 150, method="hybrid", confidence=0.75),
        ShotResult(150, 300, method="hybrid", confidence=0.6),
    ]
    # Both internal boundaries snap onto 120.
    def snap(_video: Path | str, boundaries: list[int], **_kw: object) -> list[int]:
        return [120 for _ in boundaries]

    monkeypatch.setattr("laura.analysis.refine.snap_boundaries", snap)
    out = _snap_fused_shots(_FAKE, fused)

    starts = [s.src_in_frame for s in out]
    assert starts == [0, 120]  # collision deduped to one boundary
    assert out[-1].src_out_frame_exclusive == 300
    for a, b in zip(out, out[1:], strict=False):
        assert a.src_out_frame_exclusive == b.src_in_frame
        assert a.src_out_frame_exclusive > a.src_in_frame
