"""Pure (ML-free) tests for analysis mapping, speaker assignment, availability probes."""

from __future__ import annotations

from laura.analysis.asr import faster_whisper_available
from laura.analysis.diarize import assign_speakers, pyannote_available
from laura.analysis.mapping import map_segment
from laura.analysis.shots import scenedetect_available
from laura.analysis.types import SegmentResult, SpeakerTurn, WordResult


def test_map_segment_samples_and_frames() -> None:
    seg = SegmentResult(
        text="hi", start_sec=1.0, end_sec=2.0,
        words=[WordResult("hi", 1.0, 1.5)],
    )
    seg_row, words = map_segment(seg, audio_sample_rate=48000, rate_num=30, rate_den=1)

    assert seg_row["start_sample"] == 48000
    assert seg_row["end_sample"] == 96000
    assert seg_row["start_frame"] == 30   # 1.0s @30fps, floored In
    assert seg_row["end_frame"] == 60     # 2.0s @30fps, ceiled Out

    assert len(words) == 1
    assert words[0]["idx"] == 0
    assert words[0]["start_sample"] == 48000
    assert words[0]["end_sample"] == 72000   # 1.5s * 48000
    assert words[0]["start_frame"] == 30
    assert words[0]["end_frame"] == 45       # 1.5s @30fps


def test_assign_speakers_by_overlap() -> None:
    s1 = SegmentResult("a", 0.0, 1.0)
    s2 = SegmentResult("b", 2.0, 3.0)
    turns = [SpeakerTurn(0.0, 1.2, "SPEAKER_00"), SpeakerTurn(1.8, 3.5, "SPEAKER_01")]
    assign_speakers([s1, s2], turns)
    assert s1.speaker_label == "SPEAKER_00"
    assert s2.speaker_label == "SPEAKER_01"


def test_assign_speakers_no_turns_is_noop() -> None:
    seg = SegmentResult("x", 0.0, 1.0)
    assign_speakers([seg], [])
    assert seg.speaker_label is None


def test_availability_probes_return_bool() -> None:
    # These must never raise even when the extras are not installed.
    assert isinstance(scenedetect_available(), bool)
    assert isinstance(faster_whisper_available(), bool)
    assert isinstance(pyannote_available(), bool)
