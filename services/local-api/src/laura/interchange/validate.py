"""Export preflight: capability/degradation model (docs/07-interchange.md).

Before writing, report whether a target would silently lose information so the export
dialog can warn the user. EDL in particular is structurally limited.
"""

from __future__ import annotations

from typing import Any

from .timeline import Timeline

SUPPORTED = {"otio", "edl", "fcp7xml", "fcpxml", "srt", "vtt"}


def validate_export(
    timeline: Timeline, fmt: str, *, has_split: bool = False
) -> dict[str, Any]:
    """Export preflight. ``has_split`` flags an accepted L/J split edit (3b) so EDL — which has no
    single split-edit event — can warn that the split is emitted as parallel V/A events rather than
    one event (the closest faithful CMX3600 form)."""
    fmt = fmt.lower()
    if fmt not in SUPPORTED:
        return {
            "format": fmt, "ok": False, "lossy": False,
            "warnings": [f"unsupported export format: {fmt}"], "drops": [],
        }

    drops: list[str] = []
    warnings: list[str] = []

    if fmt == "edl":
        if len({c.lane for c in timeline.clips}) > 1:
            drops.append("multiple video lanes are flattened to one in EDL")
        if any(c.speaker_label for c in timeline.clips):
            drops.append("speaker labels are not represented in EDL")
        if any(
            (c.src_out_frame_exclusive - c.src_in_frame)
            != (c.seq_out_frame_exclusive - c.seq_in_frame)
            for c in timeline.clips
        ):
            drops.append("speed changes are not represented in plain CMX3600")
        if has_split:
            # CMX3600 has no single split-edit event; the L/J cut is emitted as parallel V/A events
            # on the offset sound cut — faithful, but not a one-event split.
            warnings.append(
                "L/J split edits are emitted as parallel V/A events (CMX3600 has no "
                "single split-edit event)"
            )

    if fmt == "fcpxml":
        warnings.append("FCPXML adapter is community-supported; verify the result in FCP")

    if fmt in {"srt", "vtt"} and not timeline.clips:
        warnings.append("timeline has no clips to caption")

    return {"format": fmt, "ok": True, "lossy": bool(drops),
            "warnings": warnings, "drops": drops}
