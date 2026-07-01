"""Tests for the pure SRT caption builder (render.srt.sequence_transcript_to_srt).

All timing state is integer frames; milliseconds exist only in the rendered output.
Invariants: end-exclusive ranges, integer-frame math, NDF timecodes.
"""

from __future__ import annotations

import pytest

from laura.render.srt import _frame_to_srt_time, sequence_transcript_to_srt

# ---------------------------------------------------------------------------
# _frame_to_srt_time — timecode formatting
# ---------------------------------------------------------------------------


def test_frame_to_srt_time_zero() -> None:
    """Frame 0 → 00:00:00,000."""
    assert _frame_to_srt_time(0, 30, 1) == "00:00:00,000"


def test_frame_to_srt_time_one_second_30fps() -> None:
    """Frame 30 at 30fps → 00:00:01,000."""
    assert _frame_to_srt_time(30, 30, 1) == "00:00:01,000"


def test_frame_to_srt_time_half_second_30fps() -> None:
    """Frame 15 at 30fps → 00:00:00,500."""
    assert _frame_to_srt_time(15, 30, 1) == "00:00:00,500"


def test_frame_to_srt_time_one_frame_24fps() -> None:
    """Frame 1 at 24fps → 00:00:00,041 (floor of 41.666...)."""
    assert _frame_to_srt_time(1, 24, 1) == "00:00:00,041"


def test_frame_to_srt_time_one_minute() -> None:
    """30*60=1800 frames at 30fps → 00:01:00,000."""
    assert _frame_to_srt_time(1800, 30, 1) == "00:01:00,000"


def test_frame_to_srt_time_one_hour() -> None:
    """30*3600=108000 frames at 30fps → 01:00:00,000."""
    assert _frame_to_srt_time(108000, 30, 1) == "01:00:00,000"


def test_frame_to_srt_time_fractional_rate() -> None:
    """29.97fps = 30000/1001; 30000 frames → ~1000.0 s → 00:16:40,000."""
    # 30000 * 1001 * 1000 // 30000 = 1001000 ms = 1001 s = 16m41s
    assert _frame_to_srt_time(30000, 30000, 1001) == "00:16:41,000"


def test_frame_to_srt_time_invalid_rate_raises() -> None:
    with pytest.raises(ValueError):
        _frame_to_srt_time(30, 0, 1)

    with pytest.raises(ValueError):
        _frame_to_srt_time(30, 30, 0)


def test_frame_to_srt_time_25fps_one_frame() -> None:
    """Frame 1 at 25fps → 00:00:00,040 exactly."""
    assert _frame_to_srt_time(1, 25, 1) == "00:00:00,040"


# ---------------------------------------------------------------------------
# sequence_transcript_to_srt — full SRT output
# ---------------------------------------------------------------------------


def test_srt_empty_segments() -> None:
    """Empty segment list → empty string (no output at all)."""
    result = sequence_transcript_to_srt([], rate_num=30, rate_den=1)
    assert result == ""


def test_srt_single_segment() -> None:
    """One segment → index 1, correct timecodes, text, trailing blank line."""
    segs = [{"seq_in_frame": 0, "seq_out_frame_exclusive": 30, "text": "Hello world"}]
    result = sequence_transcript_to_srt(segs, rate_num=30, rate_den=1)
    lines = result.split("\n")
    assert lines[0] == "1"
    assert lines[1] == "00:00:00,000 --> 00:00:01,000"
    assert lines[2] == "Hello world"
    assert lines[3] == ""


