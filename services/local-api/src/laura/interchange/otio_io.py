"""OpenTimelineIO read/write — the canonical interchange (ADR-0001).

Uses the real OTIO library so output is schema-valid and round-trips through other
tools. Integer frame indices are preserved as exact RationalTime values.
"""

from __future__ import annotations

import opentimelineio as otio

from .timeline import Clip, Timeline


def timeline_to_otio_string(tl: Timeline) -> str:
    rate = tl.rate_num / tl.rate_den
    timeline = otio.schema.Timeline(name=tl.name)
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    timeline.tracks.append(track)

    playhead = 0
    for clip in tl.ordered():
        if clip.seq_in_frame > playhead:
            gap = clip.seq_in_frame - playhead
            track.append(
                otio.schema.Gap(
                    source_range=otio.opentime.TimeRange(
                        otio.opentime.RationalTime(0, rate),
                        otio.opentime.RationalTime(gap, rate),
                    )
                )
            )
        duration = clip.src_out_frame_exclusive - clip.src_in_frame
        source_range = otio.opentime.TimeRange(
            otio.opentime.RationalTime(clip.src_in_frame, rate),
            otio.opentime.RationalTime(duration, rate),
        )
        media = (
            otio.schema.ExternalReference(target_url=clip.source_url)
            if clip.source_url
            else otio.schema.MissingReference()
        )
        track.append(otio.schema.Clip(name=clip.name, source_range=source_range,
                                      media_reference=media))
        playhead = clip.seq_out_frame_exclusive

    result: str = otio.adapters.write_to_string(timeline, "otio_json")
    return result


def otio_string_to_timeline(
    text: str, *, rate_num: int, rate_den: int, drop_frame: bool = False
) -> Timeline:
    """Parse OTIO JSON back into the canonical model (used for round-trip tests)."""
    timeline = otio.adapters.read_from_string(text, "otio_json")
    clips: list[Clip] = []
    for clip in timeline.find_clips():
        rec = clip.range_in_parent()
        src = clip.source_range
        seq_in = int(rec.start_time.value)
        seq_dur = int(rec.duration.value)
        src_in = int(src.start_time.value)
        src_dur = int(src.duration.value)
        clips.append(
            Clip(
                name=str(clip.name),
                src_in_frame=src_in,
                src_out_frame_exclusive=src_in + src_dur,
                seq_in_frame=seq_in,
                seq_out_frame_exclusive=seq_in + seq_dur,
                source_url=getattr(clip.media_reference, "target_url", None),
            )
        )
    return Timeline(
        name=str(timeline.name), rate_num=rate_num, rate_den=rate_den,
        drop_frame=drop_frame, clips=clips,
    )
