"""FCP 7 XML (xmeml v5) writer — the recommended Premiere interchange path (docs/07).

Deterministic, dependency-light (stdlib ElementTree). Integer frame values map directly
to xmeml frame counts; the rate carries timebase (nominal) + ntsc (1001 denominator).
"""

from __future__ import annotations

from xml.etree.ElementTree import Element, SubElement, indent, tostring

from ..timebase import FrameRate
from .timeline import Clip, Timeline


def _rate(parent: Element, nominal: int, ntsc: str) -> None:
    rate = SubElement(parent, "rate")
    SubElement(rate, "timebase").text = str(nominal)
    SubElement(rate, "ntsc").text = ntsc


def _pathurl(source_url: str | None) -> str:
    if not source_url:
        return ""
    if source_url.startswith("file://"):
        return source_url
    return "file://localhost/" + source_url.replace("\\", "/").lstrip("/")


def timeline_to_fcp7_xml(timeline: Timeline) -> str:
    fr = FrameRate(timeline.rate_num, timeline.rate_den, timeline.drop_frame)
    nominal = fr.nominal
    ntsc = "TRUE" if timeline.rate_den == 1001 else "FALSE"
    seq_len = max((c.seq_out_frame_exclusive for c in timeline.clips), default=0)

    xmeml = Element("xmeml", version="5")
    sequence = SubElement(xmeml, "sequence")
    SubElement(sequence, "name").text = timeline.name
    SubElement(sequence, "duration").text = str(seq_len)
    _rate(sequence, nominal, ntsc)
    media = SubElement(sequence, "media")
    video = SubElement(media, "video")
    track = SubElement(video, "track")

    seen_files: set[str] = set()
    for i, clip in enumerate(timeline.ordered(), start=1):
        _clipitem(track, clip, i, nominal, ntsc, seen_files)

    indent(xmeml)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(xmeml, encoding="unicode") + "\n"


def _clipitem(
    track: Element, clip: Clip, index: int, nominal: int, ntsc: str, seen_files: set[str]
) -> None:
    src_len = clip.src_out_frame_exclusive - clip.src_in_frame
    item = SubElement(track, "clipitem", id=f"clipitem-{index}")
    SubElement(item, "name").text = clip.name
    SubElement(item, "duration").text = str(src_len)
    _rate(item, nominal, ntsc)
    SubElement(item, "in").text = str(clip.src_in_frame)
    SubElement(item, "out").text = str(clip.src_out_frame_exclusive)
    SubElement(item, "start").text = str(clip.seq_in_frame)
    SubElement(item, "end").text = str(clip.seq_out_frame_exclusive)

    file_id = f"file-{clip.asset_id or index}"
    if file_id in seen_files:
        SubElement(item, "file", id=file_id)
        return
    seen_files.add(file_id)
    file_el = SubElement(item, "file", id=file_id)
    SubElement(file_el, "name").text = clip.name
    SubElement(file_el, "pathurl").text = _pathurl(clip.source_url)
    _rate(file_el, nominal, ntsc)
    SubElement(file_el, "duration").text = str(src_len)
