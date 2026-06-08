"""Shot/cut detection via PySceneDetect (optional extra: ``[scene]``).

Deterministic content-based cut detection. Frame indices come straight from
PySceneDetect's scene list, which is already end-exclusive (each scene's end is the
next scene's start). TransNetV2 refinement is a future optional second pass.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .types import ShotResult

DEFAULT_THRESHOLD = 27.0
_DETECTORS = {"adaptive", "content", "histogram", "transnet", "hybrid"}


def scenedetect_available() -> bool:
    try:
        import scenedetect  # noqa: F401
    except Exception:
        return False
    return True


def detect_shots(
    video_path: Path | str,
    *,
    detector: str = "adaptive",
    threshold: float = DEFAULT_THRESHOLD,
) -> list[ShotResult]:
    """Detect shot boundaries (end-exclusive source-frame ranges).

    ``detector`` selects the algorithm: ``adaptive`` (PySceneDetect rolling content score,
    fewer false cuts on motion — default), ``content`` (HSV content), ``histogram``
    (Y-channel histogram correlation), ``transnet`` (the learned TransNetV2 engine, extra
    ``scene-ml``), or ``hybrid`` (fuse Adaptive + TransNetV2 with confidence; degrades to
    adaptive alone when TransNetV2 is absent or fails). Raises ImportError if the required
    extra (``scene`` / ``scene-ml``) is absent.
    """
    if detector not in _DETECTORS:
        raise ValueError(f"unknown detector {detector!r}; choose one of {sorted(_DETECTORS)}")
    if detector == "hybrid":
        shots, _ = detect_shots_hybrid(video_path, threshold=threshold)
        return shots
    if detector == "transnet":
        from .transnet import detect_shots_transnet  # lazy: torch + weights are heavy

        return detect_shots_transnet(video_path)
    from scenedetect import AdaptiveDetector, ContentDetector, detect

    if detector == "adaptive":
        algo = AdaptiveDetector()
    elif detector == "histogram":
        from scenedetect import HistogramDetector  # lazy: not on older PySceneDetect

        algo = HistogramDetector()
    else:
        algo = ContentDetector(threshold=threshold)

    scenes = detect(str(video_path), algo)
    return [
        ShotResult(
            src_in_frame=int(start.frame_num),
            src_out_frame_exclusive=int(end.frame_num),
            method=f"pyscenedetect:{detector}",
        )
        for start, end in scenes
    ]


def detect_shots_hybrid(
    video_path: Path | str,
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> tuple[list[ShotResult], dict[str, Any]]:
    """Max-quality fusion of PySceneDetect ``adaptive`` and TransNetV2 boundaries.

    Always runs the always-present ``adaptive`` detector. If TransNetV2 (extra ``scene-ml``)
    is available *and* its inference succeeds, the two boundary sets are fused with
    confidence via :func:`laura.analysis.fuse.fuse_shots`. If TransNetV2 is absent or raises,
    the hybrid degrades gracefully to the adaptive shots alone — it never fails merely
    because the learned engine is missing.

    Returns ``(shots, diagnostics)``. ``diagnostics`` always carries ``adaptive_count`` and
    either ``transnet_count`` (fused) or ``hybrid`` (a ``"degraded: <reason>"`` note).
    """
    adaptive = detect_shots(video_path, detector="adaptive", threshold=threshold)
    diagnostics: dict[str, Any] = {"adaptive_count": len(adaptive)}

    from .transnet import transnetv2_available

    if not transnetv2_available():
        diagnostics["hybrid"] = "degraded: TransNetV2 unavailable (scene-ml extra absent)"
        return adaptive, diagnostics

    from .transnet import detect_shots_transnet  # lazy: torch + weights are heavy

    try:
        transnet = detect_shots_transnet(video_path)
    except (ImportError, RuntimeError) as exc:
        diagnostics["hybrid"] = f"degraded: TransNetV2 {type(exc).__name__}: {exc}"
        return adaptive, diagnostics

    from .fuse import fuse_shots

    fused = fuse_shots(adaptive, transnet)
    diagnostics["transnet_count"] = len(transnet)
    return fused, diagnostics
