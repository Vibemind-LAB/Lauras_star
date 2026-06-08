"""Unit tests for audio-silence detection and silence-aware cut placement.

The parser tests (:func:`laura.analysis.silence.parse_silencedetect`) run on *captured* ffmpeg
``silencedetect`` stderr — no ffmpeg, fully deterministic. They pin the seconds->source-frame
conversion and every edge case: a single silence, several silences, a silence that runs to EOF
(unmatched ``silence_start``) closed at the media end, an unmatched start with no known media end
(dropped), a stray end, degenerate ranges, and empty input.

The placement tests confirm the editorial *tiering* in :func:`laura.analysis.joint.joint_place`:
a frame inside a real audio silence (1.0) outscores a mere clean word edge (0.85), which
outscores a mid-word frame (0.0) — so at an equal-ish visual score the cut prefers genuine
silence over an ASR word boundary, while ``silence=None`` keeps exactly the old behaviour.
"""

from __future__ import annotations

import pytest

from laura.analysis.editorial import Word, editorial_metrics
from laura.analysis.joint import joint_place
from laura.analysis.silence import detect_silence, parse_silencedetect

# A realistic ffmpeg stderr fragment: banner-ish Duration line + one silence pair. 30fps assumed,
# so 1.0s -> frame 30 and 1.6s -> frame 48 (round(secs*30)). Mirrors real captured output.
_STDERR_ONE = """\
  Duration: 00:00:02.60, start: 0.000000, bitrate: 77 kb/s
[Parsed_silencedetect_0 @ 0x55] silence_start: 0.999917
[Parsed_silencedetect_0 @ 0x55] silence_end: 1.600062 | silence_duration: 0.600146
"""

_STDERR_MULTI = """\
  Duration: 00:00:03.20, start: 0.000000, bitrate: 80 kb/s
[Parsed_silencedetect_0 @ 0x55] silence_start: 0.999917
[Parsed_silencedetect_0 @ 0x55] silence_end: 1.600062 | silence_duration: 0.600146
[Parsed_silencedetect_0 @ 0x55] silence_start: 2.599958
[Parsed_silencedetect_0 @ 0x55] silence_end: 3.200000 | silence_duration: 0.600042
"""

# A start with no matching end: the silence ran to EOF. Duration line gives the media end (3.2s).
_STDERR_UNMATCHED_WITH_DURATION = """\
  Duration: 00:00:03.20, start: 0.000000, bitrate: 80 kb/s
[Parsed_silencedetect_0 @ 0x55] silence_start: 2.599958
"""

# A start with no matching end AND no Duration line -> we cannot know the media end -> dropped.
_STDERR_UNMATCHED_NO_DURATION = """\
[Parsed_silencedetect_0 @ 0x55] silence_start: 2.599958
"""

RATE_NUM, RATE_DEN = 30, 1


def test_parse_single_silence_to_frames() -> None:
    # 0.999917s -> round(29.997) = 30; 1.600062s -> round(48.0) = 48. End-exclusive [30, 48).
    assert parse_silencedetect(_STDERR_ONE, RATE_NUM, RATE_DEN) == [(30, 48)]


def test_parse_multiple_silences() -> None:
    # Two pairs: [30,48) and 2.599958s->78, 3.2s->96 -> [78, 96).
    assert parse_silencedetect(_STDERR_MULTI, RATE_NUM, RATE_DEN) == [(30, 48), (78, 96)]


def test_parse_unmatched_start_closes_at_media_end() -> None:
    # silence_start 2.599958s -> 78, no end line -> close at Duration 3.2s -> 96. [78, 96).
    assert parse_silencedetect(
        _STDERR_UNMATCHED_WITH_DURATION, RATE_NUM, RATE_DEN
    ) == [(78, 96)]


def test_parse_unmatched_start_without_duration_is_dropped() -> None:
    # No end and no media end to fall back on -> we never invent an end -> dropped entirely.
    assert parse_silencedetect(_STDERR_UNMATCHED_NO_DURATION, RATE_NUM, RATE_DEN) == []


def test_parse_two_starts_first_runs_to_next_then_eof() -> None:
    # A start, then a *second* start before any end: the first silence is treated as running to
    # EOF and closed at the media end; the second has a proper end.
    stderr = (
        "  Duration: 00:00:04.00, start: 0.000000, bitrate: 80 kb/s\n"
        "[Parsed_silencedetect_0 @ 0x55] silence_start: 0.500000\n"
        "[Parsed_silencedetect_0 @ 0x55] silence_start: 2.000000\n"
        "[Parsed_silencedetect_0 @ 0x55] silence_end: 2.500000\n"
    )
    # first: 0.5s->15 closed at 4.0s->120 -> (15, 120); second: 2.0s->60, 2.5s->75 -> (60, 75).
    assert parse_silencedetect(stderr, RATE_NUM, RATE_DEN) == [(15, 120), (60, 75)]


