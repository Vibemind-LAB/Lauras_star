"""FCPXML (Final Cut Pro X) writer — guarded path (docs/07).

Community-supported format with real adapter risk; we emit a minimal, schema-shaped
document deterministically. FCPXML uses rational time strings ("value/timescaleS").
"""

from __future__ import annotations

from xml.etree.ElementTree import Element, SubElement, indent, tostring

from .timeline import Clip, Timeline


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


def timeline_to_fcpx_xml(
    timeline: Timeline, audio_clips: list[Clip] | None = None
) -> str:
    """Emit FCPXML (Final Cut Pro X).

    ``audio_clips`` carries the L/J split-edit representation (3b): when given, each picture
    ``asset-clip`` carries a connected audio clip on ``lane="-1"`` whose ``offset`` (sequence
    position) and ``start`` (source in) sit on the OFFSET audio frame, while the picture clip on the
    primary spine stays frame-exact on the visual cut — FCPXML's split-edit form. When ``None`` (no
    accepted split) the document is byte-for-byte the single-track hard cut Laura emits today.
    """
    rate_num, rate_den = timeline.rate_num, timeline.rate_den
    fcpxml = Element("fcpxml", version="1.9")
    resources = SubElement(fcpxml, "resources")
    SubElement(
        resources, "format", id="r1", name="LauraFormat",
        frameDuration=_frame_duration(rate_num, rate_den),
    )

    asset_ids: dict[str, str] = {}

    def _ref(clip: Clip, has_audio: bool = False) -> str:
        key = clip.asset_id or clip.name
        aid = asset_ids.get(key)
        if aid is None:
            aid = f"a{len(asset_ids) + 1}"
            asset_ids[key] = aid
            asset = SubElement(
                resources, "asset", id=aid, name=clip.name,
                src=_pathurl(clip.source_url), format="r1", hasVideo="1",
            )
            if has_audio:
                asset.set("hasAudio", "1")
        return aid

    # The split-shifted audio clips are returned positionally aligned with the picture clips (one
    # per picture clip, same order), so pair them by index — NOT by sequence frame, which the L/J
    # offset has shifted. Empty (the common case) leaves the spine a plain single-track hard cut.
    ordered = timeline.ordered()
    audio_for: list[Clip | None] = list(audio_clips) if audio_clips else [None] * len(ordered)

    library = SubElement(fcpxml, "library")
    event = SubElement(library, "event", name="Laura")
    project = SubElement(event, "project", name=timeline.name)
    sequence = SubElement(project, "sequence", format="r1")
    spine = SubElement(sequence, "spine")
    for clip, ac in zip(ordered, audio_for, strict=True):
        ref = _ref(clip, has_audio=bool(audio_clips))
        item = SubElement(
            spine, "asset-clip", ref=ref, name=clip.name,
            offset=_time(clip.seq_in_frame, rate_num, rate_den),
            duration=_time(clip.src_out_frame_exclusive - clip.src_in_frame, rate_num, rate_den),
            start=_time(clip.src_in_frame, rate_num, rate_den),
        )
        # Pair the picture clip with its split-shifted audio as a CONNECTED clip on lane -1. The
        # audio's own offset/start land on the OFFSET frame (the L/J edit) while the picture above
        # stays frame-exact.
        if ac is not None:
            SubElement(
                item, "asset-clip", ref=ref, name=ac.name, lane="-1",
                offset=_time(ac.seq_in_frame, rate_num, rate_den),
                duration=_time(
                    ac.src_out_frame_exclusive - ac.src_in_frame, rate_num, rate_den
                ),
                start=_time(ac.src_in_frame, rate_num, rate_den),
                audioRole="dialogue",
            )

    indent(fcpxml)
    body = tostring(fcpxml, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + body + "\n"
