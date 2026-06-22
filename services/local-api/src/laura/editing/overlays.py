"""Pure overlay precedence resolution for opaque replace-overlays.

A timeline has BASE clip rows (lane 0) and REPLACE-OVERLAY clip rows (lane >= 1,
role='replace').  Over each overlay's seq-range the overlay replaces the base; the
base survives only OUTSIDE overlay ranges (time-aligned opaque replace, 1:1 src/seq).

All frame indices are integers, end-exclusive.
"""
from __future__ import annotations

from typing import Any


def _subtract_intervals(
    seq_in: int,
    seq_out: int,
    overlays: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Subtract a sorted, non-overlapping list of ``(lo, hi)`` intervals from ``[seq_in, seq_out)``.

    Returns a list of surviving ``(lo, hi)`` sub-intervals, each non-empty.
    """
    result: list[tuple[int, int]] = []
    cursor = seq_in
    for o_lo, o_hi in overlays:
        if o_hi <= cursor:
            continue
        if o_lo >= seq_out:
            break
        # surviving piece before this overlay
        clipped_lo = max(o_lo, cursor)
        if cursor < clipped_lo:
            result.append((cursor, clipped_lo))
        cursor = max(cursor, o_hi)
    # surviving tail after last overlay
    if cursor < seq_out:
        result.append((cursor, seq_out))
    return result


def apply_overlay_precedence(
    base_rows: list[dict[str, Any]],
    overlay_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return a flat, seq-ordered list of clip rows after applying replace-overlay precedence.

    Each overlay row is emitted as-is.  For every base row, the union of overlapping
    overlay seq-ranges is subtracted, yielding surviving sub-segments whose src frames
    are recalculated using the 1:1 src/seq mapping.  Zero-length segments are dropped.
    The result is sorted by ``seq_in_frame``.
    """
    if not overlay_rows:
        return list(base_rows)

    # Build sorted list of overlay seq-ranges (no mutual overlap assumed).
    overlay_spans: list[tuple[int, int]] = sorted(
        (int(r["seq_in_frame"]), int(r["seq_out_frame_exclusive"])) for r in overlay_rows
    )

    output: list[dict[str, Any]] = list(overlay_rows)

    for base in base_rows:
        b_seq_in: int = int(base["seq_in_frame"])
        b_seq_out: int = int(base["seq_out_frame_exclusive"])
        b_src_in: int = int(base["src_in_frame"])

        # Keep only overlays that intersect this base row's seq-range.
        relevant: list[tuple[int, int]] = [
            (lo, hi)
            for lo, hi in overlay_spans
            if lo < b_seq_out and hi > b_seq_in
        ]

        surviving = _subtract_intervals(b_seq_in, b_seq_out, relevant)

        for s, e in surviving:
            offset = s - b_seq_in
            sub: dict[str, Any] = {
                **base,
                "seq_in_frame": s,
                "seq_out_frame_exclusive": e,
                "src_in_frame": b_src_in + offset,
                "src_out_frame_exclusive": b_src_in + (e - b_seq_in),
            }
            output.append(sub)

    output.sort(key=lambda r: int(r["seq_in_frame"]))
    return output
