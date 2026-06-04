"""Canonical timeline model used by every exporter.

Frames are integers, ranges end-exclusive (ADR-0005). Exporters receive this model,
never UI state or raw DB rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Clip:
    name: str
    src_in_frame: int
    src_out_frame_exclusive: int
    seq_in_frame: int
    seq_out_frame_exclusive: int
    lane: int = 0
    asset_id: str | None = None
    source_url: str | None = None
    speaker_label: str | None = None
    speed_num: int = 1
    speed_den: int = 1


@dataclass(frozen=True)
class Timeline:
    name: str
    rate_num: int
    rate_den: int
    drop_frame: bool = False
    clips: list[Clip] = field(default_factory=list)

    def ordered(self) -> list[Clip]:
        """Clips sorted by sequence position (then lane) — the export order."""
        return sorted(self.clips, key=lambda c: (c.seq_in_frame, c.lane))


def _clip_source_url(source_path: str | None) -> str | None:
    """Map an asset's stored source_path to an export media reference.

    A URL-imported asset stores ``url:<url>`` until its download completes; while
    pending it has no local media, so exporters get no path (it's offline) rather
    than a malformed ``file://localhost/url:...`` reference.
    """
    if source_path is None or source_path.startswith("url:"):
        return None
    return source_path


def timeline_from_rows(
    timeline_row: dict[str, Any],
    clip_rows: list[dict[str, Any]],
    project_row: dict[str, Any],
    assets_by_id: dict[str, dict[str, Any]],
    speakers_by_id: dict[str, dict[str, Any]],
) -> Timeline:
    """Build the canonical model from DB rows (bridges persistence -> interchange)."""
    clips: list[Clip] = []
    for row in clip_rows:
        asset = assets_by_id.get(row["asset_id"], {})
        speaker = speakers_by_id.get(row["speaker_id"]) if row.get("speaker_id") else None
        clips.append(
            Clip(
                name=asset.get("display_name", row["asset_id"]),
                src_in_frame=row["src_in_frame"],
                src_out_frame_exclusive=row["src_out_frame_exclusive"],
                seq_in_frame=row["seq_in_frame"],
                seq_out_frame_exclusive=row["seq_out_frame_exclusive"],
                lane=row.get("lane", 0),
                asset_id=row["asset_id"],
                source_url=_clip_source_url(asset.get("source_path")),
                speaker_label=speaker.get("label") if speaker else None,
                speed_num=row.get("speed_num") or 1,
                speed_den=row.get("speed_den") or 1,
            )
        )
    return Timeline(
        name=timeline_row["name"],
        rate_num=project_row["sequence_rate_num"],
        rate_den=project_row["sequence_rate_den"],
        drop_frame=bool(project_row["drop_frame"]),
        clips=clips,
    )
