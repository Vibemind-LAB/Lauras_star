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

    # Final hybrid step: snap each fused boundary to its local peak-diff frame so cuts on
    # gradual transitions (dissolves/crossfades) land frame-exactly. Hard cuts already sit on
    # the peak -> no-op. Degrades gracefully: snapping never fails a boundary (per-boundary
    # IO errors leave it put), so on any failure we fall back to the unsnapped fusion.
    try:
        fused = _snap_fused_shots(video_path, fused)
    except Exception as exc:  # pragma: no cover - defensive; snap_boundaries is per-boundary safe
        diagnostics["snap"] = f"degraded: {type(exc).__name__}: {exc}"
    return fused, diagnostics


def _snap_fused_shots(
    video_path: Path | str, fused: list[ShotResult]
) -> list[ShotResult]:
    """Rebuild contiguous shots after snapping each internal boundary to its peak-diff frame.

    Keeps the per-boundary confidence from the fusion and ``method="hybrid"``. The result
    preserves the same invariants as :func:`laura.analysis.fuse.fuse_shots`: contiguous,
    end-exclusive, non-overlapping, no zero-length shots, covering ``[0, total)``.
    """
    if len(fused) < 2:
        return fused  # no internal boundary to snap (whole-asset shot)

    from .refine import snap_boundaries

    total = fused[-1].src_out_frame_exclusive

    # Snap each internal boundary (every shot start except the leading 0) one at a time so the
    # snapped frame stays paired with its fusion confidence. Per-boundary snapping keeps a
    # boundary put on any IO error, so a single failure never drops a cut.
    starts: list[tuple[int, float | None]] = [(0, fused[0].confidence)]
    seen: set[int] = {0}
    for shot in fused[1:]:
        snapped_pair = snap_boundaries(
            video_path, [shot.src_in_frame], total_frames=total
        )
        new = snapped_pair[0] if snapped_pair else shot.src_in_frame
        # Two adjacent boundaries can snap onto the same frame -> keep one (dedup), and never
        # let a snap land on 0/total (which would create a zero-length shot).
        if new in seen or not (0 < new < total):
            continue
        seen.add(new)
        starts.append((new, shot.confidence))

    starts.sort(key=lambda s: s[0])
    edges = [start for start, _ in starts] + [total]
    rebuilt: list[ShotResult] = []
    for i, (start, confidence) in enumerate(starts):
        end = edges[i + 1]
        if end <= start:  # guard against any duplicate/degenerate boundary
            continue
        rebuilt.append(
            ShotResult(
                src_in_frame=start,
                src_out_frame_exclusive=end,
                method="hybrid",
                confidence=confidence,
            )
        )
    return rebuilt or fused
