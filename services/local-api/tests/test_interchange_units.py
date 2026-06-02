"""Golden/unit tests for the interchange writers (no DB, no network)."""

from __future__ import annotations

from typing import Any

from laura.interchange.captions import segments_to_srt, segments_to_vtt
from laura.interchange.edl import timeline_to_edl
from laura.interchange.otio_io import otio_string_to_timeline, timeline_to_otio_string
from laura.interchange.timeline import Clip, Timeline
from laura.interchange.validate import validate_export

SEGMENTS: list[dict[str, Any]] = [
    {"start_frame": 30, "end_frame": 60, "text": "Hello", "speaker_label": None},
    {"start_frame": 60, "end_frame": 90, "text": "World", "speaker_label": "SPEAKER_00"},
]


def test_srt_golden() -> None:
    expected = (
        "1\n"
        "00:00:01,000 --> 00:00:02,000\n"
        "Hello\n"
        "\n"
        "2\n"
        "00:00:02,000 --> 00:00:03,000\n"
        "SPEAKER_00: World\n"
    )
    assert segments_to_srt(SEGMENTS, 30, 1) == expected


def test_vtt_golden() -> None:
    expected = (
        "WEBVTT\n"
        "\n"
        "00:00:01.000 --> 00:00:02.000\n"
        "Hello\n"
        "\n"
        "00:00:02.000 --> 00:00:03.000\n"
        "SPEAKER_00: World\n"
    )
    assert segments_to_vtt(SEGMENTS, 30, 1) == expected


def _two_clip_timeline() -> Timeline:
    return Timeline(
        name="Cut", rate_num=30, rate_den=1,
        clips=[
            Clip("A.mov", src_in_frame=0, src_out_frame_exclusive=30,
                 seq_in_frame=0, seq_out_frame_exclusive=30),
            Clip("B.mov", src_in_frame=100, src_out_frame_exclusive=130,
                 seq_in_frame=30, seq_out_frame_exclusive=60),
        ],
    )


def test_edl_structure_and_timecodes() -> None:
    edl = timeline_to_edl(_two_clip_timeline())
    lines = edl.splitlines()
    assert lines[0] == "TITLE: Cut"
    assert lines[1] == "FCM: NON-DROP FRAME"
    assert "00:00:00:00 00:00:01:00 00:00:00:00 00:00:01:00" in edl  # event 1
    assert "00:00:03:10 00:00:04:10 00:00:01:00 00:00:02:00" in edl  # event 2
    assert "* FROM CLIP NAME: A.mov" in edl
    assert "* FROM CLIP NAME: B.mov" in edl
    # deterministic
    assert timeline_to_edl(_two_clip_timeline()) == edl


def test_edl_drop_frame() -> None:
    tl = Timeline(
        name="DF", rate_num=30000, rate_den=1001, drop_frame=True,
        clips=[Clip("A", 0, 1800, 0, 1800)],
    )
    edl = timeline_to_edl(tl)
    assert "FCM: DROP FRAME" in edl
    assert "00:00:00;00 00:01:00;02" in edl  # drop-frame timecodes use ';'


def test_otio_roundtrip_preserves_frames() -> None:
    tl = Timeline(
        name="RT", rate_num=30, rate_den=1,
        clips=[
            Clip("A", src_in_frame=10, src_out_frame_exclusive=40,
                 seq_in_frame=0, seq_out_frame_exclusive=30, source_url="file:///a.mov"),
            Clip("B", src_in_frame=0, src_out_frame_exclusive=20,
                 seq_in_frame=30, seq_out_frame_exclusive=50),
        ],
    )
    text = timeline_to_otio_string(tl)
    assert "OTIO_SCHEMA" in text

    back = otio_string_to_timeline(text, rate_num=30, rate_den=1)
    assert len(back.clips) == 2
    a, b = back.ordered()
    assert (a.src_in_frame, a.src_out_frame_exclusive) == (10, 40)
    assert (a.seq_in_frame, a.seq_out_frame_exclusive) == (0, 30)
    assert a.source_url == "file:///a.mov"
    assert (b.src_in_frame, b.src_out_frame_exclusive) == (0, 20)
    assert (b.seq_in_frame, b.seq_out_frame_exclusive) == (30, 50)


def test_validate_capabilities() -> None:
    clean = _two_clip_timeline()
    assert validate_export(clean, "otio")["lossy"] is False
    assert validate_export(clean, "edl")["lossy"] is False

    with_speaker = Timeline(
        name="S", rate_num=30, rate_den=1,
        clips=[Clip("A", 0, 30, 0, 30, speaker_label="SPEAKER_00")],
    )
    edl_diag = validate_export(with_speaker, "edl")
    assert edl_diag["lossy"] is True
    assert any("speaker" in d for d in edl_diag["drops"])

    assert validate_export(clean, "aaf")["ok"] is False
