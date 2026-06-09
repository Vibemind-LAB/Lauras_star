"""Tests for the pure ASS karaoke caption builder (render.captions.build_ass).

All timing state is integer frames; centiseconds exist only in the rendered output.
"""

from __future__ import annotations

import re

import pytest

from laura.render.captions import build_ass

# ---------------------------------------------------------------------------
# 1. Empty lines — valid ASS structure, no Dialogue rows
# ---------------------------------------------------------------------------


def test_empty_lines_structure() -> None:
    ass = build_ass([], rate_num=30, rate_den=1)
    assert "PlayResX: 1080" in ass
    assert "PlayResY: 1920" in ass
    assert "[Events]" in ass
    assert "Dialogue:" not in ass


# ---------------------------------------------------------------------------
# 2. One line, two words → exactly one Dialogue, two \kf tags, correct timing
# ---------------------------------------------------------------------------


def test_one_line_two_words_dialogue_count() -> None:
    """words (0,15) and (15,30) at 30fps span 0:00:00.00 → 0:00:01.00."""
    ass = build_ass(
        [[("Hello", 0, 15), ("world", 15, 30)]],
        rate_num=30,
        rate_den=1,
    )
    dialogue_lines = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    assert len(dialogue_lines) == 1

    dl = dialogue_lines[0]
    # Start and End timestamps
    assert "0:00:00.00" in dl
    assert "0:00:01.00" in dl

    # Exactly two \kf tags
    kf_tags = re.findall(r"\\kf\d+", dl)
    assert len(kf_tags) == 2


# ---------------------------------------------------------------------------
# 3. \kf duration: single word (0,15) @30fps → 50 centiseconds
# ---------------------------------------------------------------------------


def test_kf_duration_single_word() -> None:
    """15 frames at 30fps = 0.5 s = 50 cs → {\\kf50}."""
    ass = build_ass([[("test", 0, 15)]], rate_num=30, rate_den=1)
    events = ass.split("[Events]")[1]
    assert r"{\kf50}" in events


# ---------------------------------------------------------------------------
# 4. Brace escaping — raw {x} must not appear in the Events section
# ---------------------------------------------------------------------------


def test_brace_escaping() -> None:
    """A word containing curly braces must not produce raw ASS override blocks."""
    ass = build_ass([[("{x}", 0, 30)]], rate_num=30, rate_den=1)
    events_section = ass.split("[Events]")[1]
    # Raw {x} would open an (invalid) override block — must not appear.
    assert "{x}" not in events_section
    # The replacement (x) must be present.
    assert "(x)" in events_section


# ---------------------------------------------------------------------------
# 5. Rounding edge — cc must stay in [0, 99], no ".100" in any timestamp
# ---------------------------------------------------------------------------


def test_rounding_edge_no_malformed_cc() -> None:
    """Pick a frame/rate where naive rounding could produce cc == 100.

    Example: frame=29 at 30/1 fps
      total_cs = 29 * 1 * 100 / 30 = 96.666…  → rounds to 97  (ok)

    A more sensitive case: frame=1 at rate_num=3, rate_den=1
      total_cs = 1 * 1 * 100 / 3 = 33.333… → rounds to 33  (ok)

    We use a synthetic rate where a near-100 value is produced:
      rate_num=30, rate_den=1, frame=29:  2900/30 = 96.67 → 97 cs  (safe)
      rate_num=100, rate_den=3, frame=1:  300/100 = 3 cs  (safe)

    The hardest case is frame/rate such that total_cs rounds to exactly 100
    within a second, which would produce ".100" — we verify it never occurs.
    """
    # Generate a range of awkward (frame, rate_num, rate_den) combos and assert
    # that no timestamp in the output contains ".100" or a two-digit cc that
    # exceeds 99.
    cases: list[tuple[int, int, int]] = [
        (29, 30, 1),
        (1, 3, 1),
        (23, 24, 1),
        (999, 1000, 1),      # near-100 cs: 999 * 100 / 1000 = 99.9 → 100? check
        (1001, 1000, 1),     # just over 1 second
        (47, 48, 1),
    ]
    for frame, rn, rd in cases:
        ass = build_ass([[("w", 0, frame)]], rate_num=rn, rate_den=rd)
        # No timestamp should have .100 or longer cc
        assert ".100" not in ass, f"Malformed cc in output for frame={frame} rate={rn}/{rd}"
        # Verify pattern: all time fields match h:mm:ss.cc with cc in [00,99]
        timestamps = re.findall(r"\d+:\d{2}:\d{2}\.(\d{2})", ass)
        for cc_str in timestamps:
            cc_val = int(cc_str)
            assert 0 <= cc_val <= 99, (
                f"cc={cc_val} out of range for frame={frame} rate={rn}/{rd}"
            )


