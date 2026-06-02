"""Golden tests for SMPTE timecode <-> frame, with focus on drop-frame edges."""

from __future__ import annotations

import pytest

from laura.timebase import (
    ALL_PRESETS,
    FPS_24,
    FPS_25,
    FPS_29_97_DF,
    FPS_29_97_NDF,
    FPS_30,
    FPS_59_94_DF,
    FPS_60,
    FrameRate,
    frames_to_timecode,
    timecode_to_frames,
)


@pytest.mark.parametrize(
    ("frame", "fr", "expected"),
    [
        # --- NDF sanity ---
        (0, FPS_30, "00:00:00:00"),
        (30, FPS_30, "00:00:01:00"),
        (1800, FPS_30, "00:01:00:00"),
        (108000, FPS_30, "01:00:00:00"),
        (25, FPS_25, "00:00:01:00"),
        (24, FPS_24, "00:00:01:00"),
        (60, FPS_60, "00:00:01:00"),
        # --- 29.97 DROP-FRAME: the critical edges ---
        (0, FPS_29_97_DF, "00:00:00;00"),
        (1799, FPS_29_97_DF, "00:00:59;29"),
        (1800, FPS_29_97_DF, "00:01:00;02"),   # ;00 and ;01 are dropped
        (17981, FPS_29_97_DF, "00:09:59;29"),
        (17982, FPS_29_97_DF, "00:10:00;00"),  # tenth minute: NO drop
        # --- 59.94 DROP-FRAME ---
        (0, FPS_59_94_DF, "00:00:00;00"),
        (3599, FPS_59_94_DF, "00:00:59;59"),
        (3600, FPS_59_94_DF, "00:01:00;04"),   # ;00..;03 dropped
        (35964, FPS_59_94_DF, "00:10:00;00"),
    ],
)
def test_frames_to_timecode_golden(frame: int, fr: FrameRate, expected: str) -> None:
    assert frames_to_timecode(frame, fr) == expected
    # and the inverse round-trips
    assert timecode_to_frames(expected, fr) == frame


@pytest.mark.parametrize(
    "fr", ALL_PRESETS, ids=lambda fr: f"{fr.rate_num}-{fr.rate_den}-df{int(fr.drop_frame)}"
)
def test_roundtrip_dense(fr: FrameRate) -> None:
    """frame -> timecode -> frame is the identity for every frame in tricky regions."""
    nominal = fr.nominal
    regions = [
        range(0, nominal * 3),                      # first seconds
        range(1798 - 5, 1802 + 5) if nominal == 30 else range(0, 5),
        range(nominal * 60 - 5, nominal * 60 + 5),  # around the 1-minute drop
        range(nominal * 600 - 5, nominal * 600 + 5),  # around the 10-minute non-drop
        range(108000, 108000 + 3),                  # ~1h region
    ]
    for region in regions:
        for f in region:
            tc = frames_to_timecode(f, fr)
            assert timecode_to_frames(tc, fr) == f, (f, tc, fr)


def test_negative_frame_roundtrips() -> None:
    assert frames_to_timecode(-30, FPS_30) == "-00:00:01:00"
    assert timecode_to_frames("-00:00:01:00", FPS_30) == -30


def test_frame_field_overflow_rejected() -> None:
    with pytest.raises(ValueError):
        timecode_to_frames("00:00:00:30", FPS_30)  # ff must be < 30


def test_invalid_timecode_string() -> None:
    with pytest.raises(ValueError):
        timecode_to_frames("nonsense", FPS_30)


def test_df_and_ndf_differ_only_in_numbering() -> None:
    """At the same frame index, DF and NDF point to the same physical instant but
    different labels after the first minute."""
    # frame 1800: NDF label 00:01:00:00, DF label 00:01:00;02
    assert frames_to_timecode(1800, FPS_29_97_NDF) == "00:01:00:00"
    assert frames_to_timecode(1800, FPS_29_97_DF) == "00:01:00;02"
