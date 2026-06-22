"""Ripple-track scene markers after a delete_range on the rough-cut timeline.

A scene is a marker pair ``(seq_in, seq_out_exclusive)`` over the continuous rough-cut.
When a sequence span ``[del_seq_in, del_seq_out_excl)`` is ripple-deleted (the SAME
geometry ``editing.operations.delete_range`` applies to clips), every marker frame moves
by the deleted length if it lay past the span, or collapses to ``del_seq_in`` if it lay
inside it. Pure: integer frames, end-exclusive (invariants #1/#2), no DB.
"""
from __future__ import annotations


def _shift_frame(f: int, del_in: int, del_out: int) -> int:
    """Project one sequence frame through a ripple delete of ``[del_in, del_out)``."""
    length = del_out - del_in
    if f <= del_in:
        return f
    if f >= del_out:
        return f - length
    return del_in  # inside the deleted span -> collapses to the cut point


def reconcile_after_delete(
    bounds: list[tuple[int, int]], del_seq_in: int, del_seq_out_excl: int
) -> list[tuple[int, int]]:
    """Return new scene bounds after ripple-deleting ``[del_seq_in, del_seq_out_excl)``.

    Shifts bounds past the span left by its length, collapses bounds inside the span,
    drops scenes that become zero-length, and preserves input order/contiguity.
    """
    if del_seq_out_excl < del_seq_in:
        raise ValueError("del_seq_out_excl must be >= del_seq_in")
    out: list[tuple[int, int]] = []
    for s_in, s_out in bounds:
        n_in = _shift_frame(s_in, del_seq_in, del_seq_out_excl)
        n_out = _shift_frame(s_out, del_seq_in, del_seq_out_excl)
        if n_out > n_in:  # drop zero-length scenes (fully inside the deleted span)
            out.append((n_in, n_out))
    return out
