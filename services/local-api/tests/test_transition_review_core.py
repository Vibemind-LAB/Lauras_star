"""Plan B / Task B1 — transition_review datatypes, boundary_signature, frame_strip_plan."""

from __future__ import annotations

from typing import Any

from laura.analysis.transition_review import (
    Boundary,
    SuggestedFix,
    TransitionVerdict,
    boundary_signature,
    frame_strip_plan,
)


def _b(**kw: Any) -> Boundary:
    base: dict[str, Any] = dict(
        timeline_id="t1", kind="rough_cut", asset_a="A", asset_b="B",
        src_in_a=0, src_out_a=30, src_in_b=0, src_out_b=30,
        seq_in_a=0, seq_out_a=30, removed_gap_frames=0, same_source=False,
    )
    base.update(kw)
    return Boundary(**base)


def test_datatypes_construct() -> None:
    fix = SuggestedFix(kind="transition", transition_style="crossfade", transition_frames=6)
    v = TransitionVerdict(smoothness=0.2, label="jump_cut", reason="x", suggested_fix=fix)
    assert v.suggested_fix.transition_style == "crossfade"


def test_signature_is_stable() -> None:
    b = _b()
    assert boundary_signature(b, 6, "pv1") == boundary_signature(b, 6, "pv1")


def test_signature_sensitive_to_source_and_k_and_proxy() -> None:
    base = boundary_signature(_b(src_out_a=30), 6, "pv1")
    assert boundary_signature(_b(src_out_a=31), 6, "pv1") != base  # src change
    assert boundary_signature(_b(src_out_a=30), 7, "pv1") != base  # k change
    assert boundary_signature(_b(src_out_a=30), 6, "pv2") != base  # proxy change


def test_signature_ignores_seq_position() -> None:
    # boundary_seq position is NOT part of identity (it drifts on upstream edits)
    assert boundary_signature(_b(seq_in_a=0, seq_out_a=30), 6, "pv1") == boundary_signature(
        _b(seq_in_a=500, seq_out_a=530), 6, "pv1"
    )


def test_frame_strip_plan_basic() -> None:
    b = _b(src_in_a=0, src_out_a=30, src_in_b=10, src_out_b=40)
    refs = frame_strip_plan(b, 4)
    assert refs == [
        ("A", 26), ("A", 27), ("A", 28), ("A", 29),  # last 4 of A, end-exclusive
        ("B", 10), ("B", 11), ("B", 12), ("B", 13),  # first 4 of B
    ]


def test_frame_strip_plan_short_clips() -> None:
    b = _b(src_in_a=0, src_out_a=3, src_in_b=0, src_out_b=2)
    assert frame_strip_plan(b, 6) == [("A", 0), ("A", 1), ("A", 2), ("B", 0), ("B", 1)]


def test_frame_strip_plan_last_a_frame_is_end_exclusive() -> None:
    b = _b(src_out_a=30)
    a_frames = [f for aid, f in frame_strip_plan(b, 6) if aid == "A"]
    assert max(a_frames) == 29  # src_out_a - 1
