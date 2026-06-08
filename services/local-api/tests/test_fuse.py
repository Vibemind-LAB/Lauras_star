"""Pure unit tests for :func:`laura.analysis.fuse.fuse_shots`.

No IO / inference — these exercise the fusion logic directly: agreement bumps confidence to
1.0, disagreement keeps the lone-engine boundary at its engine-specific confidence, near
boundaries cluster within ``tol``, and the output is always a contiguous, end-exclusive,
non-overlapping cover of ``[0, total)``.
"""

from __future__ import annotations

from laura.analysis.fuse import fuse_shots
from laura.analysis.types import ShotResult


def _from_boundaries(boundaries: list[int], total: int, method: str) -> list[ShotResult]:
    """Turn internal boundaries into a contiguous, end-exclusive shot list over [0, total)."""
    edges = [0, *boundaries, total]
    return [
        ShotResult(edges[i], edges[i + 1], method=method)
        for i in range(len(edges) - 1)
        if edges[i + 1] > edges[i]
    ]


def _assert_contiguous_cover(shots: list[ShotResult], total: int) -> None:
    assert shots, "expected at least one shot"
    assert shots[0].src_in_frame == 0
    assert shots[-1].src_out_frame_exclusive == total
    for a, b in zip(shots, shots[1:], strict=False):
        assert a.src_out_frame_exclusive == b.src_in_frame  # contiguous, end-exclusive
        assert a.src_out_frame_exclusive > a.src_in_frame    # no zero-length / overlap
    assert shots[-1].src_out_frame_exclusive > shots[-1].src_in_frame


def test_agreement_yields_confidence_one() -> None:
    """Two near-identical boundary sets -> shared boundaries at confidence 1.0."""
    adaptive = _from_boundaries([100, 200, 300], total=400, method="pyscenedetect:adaptive")
    transnet = _from_boundaries([101, 199, 302], total=400, method="transnetv2")

    fused = fuse_shots(adaptive, transnet, tol=8)

    _assert_contiguous_cover(fused, total=400)
    # 3 internal boundaries -> 4 shots, all starts at confidence 1.0 (both engines agreed).
    assert len(fused) == 4
    assert all(s.confidence == 1.0 for s in fused)
    assert all(s.method == "hybrid" for s in fused)
    # Boundaries snapped to the TransNetV2 positions.
    assert [s.src_in_frame for s in fused] == [0, 101, 199, 302]


def test_disagreement_keeps_transnet_only_boundary_at_075() -> None:
    """TransNetV2 sees an extra gradual-transition boundary adaptive lacks -> kept at 0.75."""
    adaptive = _from_boundaries([100, 300], total=400, method="pyscenedetect:adaptive")
    transnet = _from_boundaries([100, 200, 300], total=400, method="transnetv2")

    fused = fuse_shots(adaptive, transnet, tol=8)

    _assert_contiguous_cover(fused, total=400)
    by_start = {s.src_in_frame: s for s in fused}
    assert set(by_start) == {0, 100, 200, 300}
    assert by_start[0].confidence == 1.0      # asset start
    assert by_start[100].confidence == 1.0    # both engines
    assert by_start[200].confidence == 0.75   # transnet-only extra boundary
    assert by_start[300].confidence == 1.0    # both engines


def test_adaptive_only_boundary_at_06() -> None:
    """An adaptive boundary the net lacks survives the fusion at confidence 0.6."""
    adaptive = _from_boundaries([100, 200], total=300, method="pyscenedetect:adaptive")
    transnet = _from_boundaries([100], total=300, method="transnetv2")

    fused = fuse_shots(adaptive, transnet, tol=8)

    _assert_contiguous_cover(fused, total=300)
    by_start = {s.src_in_frame: s for s in fused}
    assert by_start[100].confidence == 1.0   # agreed
    assert by_start[200].confidence == 0.6   # adaptive-only


def test_tolerance_clustering_merges_near_boundaries() -> None:
    """Boundaries 3 frames apart merge into ONE boundary with tol=8 (no double cut)."""
    adaptive = _from_boundaries([150], total=300, method="pyscenedetect:adaptive")
    transnet = _from_boundaries([153], total=300, method="transnetv2")

    fused = fuse_shots(adaptive, transnet, tol=8)

    _assert_contiguous_cover(fused, total=300)
    assert len(fused) == 2  # one merged internal boundary -> two shots
    # Both engines contributed within tol -> confidence 1.0 at the (transnet) position 153.
    assert fused[1].src_in_frame == 153
    assert fused[1].confidence == 1.0


def test_tolerance_respected_when_apart() -> None:
    """Boundaries further apart than tol stay distinct."""
    adaptive = _from_boundaries([150], total=300, method="pyscenedetect:adaptive")
    transnet = _from_boundaries([170], total=300, method="transnetv2")

    fused = fuse_shots(adaptive, transnet, tol=8)

    _assert_contiguous_cover(fused, total=300)
    starts = [s.src_in_frame for s in fused]
    assert starts == [0, 150, 170]
    by_start = {s.src_in_frame: s for s in fused}
    assert by_start[150].confidence == 0.6    # adaptive-only
    assert by_start[170].confidence == 0.75   # transnet-only


def test_empty_transnet_returns_adaptive_unchanged() -> None:
    adaptive = _from_boundaries([100], total=200, method="pyscenedetect:adaptive")
    fused = fuse_shots(adaptive, [], tol=8)
    assert fused == adaptive  # unchanged method/confidence


def test_empty_adaptive_returns_transnet_unchanged() -> None:
    transnet = _from_boundaries([100], total=200, method="transnetv2")
    fused = fuse_shots([], transnet, tol=8)
    assert fused == transnet


def test_both_empty_returns_single_whole_hybrid_shot() -> None:
    fused = fuse_shots([], [], tol=8)
    assert len(fused) == 1
    assert fused[0].src_in_frame == 0
    assert fused[0].src_out_frame_exclusive == 1  # total is 0 -> 1 (never zero-length)
    assert fused[0].method == "hybrid"
    assert fused[0].confidence == 1.0


def test_differing_totals_uses_max_end() -> None:
    """``total`` is the max end across both inputs; the cover reaches it."""
    adaptive = _from_boundaries([100], total=180, method="pyscenedetect:adaptive")
    transnet = _from_boundaries([100], total=200, method="transnetv2")
    fused = fuse_shots(adaptive, transnet, tol=8)
    _assert_contiguous_cover(fused, total=200)


def test_no_internal_boundaries_single_shot() -> None:
    """Both engines see the whole asset as one shot -> one hybrid shot at confidence 1.0."""
    adaptive = [ShotResult(0, 250, method="pyscenedetect:adaptive")]
    transnet = [ShotResult(0, 250, method="transnetv2")]
    fused = fuse_shots(adaptive, transnet, tol=8)
    assert len(fused) == 1
    _assert_contiguous_cover(fused, total=250)
    assert fused[0].confidence == 1.0
