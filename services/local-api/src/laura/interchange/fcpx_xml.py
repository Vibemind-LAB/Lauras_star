"""FCPXML (Final Cut Pro X) writer — guarded path (docs/07).

Community-supported format with real adapter risk; we emit a minimal, schema-shaped
document deterministically. FCPXML uses rational time strings ("value/timescaleS").
"""

from __future__ import annotations

from xml.etree.ElementTree import Element, SubElement, indent, tostring

from .timeline import Timeline


def _pathurl(source_url: str | None) -> str:
    if not source_url:
        return ""
    if source_url.startswith("file://"):
        return source_url
    return "file://localhost/" + source_url.replace("\\", "/").lstrip("/")


def _frame_duration(rate_num: int, rate_den: int) -> str:
    return f"{rate_den}/{rate_num}s"


def _time(frames: int, rate_num: int, rate_den: int) -> str:
    if frames == 0:
        return "0s"
    return f"{frames * rate_den}/{rate_num}s"


def timeline_to_fcpx_xml(timeline: Timeline) -> str:
    fcpxml = Element("fcpxml", version="1.9")
    resources = SubElement(fcpxml, "resources")
    SubElement(
        resources, "format", id="r1", name="LauraFormat",
        frameDuration=_frame_duration(timeline.rate_num, timeline.rate_den),
    )

    asset_ids: dict[str, str] = {}
    for clip in timeline.ordered():
        key = clip.asset_id or clip.name
        if key not in asset_ids:
            aid = f"a{len(asset_ids) + 1}"
            asset_ids[key] = aid
            SubElement(
                resources, "asset", id=aid, name=clip.name,
                src=_pathurl(clip.source_url), hasVideo="1", format="r1",
            )

    library = SubElement(fcpxml, "library")
    event = SubElement(library, "event", name="Laura")
    project = SubElement(event, "project", name=timeline.name)
    sequence = SubElement(project, "sequence", format="r1")
    spine = SubElement(sequence, "spine")
    for clip in timeline.ordered():
        key = clip.asset_id or clip.name
        SubElement(
            spine, "asset-clip", ref=asset_ids[key], name=clip.name,
            offset=_time(clip.seq_in_frame, timeline.rate_num, timeline.rate_den),
            duration=_time(
                clip.src_out_frame_exclusive - clip.src_in_frame,
                timeline.rate_num, timeline.rate_den,
            ),
            start=_time(clip.src_in_frame, timeline.rate_num, timeline.rate_den),
        )

    indent(fcpxml)
    body = tostring(fcpxml, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + body + "\n"