def test_srt_multiple_segments_ordering() -> None:
    """Multiple segments → sequential 1-based indices, correct timecodes."""
    segs = [
        {"seq_in_frame": 0, "seq_out_frame_exclusive": 30, "text": "First"},
        {"seq_in_frame": 60, "seq_out_frame_exclusive": 90, "text": "Second"},
        {"seq_in_frame": 120, "seq_out_frame_exclusive": 150, "text": "Third"},
    ]
    result = sequence_transcript_to_srt(segs, rate_num=30, rate_den=1)
    lines = result.split("\n")
    # Block 1
    assert lines[0] == "1"
    assert lines[1] == "00:00:00,000 --> 00:00:01,000"
    assert lines[2] == "First"
    assert lines[3] == ""
    # Block 2
    assert lines[4] == "2"
    assert lines[5] == "00:00:02,000 --> 00:00:03,000"
    assert lines[6] == "Second"
    assert lines[7] == ""
    # Block 3
    assert lines[8] == "3"
    assert lines[9] == "00:00:04,000 --> 00:00:05,000"
    assert lines[10] == "Third"
    assert lines[11] == ""


def test_srt_skips_zero_duration_segments() -> None:
    """Segments where in_frame >= out_frame are silently skipped."""
    segs = [
        {"seq_in_frame": 30, "seq_out_frame_exclusive": 30, "text": "Zero"},  # skip
        {"seq_in_frame": 60, "seq_out_frame_exclusive": 90, "text": "Valid"},
    ]
    result = sequence_transcript_to_srt(segs, rate_num=30, rate_den=1)
    lines = result.split("\n")
    # Only one block, indexed as 1.
    assert lines[0] == "1"
    assert "Valid" in result
    assert "Zero" not in result


def test_srt_skips_empty_text_segments() -> None:
    """Segments with empty or whitespace-only text are silently skipped."""
    segs = [
        {"seq_in_frame": 0, "seq_out_frame_exclusive": 30, "text": "   "},  # blank
        {"seq_in_frame": 60, "seq_out_frame_exclusive": 90, "text": "Real"},
    ]
    result = sequence_transcript_to_srt(segs, rate_num=30, rate_den=1)
    assert "1\n" in result
    assert "2\n" not in result
    assert "Real" in result


def test_srt_index_is_continuous_despite_skips() -> None:
    """Index counter is continuous (no gaps) even when some segments are skipped."""
    segs = [
        {"seq_in_frame": 0, "seq_out_frame_exclusive": 30, "text": "A"},
        {"seq_in_frame": 30, "seq_out_frame_exclusive": 30, "text": "skip"},  # zero dur
        {"seq_in_frame": 60, "seq_out_frame_exclusive": 90, "text": "B"},
    ]
    result = sequence_transcript_to_srt(segs, rate_num=30, rate_den=1)
    # Should have indices 1 and 2, not 1 and 3.
    assert "\n1\n" in "\n" + result
    assert "\n2\n" in result
    assert "\n3\n" not in result


def test_srt_timecode_format_has_comma() -> None:
    """SRT timecodes use comma as ms separator (not a dot like ASS)."""
    segs = [{"seq_in_frame": 0, "seq_out_frame_exclusive": 15, "text": "Test"}]
    result = sequence_transcript_to_srt(segs, rate_num=30, rate_den=1)
    # Expect '00:00:00,000 --> 00:00:00,500'
    assert "," in result
    assert "00:00:00,000 --> 00:00:00,500" in result


def test_srt_all_segments_skipped_returns_empty() -> None:
    """When every segment is filtered out, result is empty string."""
    segs = [
        {"seq_in_frame": 10, "seq_out_frame_exclusive": 5, "text": "bad range"},
        {"seq_in_frame": 0, "seq_out_frame_exclusive": 30, "text": ""},
    ]
    result = sequence_transcript_to_srt(segs, rate_num=30, rate_den=1)
    assert result == ""


def test_srt_text_is_stripped() -> None:
    """Leading/trailing whitespace in segment text is stripped."""
    segs = [{"seq_in_frame": 0, "seq_out_frame_exclusive": 30, "text": "  hello  "}]
    result = sequence_transcript_to_srt(segs, rate_num=30, rate_den=1)
    assert "\nhello\n" in result
    assert "  hello  " not in result