def test_parse_stray_end_is_ignored() -> None:
    stderr = "[Parsed_silencedetect_0 @ 0x55] silence_end: 1.0 | silence_duration: 0.5\n"
    assert parse_silencedetect(stderr, RATE_NUM, RATE_DEN) == []


def test_parse_degenerate_interval_dropped() -> None:
    # start == end after rounding -> zero-length -> dropped (every returned range is non-empty).
    stderr = (
        "[Parsed_silencedetect_0 @ 0x55] silence_start: 1.000000\n"
        "[Parsed_silencedetect_0 @ 0x55] silence_end: 1.000000\n"
    )
    assert parse_silencedetect(stderr, RATE_NUM, RATE_DEN) == []


def test_parse_empty_stderr() -> None:
    assert parse_silencedetect("", RATE_NUM, RATE_DEN) == []
    assert parse_silencedetect("no silence here, just noise\n", RATE_NUM, RATE_DEN) == []


def test_parse_respects_fractional_rate() -> None:
    # 24000/1001 ~= 23.976fps. 1.0s -> round(23.976) = 24; 2.0s -> round(47.952) = 48.
    stderr = (
        "[Parsed_silencedetect_0 @ 0x55] silence_start: 1.000000\n"
        "[Parsed_silencedetect_0 @ 0x55] silence_end: 2.000000\n"
    )
    assert parse_silencedetect(stderr, 24000, 1001) == [(24, 48)]


def test_detect_silence_missing_file_returns_empty() -> None:
    # Unreadable path -> ffmpeg errors -> defensive [] (never raises).
    assert detect_silence("does/not/exist.mp4", rate_num=30, rate_den=1) == []


# === silence-aware joint placement ==============================================================

WINDOW = 12


def test_joint_prefers_silence_interior_over_word_edge() -> None:
    # A clean word edge at 33 and a real silence interval [30, 48). Cut at 30 sits inside the
    # silence (editorial 1.0); the word edge 33 is only a clean edge (0.85). With no visual
    # signal the editorial term decides, so the silence interior wins over the word edge.
    words = [Word(start_frame=10, end_frame=33), Word(start_frame=48, end_frame=70)]
    silence = [(30, 48)]
    frame, _score = joint_place(30, words, None, window=WINDOW, silence=silence)
    assert frame == 30  # inside the silence, not dragged to the word edge


def test_silence_outscores_word_edge_at_equalish_visual() -> None:
    # Both candidates are visually similar (flat window). Frame 40 is inside silence [38, 46);
    # frame 34 is a clean word edge (end of word [20,34)) but NOT in the silence. Silence (1.0)
    # beats the edge (0.85), so the cut lands in the silence even though the edge is closer-ish.
    words = [Word(start_frame=20, end_frame=34), Word(start_frame=46, end_frame=60)]
    silence = [(38, 46)]
    frame, _score = joint_place(36, words, None, window=WINDOW, silence=silence)
    assert 38 <= frame < 46  # chose a frame inside the detected silence


def test_silence_none_is_backward_compatible() -> None:
    # silence=None -> editorial term collapses to the historical 1.0/0.0; the clean word edge is
    # the best reachable frame (no silence tier), reproducing the pre-silence choice exactly.
    words = [Word(start_frame=10, end_frame=33), Word(start_frame=48, end_frame=70)]
    with_none = joint_place(30, words, None, window=WINDOW)[0]
    # 30 is mid-word [10,33); nearest clean edge is 33 -> the old word-edge snap.
    assert with_none == 33


def test_silence_aware_score_strictly_above_word_edge() -> None:
    # No visual signal, default weights. Pin a single frame each (window=0) so the score is purely
    # editorial: a silence-interior frame (1.0) must score strictly above a clean word edge that is
    # NOT in silence (0.85). Word [20,34) -> edge 34 is clean but outside the silence [38,46).
    words = [Word(start_frame=20, end_frame=34), Word(start_frame=46, end_frame=60)]
    silence = [(38, 46)]
    _f_sil, s_sil = joint_place(40, words, None, window=0, silence=silence)  # 40 in silence
    _f_edge, s_edge = joint_place(34, words, None, window=0, silence=silence)  # 34 clean edge
    assert s_sil > s_edge


def test_editorial_metrics_reports_pct_on_silence() -> None:
    words = [Word(start_frame=10, end_frame=33), Word(start_frame=48, end_frame=70)]
    silence = [(36, 48)]  # the real pause; word edge 33 sits just before it (not in silence)
    cuts = [40, 33, 20]  # 40 in silence, 33 clean word edge (not silence), 20 mid-word
    m = editorial_metrics(cuts, words, silence=silence)
    assert m["pct_on_silence"] == pytest.approx(1 / 3)
    # Without silence the key is absent (backward compatible).
    assert "pct_on_silence" not in editorial_metrics(cuts, words)