# ---------------------------------------------------------------------------
# 6. Style section sanity — Reel style present with expected fields
# ---------------------------------------------------------------------------


def test_style_section_present() -> None:
    ass = build_ass([], rate_num=24, rate_den=1)
    assert "[V4+ Styles]" in ass
    assert "Style: Reel" in ass
    assert "&H00FFFFFF" in ass   # white primary
    assert "&H0000FFFF" in ass   # cyan secondary (karaoke pre-highlight)


# ---------------------------------------------------------------------------
# 7. Non-default parameters propagate
# ---------------------------------------------------------------------------


def test_custom_play_res_and_margin() -> None:
    ass = build_ass([], rate_num=25, rate_den=1, play_w=1920, play_h=1080, margin_v=120)
    assert "PlayResX: 1920" in ass
    assert "PlayResY: 1080" in ass
    assert ",120" in ass  # margin_v appears in the style line


# ---------------------------------------------------------------------------
# 8. Backslash in word text is dropped (not turned into a spurious \N tag)
# ---------------------------------------------------------------------------


def test_backslash_escaping() -> None:
    ass = build_ass([[("foo\\bar", 0, 10)]], rate_num=30, rate_den=1)
    events = ass.split("[Events]")[1]
    assert "foobar" in events
    # No spurious \N hard-newline
    assert r"\N" not in events


# ---------------------------------------------------------------------------
# 9. Empty word inside a line is skipped; line with only empty words is dropped
# ---------------------------------------------------------------------------


def test_empty_words_skipped() -> None:
    # Line with one real word and one empty word
    ass = build_ass([[("", 0, 5), ("hello", 5, 15)]], rate_num=30, rate_den=1)
    dialogue_lines = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    assert len(dialogue_lines) == 1
    # Only one \kf tag (the empty word is skipped)
    kf_tags = re.findall(r"\\kf\d+", dialogue_lines[0])
    assert len(kf_tags) == 1


def test_all_empty_words_produces_no_dialogue() -> None:
    ass = build_ass([[("", 0, 5), ("", 5, 10)]], rate_num=30, rate_den=1)
    assert "Dialogue:" not in ass


# ---------------------------------------------------------------------------
# 10. Non-integer-frame edge: 24000/1001 (NTSC) — smoke test
# ---------------------------------------------------------------------------


def test_ntsc_rate_smoke() -> None:
    """23.976 fps (rate_num=24000, rate_den=1001) must produce a valid document."""
    ass = build_ass(
        [[("A", 0, 24), ("B", 24, 48)]],
        rate_num=24000,
        rate_den=1001,
    )
    assert "Dialogue:" in ass
    # Start is frame 0 → 0:00:00.00
    assert "0:00:00.00" in ass
    # No malformed .100
    assert ".100" not in ass


# ---------------------------------------------------------------------------
# Additional: multiple lines → multiple Dialogue rows
# ---------------------------------------------------------------------------


def test_multiple_lines_multiple_dialogues() -> None:
    ass = build_ass(
        [
            [("Line", 0, 30), ("one", 30, 60)],
            [("Line", 60, 90), ("two", 90, 120)],
        ],
        rate_num=30,
        rate_den=1,
    )
    dialogue_lines = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    assert len(dialogue_lines) == 2


# ---------------------------------------------------------------------------
# Parametrized: specific kf values at 30fps
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "start, end, expected_cs",
    [
        (0, 30, 100),   # 1 s = 100 cs
        (0, 15, 50),    # 0.5 s = 50 cs
        (0, 3, 10),     # 0.1 s = 10 cs
        (0, 6, 20),     # 0.2 s = 20 cs
    ],
)
def test_kf_values_30fps(start: int, end: int, expected_cs: int) -> None:
    ass = build_ass([[("w", start, end)]], rate_num=30, rate_den=1)
    events = ass.split("[Events]")[1]
    assert f"{{\\kf{expected_cs}}}" in events
