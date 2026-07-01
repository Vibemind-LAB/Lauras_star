"""Represent accepted L/J split edits in the OTIO timeline — migration-free.

The split-edit planner (:mod:`laura.analysis.splitedit`) RECOMMENDS, per inter-clip cut, an
independent picture frame (visual peak) and sound frame (real silence / clean word-gap). This
module makes an *accepted* recommendation REAL in Laura's source of truth — the OTIO timeline
(invariant #6) — without touching the database schema.

A *hard* cut switches picture and sound on the same frame: a single video track, audio coincident.
A *split* cut moves the sound boundary off the picture boundary by ``offset = audio_frame -
video_frame`` frames:

* **J-cut** (``offset < 0``) — the next shot's audio starts *before* its picture. The audio-track
  boundary lands ``|offset|`` frames *earlier* than the video-track boundary.
* **L-cut** (``offset > 0``) — the previous shot's audio *trails* past the picture cut. The
  audio-track boundary lands ``offset`` frames *later* than the video-track boundary.

The representation is two parallel tracks built from the SAME canonical clip model:

* **V1 (video)** — byte-for-byte the single-track OTIO Laura builds today: video boundaries stay
  frame-exact on the visual cut.
* **A1 (audio)** — the same clips, but every accepted inter-clip boundary is shifted by its
  ``offset`` frames. Audio is canonical in SAMPLES (invariant #3): each audio clip carries its
  exact sample IN/OUT in ``metadata["laura"]`` so the projection is reproducible and the frame
  boundary is only the UI view of it.

Persistence is migration-free and self-describing: the accepted offsets are stored on the OTIO
timeline's own ``metadata["laura"]["accepted_split_offsets"]`` (a list of ``{seq_cut, offset}``),
so the blob fully describes its split state and a degrade to a hard cut is just an empty list. The
stored ``timelines.otio_json`` column is a CACHE regenerated from the clips table on every edit
(see :mod:`laura.editing.otio_sync`), so the accepted offsets must be re-applied at build time from
this same list — :func:`accepted_offsets_from_otio` reads them back out of the previous blob.

With no accepted splits the audio track is identical to the video track and the timeline is exactly
what Laura builds today (additive + optional).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import opentimelineio as otio

from ..interchange.timeline import Clip, Timeline
from ..timebase.sampling import frame_to_sample

# A split whose audio/video boundary differ by at most this many frames is treated as a hard cut —
# mirrors ``splitedit.HARD_OFFSET_TOLERANCE`` (a 1-frame split is below the perception threshold).
HARD_OFFSET_TOLERANCE = 1

_LAURA_NS = "laura"
_ACCEPTED_KEY = "accepted_split_offsets"


class AcceptedSplit:
    """An accepted split offset keyed to an inter-clip cut, ready to apply at OTIO-build time.

    ``seq_cut`` is the source-frame cut that identifies the boundary (it equals the next clip's
    ``src_in_frame``, exactly the planner's :attr:`laura.analysis.splitedit.SplitCut.seq_cut`).
    ``offset = audio_frame - video_frame`` in frames; ``> 0`` is an L-cut (audio later), ``< 0`` a
    J-cut (audio earlier), and ``|offset| <= HARD_OFFSET_TOLERANCE`` a hard cut (no shift).
    """

    __slots__ = ("seq_cut", "offset")

    def __init__(self, seq_cut: int, offset: int) -> None:
        self.seq_cut = int(seq_cut)
        self.offset = int(offset)

    def is_hard(self) -> bool:
        return abs(self.offset) <= HARD_OFFSET_TOLERANCE

    def to_dict(self) -> dict[str, int]:
        return {"seq_cut": self.seq_cut, "offset": self.offset}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AcceptedSplit:
        return cls(seq_cut=int(raw["seq_cut"]), offset=int(raw["offset"]))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AcceptedSplit):
            return NotImplemented
        return self.seq_cut == other.seq_cut and self.offset == other.offset

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"AcceptedSplit(seq_cut={self.seq_cut}, offset={self.offset})"


def _offsets_by_seq_cut(splits: list[AcceptedSplit]) -> dict[int, int]:
    """Index the meaningful (non-hard) offsets by their identifying source-frame cut.

    Hard offsets are dropped: they shift nothing, so the audio boundary stays coincident with the
    video boundary — exactly the no-split timeline. The last entry for a ``seq_cut`` wins.
    """
    return {s.seq_cut: s.offset for s in splits if not s.is_hard()}


def _lane_clips(tl: Timeline, lane: int) -> list[Clip]:
    """Return the clips on ``lane`` sorted by seq_in_frame (mirrors operations.clips_on_lane)."""
    return sorted((c for c in tl.clips if c.lane == lane), key=lambda c: c.seq_in_frame)


def apply_split_cuts(
    tl: Timeline,
    splits: list[AcceptedSplit],
    *,
    audio_sample_rate: int | None = None,
) -> str:
    """Serialise ``tl`` to OTIO with accepted L/J splits as a separate audio track.

    Builds one video track per occupied lane (V1 for lane 0, V2 for lane 1, …) and, when any
    meaningful split is accepted, a parallel audio track (A1) whose inter-clip boundaries are
    shifted by the accepted per-cut ``offset`` frames.  The audio track is always Lane-0-only
    (spec §4.4 — only Lane-0 clips carry L/J offsets).

    With only lane 0 occupied AND ``splits`` empty (or all hard) the output is byte-for-byte
    the single-track OTIO Laura built before multi-lane support — the representation is purely
    additive (single-lane backcompat gate, spec §4.4/§9.1-R1).
    """
    rate = tl.rate_num / tl.rate_den
    offsets = _offsets_by_seq_cut(splits)

    timeline = otio.schema.Timeline(name=tl.name)

    # Determine occupied video lanes in ascending order.
    occupied_lanes = sorted({c.lane for c in tl.clips})
    if not occupied_lanes:
        occupied_lanes = [0]

    # Emit one video track per occupied lane (V1, V2, …).
    for lane in occupied_lanes:
        track_name = f"V{lane + 1}"
        video_track = otio.schema.Track(name=track_name, kind=otio.schema.TrackKind.Video)
        timeline.tracks.append(video_track)
        _fill_video_track(video_track, _lane_clips(tl, lane), rate)

    # Audio track (A1) uses Lane-0 clips only — L/J splits are a Lane-0 concept (spec §4.4).
    lane0_clips = _lane_clips(tl, 0)
    if offsets:
        audio_track = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
        timeline.tracks.append(audio_track)
        _fill_audio_track(
            audio_track,
            lane0_clips,
            offsets,
            rate=rate,
            rate_num=tl.rate_num,
            rate_den=tl.rate_den,
            audio_sample_rate=audio_sample_rate,
        )
        # Self-describing persistence: the accepted offsets live on the timeline blob itself, so a
        # later regenerate-from-clips can re-apply them. Only attached when there IS a split — with
        # no meaningful offset the output stays byte-for-byte the single-track OTIO Laura builds
        # today (purely additive).
        timeline.metadata[_LAURA_NS] = {
            _ACCEPTED_KEY: [AcceptedSplit(c, o).to_dict() for c, o in sorted(offsets.items())]
        }

    result: str = otio.adapters.write_to_string(timeline, "otio_json")
    return result


def _fill_video_track(track: Any, clips: list[Any], rate: float) -> None:
    """Append the video clips/gaps for a single lane — identical to the single-track OTIO logic."""
    playhead = 0
    for clip in clips:
        if clip.seq_in_frame > playhead:
            track.append(_gap(clip.seq_in_frame - playhead, rate))
        track.append(_video_clip(clip, rate))
        playhead = clip.seq_out_frame_exclusive


def _fill_audio_track(
    track: Any,
    clips: list[Any],
    offsets: dict[int, int],
    *,
    rate: float,
    rate_num: int,
    rate_den: int,
    audio_sample_rate: int | None,
) -> None:
    """Append the audio clips, shifting each accepted inter-clip boundary by its offset.

    Boundary ``i`` (between clip ``i-1`` and clip ``i``) is the cut identified by
    ``clips[i].src_in_frame``. When that cut has an accepted ``offset`` the boundary frame becomes
    ``seq_in_frame + offset`` on BOTH the trailing edge of clip ``i-1`` (its seq-out) and the
    leading edge of clip ``i`` (its seq-in). Video boundaries are untouched; only the sound moves.
    """
    n = len(clips)
    for i, clip in enumerate(clips):
        # leading boundary shift: this clip's audio-in moves by the accepted offset of its own cut
        # (the cut between the previous clip and this one), 0 for the very first clip.
        lead_shift = 0 if i == 0 else offsets.get(clips[i].src_in_frame, 0)
        # trailing boundary shift: this clip's audio-out moves by the accepted offset of the NEXT
        # cut (the boundary it shares with the following clip), 0 for the last clip.
        trail_shift = 0 if i == n - 1 else offsets.get(clips[i + 1].src_in_frame, 0)

        # An OTIO track is SEQUENTIAL: a clip's position is the sum of the durations before it, not
        # an explicit frame. So a moved boundary is expressed purely as durations — the previous
        # audio clip grows/shrinks by the offset and this one shrinks/grows by the SAME offset, and
        # the two stay back-to-back (no inter-clip gap). The cumulative positions then land on the
        # shifted boundary: an L-cut (offset > 0) lengthens the previous audio and starts this one
        # later in its source; a J-cut (offset < 0) shortens the previous audio and starts this one
        # earlier. Only the very first clip may need a leading gap to sit off sequence frame 0.
        audio_seq_in = clip.seq_in_frame + lead_shift
        audio_seq_out = clip.seq_out_frame_exclusive + trail_shift
        # The source range follows the sequence shift exactly (audio and picture share a source).
        audio_src_in = clip.src_in_frame + lead_shift
        audio_len = audio_seq_out - audio_seq_in

        if i == 0 and audio_seq_in > 0:
            track.append(_gap(audio_seq_in, rate))

        track.append(
            _audio_clip(
                clip,
                audio_src_in=audio_src_in,
                audio_len=audio_len,
                rate=rate,
                rate_num=rate_num,
                rate_den=rate_den,
                audio_sample_rate=audio_sample_rate,
            )
        )


def _gap(length: int, rate: float) -> Any:
    return otio.schema.Gap(
        source_range=otio.opentime.TimeRange(
            otio.opentime.RationalTime(0, rate),
            otio.opentime.RationalTime(length, rate),
        )
    )


def _media_reference(clip: Any) -> Any:
    return (
        otio.schema.ExternalReference(target_url=clip.source_url)
        if clip.source_url
        else otio.schema.MissingReference()
    )


def _video_clip(clip: Any, rate: float) -> Any:
    """One video clip — the same construction as :func:`laura.interchange.otio_io`."""
    retimed = clip.speed_num != clip.speed_den
    seq_len = clip.seq_out_frame_exclusive - clip.seq_in_frame
    src_len = clip.src_out_frame_exclusive - clip.src_in_frame
    source_range = otio.opentime.TimeRange(
        otio.opentime.RationalTime(clip.src_in_frame, rate),
        otio.opentime.RationalTime(seq_len if retimed else src_len, rate),
    )
    otio_clip = otio.schema.Clip(
        name=clip.name, source_range=source_range, media_reference=_media_reference(clip)
    )
    if retimed:
        otio_clip.effects.append(
            otio.schema.LinearTimeWarp(time_scalar=clip.speed_num / clip.speed_den)
        )
        otio_clip.metadata[_LAURA_NS] = {
            "speed_num": clip.speed_num,
            "speed_den": clip.speed_den,
            "src_in_frame": clip.src_in_frame,
            "src_out_frame_exclusive": clip.src_out_frame_exclusive,
        }
    return otio_clip


def _audio_clip(
    clip: Any,
    *,
    audio_src_in: int,
    audio_len: int,
    rate: float,
    rate_num: int,
    rate_den: int,
    audio_sample_rate: int | None,
) -> Any:
    """One audio clip on the split-shifted boundary, carrying exact samples (invariant #3).

    The audio clip's frame range is the picture clip's range shifted by the accepted boundary
    offsets. Audio is canonical in samples, so the frame IN/OUT are projected to samples through
    :func:`laura.timebase.sampling.frame_to_sample` and stored exactly in ``metadata["laura"]``;
    the frame boundary is only the projection of those samples for the UI.
    """
    source_range = otio.opentime.TimeRange(
        otio.opentime.RationalTime(audio_src_in, rate),
        otio.opentime.RationalTime(audio_len, rate),
    )
    otio_clip = otio.schema.Clip(
        name=clip.name, source_range=source_range, media_reference=_media_reference(clip)
    )
    meta: dict[str, Any] = {
        "track": "audio",
        "src_in_frame": audio_src_in,
        "src_out_frame_exclusive": audio_src_in + audio_len,
    }
    if audio_sample_rate:
        meta["src_in_sample"] = frame_to_sample(
            audio_src_in, audio_sample_rate, rate_num, rate_den
        )
        meta["src_out_sample"] = frame_to_sample(
            audio_src_in + audio_len, audio_sample_rate, rate_num, rate_den
        )
        meta["audio_sample_rate"] = audio_sample_rate
    otio_clip.metadata[_LAURA_NS] = meta
    return otio_clip


@dataclass(frozen=True)
class AudioClip:
    """One split-shifted audio clip, the model-level analog of 3a's A1 OTIO audio clip.

    Same canonical fields as a picture :class:`~laura.interchange.timeline.Clip` but with every
    accepted inter-clip boundary shifted by its ``offset`` frames, so the NLE writers (EDL,
    FCP7-XML, FCPXML) can emit a separate audio track whose edit points carry the L/J split. Audio
    is canonical in SAMPLES (invariant #3): ``src_in_sample`` / ``src_out_sample`` carry the exact
    sample IN/OUT when an audio sample rate is known, the frame fields being the UI projection.

    The boundary math is identical to :func:`_fill_audio_track`, so the model-level export track and
    3a's OTIO audio track always agree on where the sound edit lands.
    """

    clip: Clip
    src_in_sample: int | None = None
    src_out_sample: int | None = None


def split_audio_clips(
    tl: Timeline,
    splits: list[AcceptedSplit],
    *,
    audio_sample_rate: int | None = None,
) -> list[AudioClip]:
    """Derive the split-shifted audio clips for an NLE export (model-level analog of 3a's A1 track).

    Returns ``[]`` when no meaningful (non-hard) split is accepted — the export then stays a single
    A+V hard cut, byte-for-byte what Laura emits today (purely additive). Otherwise returns one
    :class:`AudioClip` per picture clip whose inter-clip boundaries are shifted by the accepted
    per-cut ``offset`` frames, sharing the exact boundary math of 3a's :func:`_fill_audio_track`:

    * an **L-cut** (``offset > 0``) lengthens the previous audio and starts the next later,
    * a **J-cut** (``offset < 0``) shortens the previous audio and starts the next earlier,

    while the picture clips stay frame-exact on the visual cut. With ``audio_sample_rate`` given,
    each clip carries its exact sample IN/OUT (invariant #3); otherwise the projection is omitted.
    """
    offsets = _offsets_by_seq_cut(splits)
    if not offsets:
        return []

    # L/J splits are Lane-0-only (spec §4.4 — only Lane-0 clips carry inter-clip offsets).
    clips = _lane_clips(tl, 0)
    n = len(clips)
    out: list[AudioClip] = []
    for i, clip in enumerate(clips):
        # leading/trailing boundary shifts — identical to _fill_audio_track so the model-level
        # audio track and 3a's OTIO A1 track land the sound edit on exactly the same frame.
        lead_shift = 0 if i == 0 else offsets.get(clips[i].src_in_frame, 0)
        trail_shift = 0 if i == n - 1 else offsets.get(clips[i + 1].src_in_frame, 0)

        audio_seq_in = clip.seq_in_frame + lead_shift
        audio_seq_out = clip.seq_out_frame_exclusive + trail_shift
        audio_src_in = clip.src_in_frame + lead_shift
        audio_len = audio_seq_out - audio_seq_in
        audio_src_out = audio_src_in + audio_len

        in_sample: int | None = None
        out_sample: int | None = None
        if audio_sample_rate:
            in_sample = frame_to_sample(
                audio_src_in, audio_sample_rate, tl.rate_num, tl.rate_den
            )
            out_sample = frame_to_sample(
                audio_src_out, audio_sample_rate, tl.rate_num, tl.rate_den
            )

        out.append(
            AudioClip(
                clip=Clip(
                    name=clip.name,
                    src_in_frame=audio_src_in,
                    src_out_frame_exclusive=audio_src_out,
                    seq_in_frame=audio_seq_in,
                    seq_out_frame_exclusive=audio_seq_out,
                    lane=clip.lane,
                    asset_id=clip.asset_id,
                    source_url=clip.source_url,
                    speaker_label=clip.speaker_label,
                    speed_num=clip.speed_num,
                    speed_den=clip.speed_den,
                ),
                src_in_sample=in_sample,
                src_out_sample=out_sample,
            )
        )
    return out


def accepted_offsets_from_otio(text: str) -> list[AcceptedSplit]:
    """Read the accepted split offsets back out of an OTIO blob (empty when there are none).

    This is how the rebuild path recovers the split state from the PREVIOUS ``timelines.otio_json``
    when it regenerates the blob from the clips table — without it a regenerate would clobber the
    accepted splits back to hard cuts. Any malformed / absent metadata degrades to ``[]`` (a hard
    cut), never an error: a missing split is a graceful hard cut, not a broken timeline.
    """
    try:
        timeline = otio.adapters.read_from_string(text, "otio_json")
    except Exception:  # noqa: BLE001 - an unreadable blob just has no recoverable splits
        return []
    # A default/empty blob (e.g. "{}") deserialises to a bare AnyDictionary, not a Timeline — there
    # is nothing to recover from it. Guard on the schema before touching ``.metadata``.
    if not isinstance(timeline, otio.schema.Timeline):
        return []
    # OTIO deserialises nested metadata as AnyDictionary / AnyVector (Mapping / Sequence, NOT the
    # built-in dict / list), so match the abstract types — never isinstance(dict)/(list) here.
    meta = dict(timeline.metadata).get(_LAURA_NS) if timeline.metadata else None
    if not isinstance(meta, Mapping):
        return []
    raw = meta.get(_ACCEPTED_KEY)
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    out: list[AcceptedSplit] = []
    for entry in raw:
        if isinstance(entry, Mapping) and "seq_cut" in entry and "offset" in entry:
            out.append(AcceptedSplit.from_dict(dict(entry)))
    return out
