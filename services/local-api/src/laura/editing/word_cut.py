"""Map an asset source-frame range onto a timeline's sequence frames (speed=1) so a
transcript word selection can be ripple-deleted. Pure; operates on EditClip lists."""
from __future__ import annotations

from .operations import EditClip


def map_asset_range_to_seq(
    clips: list[EditClip], *, asset_id: str, src_lo: int, src_hi: int
) -> tuple[int, int] | None:
    """Sequence span covering the asset frames ``[src_lo, src_hi)`` across clips of
    ``asset_id``. Returns ``(seq_in, seq_out_exclusive)`` or ``None`` if nothing overlaps."""
    seq_in: int | None = None
    seq_out: int | None = None
    for c in clips:
        if c.asset_id != asset_id:
            continue
        if c.src_out_frame_exclusive <= src_lo or c.src_in_frame >= src_hi:
            continue
        lo = max(src_lo, c.src_in_frame)
        hi = min(src_hi, c.src_out_frame_exclusive)
        s_in = c.seq_in_frame + (lo - c.src_in_frame)
        s_out = c.seq_in_frame + (hi - c.src_in_frame)
        seq_in = s_in if seq_in is None else min(seq_in, s_in)
        seq_out = s_out if seq_out is None else max(seq_out, s_out)
    if seq_in is None or seq_out is None or seq_out <= seq_in:
        return None
    return (seq_in, seq_out)
