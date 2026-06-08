"""Unit tests for the L/J split-edit planner (:mod:`laura.analysis.splitedit`).

Pure and deterministic. ``video_frame`` (the picture cut) is driven by the same visual seam as
``joint``/``eval_cut`` — a precomputed ``diff`` aligned to the candidate band (``diff[i]`` is ``d``
at frame ``lo+i`` with ``lo == max(cut-window, 1)``) or an injected ``frame_loader`` over an
in-memory grayscale sequence. ``audio_frame`` (the sound cut) is driven by ``silence`` intervals
and ``words``. The tests pin every classification (hard / L / J) and the offset, plus the
defensive fallbacks (no words+no silence -> hard) and the window bound.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from laura.analysis.editorial import Word
from laura.analysis.eval_cut import FrameLoader
from laura.analysis.splitedit import (
    SplitCut,
    plan_split_cut,
    plan_split_cuts,
)

WINDOW = 12


def _peak_diff(cut: int, peak_frame: int, *, window: int = WINDOW) -> list[float]:
    """A ``diff`` over the candidate band with a single strong peak at ``peak_frame``.

    ``lo == max(cut-window, 1)``; entry ``i`` is ``d`` at frame ``lo+i``. The peak gets ``1.0`` and
    every other frame a tiny non-zero value, so the picture cut (visual peak) lands on the peak.
    """
    lo = max(cut - window, 1)
    n = (cut + window) - lo + 1
    diff = [0.02] * n
    diff[peak_frame - lo] = 1.0
    return diff


# === L-cut: audio (real silence) lands AFTER the picture cut =====================================


def test_audio_in_silence_after_video_is_L_cut() -> None:
    # Picture peak at the cut (100); a real silence [105, 112) sits AFTER it. The nearest silence
    # frame to cut 100 is 105 -> audio_frame 105 > video_frame 100 -> offset +5 -> "L".
    cut = 100
    diff = _peak_diff(cut, 100)
    silence = [(105, 112)]
    sc = plan_split_cut(cut, [], silence, diff=diff, window=WINDOW)
    assert sc == SplitCut(
        seq_cut=100, video_frame=100, audio_frame=105, offset=5, kind="L"
    )


# === J-cut: audio (real silence) lands BEFORE the picture cut ====================================


def test_audio_before_video_is_J_cut() -> None:
    # Picture peak at the cut (100); a real silence [88, 95) sits BEFORE it. The nearest silence
    # frame to cut 100 is 94 (95 exclusive) -> audio 94 < video 100 -> offset -6 -> "J".
    cut = 100
    diff = _peak_diff(cut, 100)
    silence = [(88, 95)]
    sc = plan_split_cut(cut, [], silence, diff=diff, window=WINDOW)
    assert sc == SplitCut(
        seq_cut=100, video_frame=100, audio_frame=94, offset=-6, kind="J"
    )


# === hard: audio and picture coincide (silence covers the visual peak) ===========================


def test_audio_aligned_with_video_is_hard() -> None:
    # Picture peak at the cut (100); the silence [98, 110) CONTAINS the peak, so the nearest silence
    # frame is 100 itself -> audio_frame == video_frame -> offset 0 -> "hard".
    cut = 100
    diff = _peak_diff(cut, 100)
    silence = [(98, 110)]
    sc = plan_split_cut(cut, [], silence, diff=diff, window=WINDOW)
    assert sc.video_frame == 100
    assert sc.audio_frame == 100
    assert sc.offset == 0
    assert sc.kind == "hard"


def test_offset_within_tolerance_is_hard() -> None:
    # A 1-frame split is below the perception threshold -> still "hard". Peak 100, silence starts at
    # 101 -> nearest silence frame 101 -> offset +1 -> |offset| <= 1 -> "hard" (not "L").
    cut = 100
    diff = _peak_diff(cut, 100)
    silence = [(101, 110)]
    sc = plan_split_cut(cut, [], silence, diff=diff, window=WINDOW)
    assert sc.audio_frame == 101
    assert sc.offset == 1
    assert sc.kind == "hard"


# === no words / no silence -> hard (audio coincides with picture) ===============================


def test_no_words_no_silence_is_hard() -> None:
    # Nothing to anchor the sound cut to -> audio_frame falls back to the picture cut -> hard.
    cut = 100
    diff = _peak_diff(cut, 103)  # picture peak a few frames off the rough cut
    sc = plan_split_cut(cut, [], None, diff=diff, window=WINDOW)
    assert sc.video_frame == 103
    assert sc.audio_frame == 103  # coincides with the picture cut
    assert sc.offset == 0
    assert sc.kind == "hard"


def test_no_visual_signal_keeps_video_at_cut() -> None:
    # No diff/video -> the picture cut cannot move -> video_frame == cut. With no words/silence the
    # sound cut coincides -> a hard cut on the original frame.
    sc = plan_split_cut(100, [], None, window=WINDOW)
    assert sc.video_frame == 100
    assert sc.audio_frame == 100
    assert sc.kind == "hard"


# === word-gap fallback when there is no silence =================================================


def test_audio_falls_back_to_word_gap_when_no_silence() -> None:
    # No silence, but a transcript: the sound cut anchors to the nearest clean word edge, never
    # bisecting a word. Word [95, 108) covers the cut 100 mid-word; its nearer edge is 108 (dist 8)
    # vs 95 (dist 5) -> 95. Picture peak at 100 -> audio 95 < video 100 -> "J".
    cut = 100
    diff = _peak_diff(cut, 100)
    words = [Word(start_frame=80, end_frame=95), Word(start_frame=95, end_frame=108)]
    sc = plan_split_cut(cut, words, None, diff=diff, window=WINDOW)
    assert sc.video_frame == 100
    assert sc.audio_frame == 95  # clean word edge, not mid-word
    assert sc.offset == -5
    assert sc.kind == "J"


def test_silence_preferred_over_word_gap() -> None:
    # Both a clean word edge (95) and a real silence ([104, 110)) are reachable. The silence is the
    # editor's real target, so the sound cut prefers it even though the word edge is closer.
    cut = 100
    diff = _peak_diff(cut, 100)
    words = [Word(start_frame=80, end_frame=95), Word(start_frame=95, end_frame=120)]
    silence = [(104, 110)]
    sc = plan_split_cut(cut, words, silence, diff=diff, window=WINDOW)
    assert sc.audio_frame == 104  # inside the silence, not the word edge 95
    assert sc.kind == "L"


# === window is respected ========================================================================


def test_window_respected_silence_outside_window_ignored() -> None:
    # The only silence sits outside ±window of the cut, so it is unreachable; with no words the
    # sound cut falls back to the picture cut -> hard. Proves the window bounds the audio search.
    cut = 100
    diff = _peak_diff(cut, 100)
    silence = [(120, 130)]  # 120 is 20 frames from the cut -> outside window=12
    sc = plan_split_cut(cut, [], silence, diff=diff, window=WINDOW)
    assert sc.audio_frame == 100  # silence not reachable -> fell back to the picture cut
    assert sc.kind == "hard"


def test_window_caps_audio_search_band() -> None:
    # A silence 5 frames away is reachable with window=6 but NOT with window=4.
    cut = 100
    silence = [(105, 110)]
    near = plan_split_cut(cut, [], silence, diff=_peak_diff(cut, 100, window=4), window=4)
    assert near.audio_frame == 100  # 105 out of ±4 -> hard
    far = plan_split_cut(cut, [], silence, diff=_peak_diff(cut, 100, window=6), window=6)
    assert far.audio_frame == 105  # 105 within ±6 -> L-cut
    assert far.kind == "L"


# === validation =================================================================================


def test_negative_window_rejected() -> None:
    with pytest.raises(ValueError, match="window"):
        plan_split_cut(100, [], None, window=-1)


# === frame_loader seam (no ffmpeg) ==============================================================

H, GW = 8, 8


def _step_sequence(peak: int, n: int) -> list[np.ndarray]:
    """Black before ``peak``, white from ``peak`` on -> a single luma diff peak at ``peak``."""
    return [np.full((H, GW), 0 if f < peak else 255, dtype=np.uint8) for f in range(n)]


def _loader_for(frames: list[np.ndarray]) -> FrameLoader:
    def loader(_video: Path | str, lo: int, hi: int) -> list[np.ndarray]:
        lo = max(0, lo)
        hi = min(len(frames), hi)
        return [frames[i] for i in range(lo, hi)]

    return loader


def test_frame_loader_seam_drives_picture_cut() -> None:
    # Hard luma jump at frame 30; the rough cut is placed 3 off (33). The picture cut snaps to 30;
    # a silence [33, 40) after it -> audio 33 -> offset +3 from video 30 -> "L".
    n = 60
    loader = _loader_for(_step_sequence(30, n))
    sc = plan_split_cut(
        33, [], [(33, 40)], window=WINDOW,
        video_path=Path("x.mp4"), total_frames=n, frame_loader=loader,
    )
    assert sc.video_frame == 30  # visual peak, not the rough-cut frame 33
    assert sc.audio_frame == 33  # nearest silence frame to the cut 33
    assert sc.offset == 3
    assert sc.kind == "L"


# === plan_split_cuts over several cuts ==========================================================


def test_plan_split_cuts_one_result_per_cut() -> None:
    n = 120
    # Two hard luma jumps at 30 and 90.
    frames = [
        np.full((H, GW), 255 if (30 <= f < 90) else 0, dtype=np.uint8) for f in range(n)
    ]
    loader = _loader_for(frames)
    silence = [(30, 36), (88, 94)]  # one around each cut
    out = plan_split_cuts(
        [30, 90], [], silence, window=WINDOW,
        video_path=Path("x.mp4"), total_frames=n, frame_loader=loader,
    )
    assert len(out) == 2
    assert [sc.seq_cut for sc in out] == [30, 90]
    # Every result is a well-formed SplitCut with a valid kind.
    assert all(sc.kind in {"hard", "L", "J"} for sc in out)
    assert all(sc.offset == sc.audio_frame - sc.video_frame for sc in out)


def test_plan_split_cuts_empty() -> None:
    assert plan_split_cuts([], [], None, window=WINDOW) == []
