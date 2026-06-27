"""OpenTimelineIO read/write — the canonical interchange (ADR-0001).

Uses the real OTIO library so output is schema-valid and round-trips through other
tools. Integer frame indices are preserved as exact RationalTime values.

Multi-lane mapping (spec §4.2):
    One OTIO video track per occupied lane, lane index → track order (low = bottom).
    Track names: lane 0 → "V1", lane 1 → "V2", etc.
    With only lane 0 occupied the output is byte-identical to the single-track OTIO
    Laura built before multi-lane support (single-lane backcompat gate, spec §4.4/§9.1-R1).
"""

from __future__ import annotations

import opentimelineio as otio

from .timeline import Clip, Timeline


def _clips_on_lane(clips: list[Clip], lane: int) -> list[Clip]:
    """Return clips on ``lane`` sorted by seq_in_frame (mirrors operations.clips_on_lane)."""
    return sorted((c for c in clips if c.lane == lane), key=lambda c: c.seq_in_frame)


def _fill_video_track(
    track: otio.schema.Track, lane_clips: list[Clip], rate: float
) -> None:
    """Fill one OTIO video track from a single lane's clip list.

    Gaps are inserted wherever the playhead lags behind the next clip's seq_in.
    Retimed clips carry a LinearTimeWarp plus exact rational metadata (spec §4.2).
    Intra-lane overlap is forbidden by P1 so seq_in >= playhead is always true.
    """
    playhead = 0
    for clip in lane_clips:
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
        # 1/1 clips keep their source duration (== sequence); retimed clips occupy their
        # sequence span and carry a LinearTimeWarp plus exact rational metadata so the
        # canonical fields survive a round-trip (OTIO retiming is otherwise lossy).
        retimed = clip.speed_num != clip.speed_den
        seq_len = clip.seq_out_frame_exclusive - clip.seq_in_frame
        src_len = clip.src_out_frame_exclusive - clip.src_in_frame
        source_range = otio.opentime.TimeRange(
            otio.opentime.RationalTime(clip.src_in_frame, rate),
            otio.opentime.RationalTime(seq_len if retimed else src_len, rate),
        )
        media = (
            otio.schema.ExternalReference(target_url=clip.source_url)
            if clip.source_url
            else otio.schema.MissingReference()
        )
        otio_clip = otio.schema.Clip(
            name=clip.name, source_range=source_range, media_reference=media
        )
        if retimed:
            otio_clip.effects.append(
                otio.schema.LinearTimeWarp(time_scalar=clip.speed_num / clip.speed_den)
            )
            otio_clip.metadata["laura"] = {
                "speed_num": clip.speed_num,
                "speed_den": clip.speed_den,
                "src_in_frame": clip.src_in_frame,
                "src_out_frame_exclusive": clip.src_out_frame_exclusive,
            }
        track.append(otio_clip)
        playhead = clip.seq_out_frame_exclusive


def timeline_to_otio_string(tl: Timeline) -> str:
    """Serialise a Timeline to OTIO JSON.

    Emits one video track per occupied lane (lane index → track name "V1", "V2", …).
    With only lane 0 occupied the output is byte-identical to the pre-multi-lane writer
    (same track name "V1", no empty additional tracks) — single-lane backcompat gate.
    """
    rate = tl.rate_num / tl.rate_den
    timeline = otio.schema.Timeline(name=tl.name)

    # Determine which lanes are actually occupied, in ascending order.
    occupied_lanes = sorted({c.lane for c in tl.clips})
    if not occupied_lanes:
        # Empty timeline: emit a single empty V1 track (matches prior behaviour).
        occupied_lanes = [0]

    for lane in occupied_lanes:
        track_name = f"V{lane + 1}"
        track = otio.schema.Track(name=track_name, kind=otio.schema.TrackKind.Video)
        timeline.tracks.append(track)
        lane_clips = _clips_on_lane(tl.clips, lane)
        _fill_video_track(track, lane_clips, rate)

    result: str = otio.adapters.write_to_string(timeline, "otio_json")
    return result


def otio_string_to_timeline(
    text: str, *, rate_num: int, rate_den: int, drop_frame: bool = False
) -> Timeline:
    """Parse OTIO JSON back into the canonical model (used for round-trip tests).

    Multi-lane: enumerates video tracks and assigns each clip's lane from the track index
    (spec §4.3).  ``range_in_parent()`` gives the absolute seq position within the track
    (gaps count into the cumulative position) so seq_in_frame is correct per lane.
    """
    timeline = otio.adapters.read_from_string(text, "otio_json")
    clips: list[Clip] = []

    # Enumerate video tracks only; lane = track index among video tracks (0-based).
    video_tracks = [
        t for t in timeline.tracks
        if t.kind == otio.schema.TrackKind.Video
    ]
    for lane, track in enumerate(video_tracks):
        for clip in track.find_clips():
            rec = clip.range_in_parent()
            src = clip.source_range
            seq_in = int(rec.start_time.value)
            seq_dur = int(rec.duration.value)
            meta = dict(clip.metadata).get("laura") if clip.metadata else None
            if meta:
                # exact canonical fields preserved across the retime round-trip
                src_in = int(meta["src_in_frame"])
                src_out = int(meta["src_out_frame_exclusive"])
                speed_num = int(meta.get("speed_num", 1))
                speed_den = int(meta.get("speed_den", 1))
            else:
                src_in = int(src.start_time.value)
                src_out = src_in + int(src.duration.value)
                speed_num = speed_den = 1
            clips.append(
                Clip(
                    name=str(clip.name),
                    src_in_frame=src_in,
                    src_out_frame_exclusive=src_out,
                    seq_in_frame=seq_in,
                    seq_out_frame_exclusive=seq_in + seq_dur,
                    lane=lane,
                    source_url=getattr(clip.media_reference, "target_url", None),
                    speed_num=speed_num,
                    speed_den=speed_den,
                )
            )

    return Timeline(
        name=str(timeline.name), rate_num=rate_num, rate_den=rate_den,
        drop_frame=drop_frame, clips=clips,
    )
