"""reconcile_after_delete: ripple-track scene bounds after a delete_range (invariant #2)."""
from __future__ import annotations

from laura.scenes.reconcile import reconcile_after_delete


def test_delete_before_boundary_shifts_later_bounds_left() -> None:
    # Scenes [0,100) [100,250); delete [10,40) (len 30) entirely inside scene 1.
    out = reconcile_after_delete([(0, 100), (100, 250)], 10, 40)
    assert out == [(0, 70), (70, 220)]


def test_delete_spanning_a_boundary_keeps_the_boundary() -> None:
    # Delete [80,140) straddles the 100 boundary; boundary survives, collapsed to del_seq_in.
    out = reconcile_after_delete([(0, 100), (100, 250)], 80, 140)
    assert out == [(0, 80), (80, 190)]


def test_scene_fully_inside_deleted_span_is_dropped() -> None:
    # Scene 2 = [100,140) lies wholly inside delete [90,200) -> zero-length -> dropped.
    out = reconcile_after_delete([(0, 100), (100, 140), (140, 300)], 90, 200)
    assert out == [(0, 90), (90, 190)]


def test_delete_at_tail_shrinks_last_scene() -> None:
    out = reconcile_after_delete([(0, 100), (100, 250)], 240, 250)
    assert out == [(0, 100), (100, 240)]


def test_empty_bounds_returns_empty() -> None:
    assert reconcile_after_delete([], 0, 10) == []


def test_output_is_contiguous_and_ordered() -> None:
    out = reconcile_after_delete([(0, 50), (50, 60), (60, 200)], 45, 120)
    # span [45,120) len 75; scene2 [50,60) inside -> drop; bounds clamp+shift.
    assert out == [(0, 45), (45, 125)]
    for (a_in, a_out), (b_in, b_out) in zip(out, out[1:]):
        assert a_out == b_in  # contiguous
        assert a_in < a_out   # no zero-length survives
