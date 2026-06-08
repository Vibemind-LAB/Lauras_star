"""Tests for representing accepted L/J split edits in the OTIO source of truth (3a).

The split planner (:mod:`laura.analysis.splitedit`) RECOMMENDS, per inter-clip cut, independent
picture and sound frames; :func:`laura.api.otio_split.apply_split_cuts` makes an ACCEPTED offset
REAL in the OTIO timeline as a separate audio track whose boundary is offset from the video
boundary — migration-free. These tests pin:

* a **J-cut** (offset < 0) puts the audio boundary EARLIER than the video boundary,
* an **L-cut** (offset > 0) puts it LATER,
* a **hard** offset (``|offset| <= 1``) leaves audio and video coincident (no audio track),
* **empty** splits leave the OTIO byte-for-byte the single-track timeline Laura builds today,
* the audio boundary projects to the correct **sample** (invariant #3),
* the accepted offsets **round-trip** through the OTIO blob (so a regenerate-from-clips can
  re-apply them without a migration), and
* end-to-end the split SURVIVES a later edit through ``editing.otio_sync``.

The video track always stays frame-exact on the visual cut.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import opentimelineio as otio

from laura.api.otio_split import (
    AcceptedSplit,
    accepted_offsets_from_otio,
    apply_split_cuts,
)
from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.editing.otio_sync import serialize_timeline_otio
from laura.interchange.otio_io import timeline_to_otio_string
from laura.interchange.timeline import Clip, Timeline
from laura.timebase.sampling import frame_to_sample

RATE_NUM, RATE_DEN = 30, 1
SAMPLE_RATE = 48_000  # 1 frame == 1600 samples at 48 kHz / 30 fps

# Two clips packed back-to-back: A on [0,50) of source, B on [100,170). The inter-clip cut is at
# source frame 100 (B.src_in_frame) == sequence frame 50 (B.seq_in_frame).
SEQ_CUT = 100
VIDEO_BOUNDARY = 50


def _rough_cut() -> Timeline:
    return Timeline(
        name="rc",
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
        clips=[
            Clip(name="A", src_in_frame=0, src_out_frame_exclusive=50,
                 seq_in_frame=0, seq_out_frame_exclusive=50, source_url="/a.mov"),
            Clip(name="B", src_in_frame=100, src_out_frame_exclusive=170,
                 seq_in_frame=50, seq_out_frame_exclusive=120, source_url="/a.mov"),
        ],
    )


def _tracks_by_kind(text: str) -> dict[Any, list[tuple[int, int, dict[str, Any] | None]]]:
    """Deserialise; return per track kind the ordered (seq_in, seq_out, laura-meta) of clips."""
    timeline = otio.adapters.read_from_string(text, "otio_json")
    out: dict[Any, list[tuple[int, int, dict[str, Any] | None]]] = {}
    for track in timeline.tracks:
        clips: list[tuple[int, int, dict[str, Any] | None]] = []
        for clip in track.find_clips():
            rng = clip.range_in_parent()
            seq_in = int(rng.start_time.value)
            seq_out = seq_in + int(rng.duration.value)
            meta = dict(clip.metadata).get("laura") if clip.metadata else None
            clips.append((seq_in, seq_out, dict(meta) if meta is not None else None))
        out[track.kind] = clips
    return out


# === J-cut: audio boundary lands EARLIER than the video boundary ================================


def test_j_cut_audio_boundary_is_earlier_than_video() -> None:
    # offset -3 => audio cut 3 frames before the picture cut (the next sound precedes its picture).
    text = apply_split_cuts(
        _rough_cut(), [AcceptedSplit(seq_cut=SEQ_CUT, offset=-3)], audio_sample_rate=SAMPLE_RATE
    )
    tracks = _tracks_by_kind(text)
    video = tracks[otio.schema.TrackKind.Video]
    audio = tracks[otio.schema.TrackKind.Audio]

    # Video boundary stays frame-exact on the visual cut.
    assert video[0][1] == VIDEO_BOUNDARY  # clip A out == 50
    assert video[1][0] == VIDEO_BOUNDARY  # clip B in  == 50
    # Audio boundary is 3 frames EARLIER: A ends at 47, B starts at 47.
    assert audio[0][1] == VIDEO_BOUNDARY - 3 == 47
    assert audio[1][0] == VIDEO_BOUNDARY - 3 == 47
    # offset is exactly the difference audio - video.
    assert audio[0][1] - video[0][1] == -3


# === L-cut: audio boundary lands LATER than the video boundary ==================================


def test_l_cut_audio_boundary_is_later_than_video() -> None:
    # offset +3 => audio cut 3 frames after the picture cut (the previous sound trails into it).
    text = apply_split_cuts(
        _rough_cut(), [AcceptedSplit(seq_cut=SEQ_CUT, offset=3)], audio_sample_rate=SAMPLE_RATE
    )
    tracks = _tracks_by_kind(text)
    video = tracks[otio.schema.TrackKind.Video]
    audio = tracks[otio.schema.TrackKind.Audio]

    assert video[0][1] == VIDEO_BOUNDARY  # video unchanged
    assert video[1][0] == VIDEO_BOUNDARY
    # Audio boundary is 3 frames LATER: A ends at 53, B starts at 53.
    assert audio[0][1] == VIDEO_BOUNDARY + 3 == 53
    assert audio[1][0] == VIDEO_BOUNDARY + 3 == 53
    assert audio[0][1] - video[0][1] == 3


# === hard: a sub-perception offset leaves audio and video coincident (no audio track) ===========


def test_hard_offset_leaves_audio_video_coincident() -> None:
    # |offset| <= 1 is hard: dropped, so no audio track is emitted and the OTIO is the video-only
    # timeline (audio coincides with video by construction).
    text = apply_split_cuts(_rough_cut(), [AcceptedSplit(seq_cut=SEQ_CUT, offset=1)])
    tracks = _tracks_by_kind(text)
    assert otio.schema.TrackKind.Audio not in tracks
    assert text.strip() == timeline_to_otio_string(_rough_cut()).strip()
    assert accepted_offsets_from_otio(text) == []


# === empty splits: byte-for-byte the single-track OTIO Laura builds today ========================


def test_empty_splits_is_unchanged_single_track_otio() -> None:
    text = apply_split_cuts(_rough_cut(), [])
    assert text.strip() == timeline_to_otio_string(_rough_cut()).strip()
    tracks = _tracks_by_kind(text)
    assert otio.schema.TrackKind.Audio not in tracks
    assert accepted_offsets_from_otio(text) == []


# === samples: the audio boundary projects to the right sample (invariant #3) =====================


def test_audio_boundary_carries_exact_samples() -> None:
    # L-cut +3: audio boundary frame 53; at 48 kHz / 30 fps that is 53 * 1600 = 84800 samples.
    text = apply_split_cuts(
        _rough_cut(), [AcceptedSplit(seq_cut=SEQ_CUT, offset=3)], audio_sample_rate=SAMPLE_RATE
    )
    audio = _tracks_by_kind(text)[otio.schema.TrackKind.Audio]
    meta_a, meta_b = audio[0][2], audio[1][2]
    assert meta_a is not None and meta_b is not None
    # A's audio-out sample == boundary frame 53 -> 84800; B's audio-in sample matches it.
    assert meta_a["src_out_sample"] == frame_to_sample(53, SAMPLE_RATE, RATE_NUM, RATE_DEN) == 84800
    assert meta_b["src_in_sample"] == frame_to_sample(103, SAMPLE_RATE, RATE_NUM, RATE_DEN)
    assert meta_a["audio_sample_rate"] == SAMPLE_RATE


def test_samples_omitted_when_no_sample_rate() -> None:
    # Without an audio sample rate the split is frame-only — no sample keys are invented.
    text = apply_split_cuts(_rough_cut(), [AcceptedSplit(seq_cut=SEQ_CUT, offset=3)])
    meta = _tracks_by_kind(text)[otio.schema.TrackKind.Audio][0][2]
    assert meta is not None
    assert "src_in_sample" not in meta
    assert meta["src_in_frame"] == 0


# === round-trip: the accepted offsets survive serialise -> deserialise ===========================


def test_accepted_offsets_round_trip() -> None:
    text = apply_split_cuts(_rough_cut(), [AcceptedSplit(seq_cut=SEQ_CUT, offset=-3)])
    recovered = accepted_offsets_from_otio(text)
    assert recovered == [AcceptedSplit(seq_cut=SEQ_CUT, offset=-3)]
    # Re-applying the recovered offsets reproduces an identical blob (idempotent persistence).
    again = apply_split_cuts(_rough_cut(), recovered)
    assert again.strip() == text.strip()


def test_accepted_offsets_from_otio_is_graceful_on_garbage() -> None:
    assert accepted_offsets_from_otio("") == []
    assert accepted_offsets_from_otio("{ not otio }") == []
    # A valid OTIO with no laura metadata simply has no recoverable splits.
    assert accepted_offsets_from_otio(timeline_to_otio_string(_rough_cut())) == []


def test_video_track_always_matches_today() -> None:
    # Whatever the split, the VIDEO track is exactly the single-track OTIO Laura builds today.
    expected = _tracks_by_kind(timeline_to_otio_string(_rough_cut()))[otio.schema.TrackKind.Video]
    for offset in (-5, -3, 3, 7):
        text = apply_split_cuts(_rough_cut(), [AcceptedSplit(seq_cut=SEQ_CUT, offset=offset)])
        assert _tracks_by_kind(text)[otio.schema.TrackKind.Video] == expected


# === end-to-end: the split SURVIVES a regenerate-from-clips edit (the 3b/3c-critical path) =======


def _db_with_rough_cut(tmp_path: Path) -> tuple[SqliteDatabase, dict[str, Any]]:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    project = repos.create_project(
        db, name="p", rate_num=RATE_NUM, rate_den=RATE_DEN, drop_frame=False,
        workspace_root="/tmp/p",
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a.mov", source_path="/a.mov"
    )
    repos.update_asset_probe(
        db,
        asset["id"],
        type="video",
        duration_frames=200,
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
        audio_sample_rate=SAMPLE_RATE,
        start_timecode=None,
        width=1920,
        height=1080,
        codec_video="h264",
        codec_audio="aac",
        is_vfr=False,
        sha256=None,
    )
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.replace_timeline_clips(db, tl["id"], [
        {"asset_id": asset["id"], "src_in_frame": 0, "src_out_frame_exclusive": 50,
         "seq_in_frame": 0, "seq_out_frame_exclusive": 50, "lane": 0,
         "speed_num": 1, "speed_den": 1},
        {"asset_id": asset["id"], "src_in_frame": 100, "src_out_frame_exclusive": 170,
         "seq_in_frame": 50, "seq_out_frame_exclusive": 120, "lane": 0,
         "speed_num": 1, "speed_den": 1},
    ])
    fresh = repos.get_timeline(db, tl["id"])
    assert fresh is not None
    return db, fresh


def test_split_survives_regenerate_from_clips(tmp_path: Path) -> None:
    db, tl = _db_with_rough_cut(tmp_path)

    # Accept an L-cut (+3) on the inter-clip cut and persist it.
    first = serialize_timeline_otio(db, tl, accepted=[AcceptedSplit(seq_cut=SEQ_CUT, offset=3)])
    repos.update_timeline_otio(db, tl["id"], first)
    assert accepted_offsets_from_otio(first) == [AcceptedSplit(seq_cut=SEQ_CUT, offset=3)]
    assert otio.schema.TrackKind.Audio in _tracks_by_kind(first)

    # Now REGENERATE from the clips table (no explicit accepted list) — simulating any later edit.
    # The accepted offset must be recovered from the previous blob, not clobbered to a hard cut.
    reloaded = repos.get_timeline(db, tl["id"])
    assert reloaded is not None
    second = serialize_timeline_otio(db, reloaded)
    assert accepted_offsets_from_otio(second) == [AcceptedSplit(seq_cut=SEQ_CUT, offset=3)]
    audio = _tracks_by_kind(second)[otio.schema.TrackKind.Audio]
    assert audio[0][1] == VIDEO_BOUNDARY + 3  # audio boundary still offset by +3, sample-accurate
    assert audio[0][2] is not None and audio[0][2]["audio_sample_rate"] == SAMPLE_RATE
