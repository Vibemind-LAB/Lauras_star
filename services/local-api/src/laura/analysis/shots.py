"""Shot/cut detection via PySceneDetect (optional extra: ``[scene]``).

Deterministic content-based cut detection. Frame indices come straight from
PySceneDetect's scene list, which is already end-exclusive (each scene's end is the
next scene's start). TransNetV2 refinement is a future optional second pass.
"""

from __future__ import annotations

from pathlib import Path

from .types import ShotResult

DEFAULT_THRESHOLD = 27.0


def scenedetect_available() -> bool:
    try:
        import scenedetect  # noqa: F401
    except Exception:
        return False
    return True


def detect_shots(
    video_path: Path | str, *, threshold: float = DEFAULT_THRESHOLD
) -> list[ShotResult]:
    """Detect shot boundaries. Returns shots as end-exclusive source-frame ranges.

    Raises ImportError (via the lazy import) if the ``scene`` extra is not installed.
    """
    from scenedetect import ContentDetector, detect

    scenes = detect(str(video_path), ContentDetector(threshold=threshold))
    results: list[ShotResult] = []
    for start, end in scenes:
        results.append(
            ShotResult(
                src_in_frame=int(start.frame_num),
                src_out_frame_exclusive=int(end.frame_num),
                method="pyscenedetect",
            )
        )
    return results
