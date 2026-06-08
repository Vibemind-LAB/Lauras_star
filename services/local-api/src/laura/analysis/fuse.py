"""Fuse two contiguous shot lists (PySceneDetect Adaptive + TransNetV2) with confidence.

Pure, IO-free, fully unit-testable. The fusion turns two independent boundary opinions
into a single contiguous, end-exclusive shot list where each shot carries a confidence
derived from how much the two engines agreed on its *start* boundary:

* both engines agree (within ``tol`` frames) -> ``1.0`` (and we take the TransNetV2 frame,
  which is frame-accurate on hard cuts and gradual transitions);
* TransNetV2 only -> ``0.75`` (the learned net is the stronger signal on its own);
* Adaptive only -> ``0.6``.

The first shot (starting at frame 0) is always confidence ``1.0`` — the asset start is not
a detected boundary but a certainty.
"""

from __future__ import annotations

from .types import ShotResult

_CONF_BOTH = 1.0
_CONF_TRANSNET = 0.75
_CONF_ADAPTIVE = 0.6


def _internal_boundaries(shots: list[ShotResult]) -> list[int]:
    """The ``src_in_frame`` of every shot after the first (the real cut points).

    Frame 0 is the asset start, not a boundary, so it is excluded. Duplicates are
    collapsed and the result is sorted.
    """
    return sorted({s.src_in_frame for s in shots if s.src_in_frame > 0})


def _total_frames(adaptive: list[ShotResult], transnet: list[ShotResult]) -> int:
    """End-exclusive frame count covered by the inputs (max end across both)."""
    ends = [s.src_out_frame_exclusive for s in adaptive] + [
        s.src_out_frame_exclusive for s in transnet
    ]
    return max(ends) if ends else 0


def _cluster_boundaries(
    candidates: list[tuple[int, str]], *, tol: int
) -> list[tuple[int, float]]:
    """Cluster ``(frame, source)`` candidates within ``tol`` frames into unique boundaries.

    Returns ``[(frame, confidence), ...]`` sorted by frame, one entry per cluster. Within a
    cluster the chosen frame is the TransNetV2 boundary when present (frame-accurate on
    transitions), else the mean adaptive frame (rounded). Confidence follows agreement:
    both sources -> 1.0, transnet-only -> 0.75, adaptive-only -> 0.6.
    """
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda c: c[0])
    clusters: list[list[tuple[int, str]]] = [[ordered[0]]]
    for frame, source in ordered[1:]:
        # Compare against the last member so a chain of near-neighbours stays one cluster.
        if frame - clusters[-1][-1][0] <= tol:
            clusters[-1].append((frame, source))
        else:
            clusters.append([(frame, source)])

    result: list[tuple[int, float]] = []
    for cluster in clusters:
        transnet_frames = [frame for frame, source in cluster if source == "transnet"]
        adaptive_frames = [frame for frame, source in cluster if source == "adaptive"]
        has_transnet = bool(transnet_frames)
        has_adaptive = bool(adaptive_frames)
        if has_transnet and has_adaptive:
            confidence = _CONF_BOTH
        elif has_transnet:
            confidence = _CONF_TRANSNET
        else:
            confidence = _CONF_ADAPTIVE
        if transnet_frames:
            # Frame-accurate on transitions: trust TransNetV2's position.
            frame = round(sum(transnet_frames) / len(transnet_frames))
        else:
            frame = round(sum(adaptive_frames) / len(adaptive_frames))
        result.append((frame, confidence))
    # Two clusters could round to the same frame; collapse, keeping the higher confidence.
    deduped: dict[int, float] = {}
    for frame, confidence in result:
        deduped[frame] = max(confidence, deduped.get(frame, 0.0))
    return sorted(deduped.items())


def fuse_shots(
    adaptive: list[ShotResult], transnet: list[ShotResult], *, tol: int = 8
) -> list[ShotResult]:
    """Fuse two contiguous, end-exclusive shot lists into one with per-shot confidence.

    Each input covers ``[0, total)`` as contiguous shots. We extract internal boundaries,
    cluster them across both engines within ``tol`` frames, and reconstruct contiguous
    ``method="hybrid"`` shots whose confidence is that of their *start* boundary. The result
    is always contiguous, end-exclusive, non-overlapping, with no zero-length shots, and
    covers ``[0, total)``.

    Edge cases: if exactly one input is empty the other is returned unchanged (its own
    method/confidence preserved); if both are empty a single whole-asset hybrid shot is
    returned.
    """
    if not adaptive and not transnet:
        total = _total_frames(adaptive, transnet)
        return [ShotResult(0, total or 1, method="hybrid", confidence=_CONF_BOTH)]
    if not transnet:
        return list(adaptive)
    if not adaptive:
        return list(transnet)

    total = _total_frames(adaptive, transnet)

    candidates: list[tuple[int, str]] = [
        (frame, "adaptive") for frame in _internal_boundaries(adaptive)
    ]
    candidates += [(frame, "transnet") for frame in _internal_boundaries(transnet)]
    # Boundaries at/after total cannot start a valid shot; drop them defensively.
    candidates = [(frame, source) for frame, source in candidates if 0 < frame < total]

    clustered = _cluster_boundaries(candidates, tol=tol)

    # Reconstruct contiguous shots. The implicit start boundary (frame 0) is certain.
    starts: list[tuple[int, float]] = [(0, _CONF_BOTH), *clustered]
    edges = [start for start, _ in starts] + [total]

    shots: list[ShotResult] = []
    for i, (start, confidence) in enumerate(starts):
        end = edges[i + 1]
        if end <= start:  # guard against any duplicate/degenerate boundary
            continue
        shots.append(
            ShotResult(
                src_in_frame=start,
                src_out_frame_exclusive=end,
                method="hybrid",
                confidence=confidence,
            )
        )
    return shots
