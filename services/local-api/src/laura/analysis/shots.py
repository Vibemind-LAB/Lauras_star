"""Shot/cut detection via PySceneDetect (optional extra: ``[scene]``).

Deterministic content-based cut detection. Frame indices come straight from
PySceneDetect's scene list, which is already end-exclusive (each scene's end is the
next scene's start). TransNetV2 refinement is a future optional second pass.
"""

from __future__ import annotations

from pathlib import Path

from .types import ShotResult

DEFAULT_THRESHOLD = 27.0
_DETECTORS = {"adaptive", "content", "histogram", "transnet"}


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
    (Y-channel histogram correlation), or ``transnet`` (the learned TransNetV2 engine, extra
    ``scene-ml``). Raises ImportError if the required extra (``scene`` / ``scene-ml``) is
    absent.
    """
    if detector not in _DETECTORS:
        raise ValueError(f"unknown detector {detector!r}; choose one of {sorted(_DETECTORS)}")
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
