"""Unit tests for joint visual+editorial cut placement (:mod:`laura.analysis.joint`).

Pure and deterministic. The visual signal is supplied two ways, mirroring ``eval_cut``'s seam:

* as a precomputed ``diff`` aligned to the candidate band (``diff[i]`` is ``d`` at frame
  ``lo+i`` with ``lo == max(cut-window, 1)``), and
* via an injected ``frame_loader`` slicing an in-memory grayscale sequence (no ffmpeg).

The headline scenario reproduces the real-test tradeoff: the visual peak sits mid-word while a
clean word edge sits a few frames away. Joint placement must resolve it by the *blended* score —
favouring the clean edge under default weights, staying on the peak under picture-first weights.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from laura.analysis.editorial import Word
from laura.analysis.eval_cut import FrameLoader
from laura.analysis.joint import bias_to_weights, joint_place

WINDOW = 12


def _ramp_diff(cut: int, peak_frame: int, edge_frame: int, edge_level: float,
               *, window: int = WINDOW) -> list[float]:
    """A realistic ``diff`` over the band: a strong peak at ``peak_frame`` and a still-high value
    at the nearby ``edge_frame``, everything else near zero.

    ``lo == max(cut-window, 1)``; entry ``i`` is ``d`` at frame ``lo+i``. The peak gets ``1.0`` and
    the edge ``edge_level`` (after normalisation those map to visual scores 1.0 and ``edge_level``),
    modelling a clean word edge that sits just off a real transition and is therefore *not* visually
    dead — the case where the default blend can legitimately prefer the edge.
    """
    lo = max(cut - window, 1)
    n = (cut + window) - lo + 1
    diff = [0.02] * n
    diff[peak_frame - lo] = 1.0
    diff[edge_frame - lo] = edge_level
    return diff


# === headline: visual peak is mid-word, a clean word edge sits a few frames away ================
#
# Real-test situation: the picture changes most at frame 270, but 270 is in the middle of a spoken
# word; the nearest clean word edge is 273, which is only 3 frames off a real transition so it is
# still visually strong (edge visual ~0.75 of the peak). The two stages disagree and joint
# placement must resolve it by the *blended* score, not an all-or-nothing snap.
#
# Word layout: a word spans [260, 273) so frame 270 is strictly inside it (mid-word) and frame 273
# is its end edge (end-exclusive -> clean). A following word [273, 290) keeps 273 a true edge.

CUT = 270
PEAK = 270
EDGE = 273
EDGE_VISUAL = 0.75
_HEADLINE_WORDS = [Word(start_frame=260, end_frame=273), Word(start_frame=273, end_frame=290)]


def _headline_diff() -> list[float]:
    return _ramp_diff(CUT, PEAK, EDGE, EDGE_VISUAL)


def test_default_weights_favour_clean_edge_over_mid_word_peak() -> None:
    # Default 0.6/0.4. Peak (270): visual 1.0, editorial 0.0 -> (0.6*1.0 + 0.4*0.0) = 0.60.
    # Edge (273): visual 0.75, editorial 1.0 -> (0.6*0.75 + 0.4*1.0) = 0.85. 0.85 > 0.60 -> edge.
    # This is the resolution of the real tradeoff: the joint blend keeps the cut frame-clean AND
    # nearly as visually strong, instead of either trashing visual (old editorial snap) or speech.
    frame, score = joint_place(CUT, _HEADLINE_WORDS, _headline_diff(), window=WINDOW)
    assert frame == EDGE
    assert score == pytest.approx(0.6 * EDGE_VISUAL + 0.4 * 1.0)


def test_picture_first_bias_stays_on_visual_peak() -> None:
    # cut_bias = 0 -> picture-first -> w_editorial 0 -> pure visual peak even when it is mid-word.
    # Peak visual 1.0 beats the edge's 0.75: the frame-exact cut is preserved.
    w_visual, w_editorial = bias_to_weights(0.0)
    frame, _score = joint_place(
        CUT, _HEADLINE_WORDS, _headline_diff(), window=WINDOW,
        w_visual=w_visual, w_editorial=w_editorial,
    )
    assert frame == PEAK


def test_sound_first_bias_favours_clean_edge() -> None:
    # cut_bias = 1 -> sound-first -> w_visual 0 -> pure editorial: the clean edge beats the mid-word
    # peak. Among clean frames the one nearest the cut wins; 273 (dist 3) is the closest clean edge.
    w_visual, w_editorial = bias_to_weights(1.0)
    frame, _score = joint_place(
        CUT, _HEADLINE_WORDS, _headline_diff(), window=WINDOW,
        w_visual=w_visual, w_editorial=w_editorial,
    )
    assert frame == EDGE


# === degenerate / graceful cases ================================================================


def test_empty_words_reduces_to_visual_peak() -> None:
    # No transcript -> editorial term is constant (everything clean) -> pure visual peak.
    frame, _score = joint_place(CUT, [], _headline_diff(), window=WINDOW)
    assert frame == PEAK


def test_visual_only_weight_reduces_to_visual_peak() -> None:
    # w_editorial == 0 even with mid-word words -> exactly the refine visual-peak choice.
    frame, _score = joint_place(
        CUT, _HEADLINE_WORDS, _headline_diff(), window=WINDOW, w_editorial=0.0
    )
    assert frame == PEAK


def test_no_diff_no_video_editorial_only_decides() -> None:
    # No visual signal at all -> visual scores all zero -> editorial term alone decides. The clean
    # frame nearest the cut wins; with cut mid-word [260,273) the nearest edge is 273.
    frame, _score = joint_place(CUT, _HEADLINE_WORDS, None, window=WINDOW)
    assert frame == EDGE


def test_no_signal_and_all_clean_leaves_cut_unchanged() -> None:
    # No visual signal and the cut is already clean (on a word edge) -> nothing beats it, unchanged.
    words = [Word(start_frame=240, end_frame=270), Word(start_frame=270, end_frame=300)]
    frame, _score = joint_place(270, words, None, window=WINDOW)
    assert frame == 270


# === tie-breaking: nearest to the original cut ==================================================


def test_tie_breaks_to_frame_nearest_original_cut() -> None:
    # Two equally clean, equally visually-flat candidates straddling the cut at equal distance.
    # With no visual peak both score the same; the tie must resolve to the nearest -> the cut frame
    # itself (distance 0) when it is clean.
    words = [Word(start_frame=200, end_frame=265), Word(start_frame=275, end_frame=320)]
    # cut 270 sits in the gap [265, 275] -> clean. All gap frames are clean and visually flat.
    frame, _score = joint_place(270, words, None, window=WINDOW)
    assert frame == 270  # distance 0 beats any other clean frame in the gap


def test_tie_among_clean_frames_picks_nearest_then_lower() -> None:
    # Cut is mid-word; two clean edges equidistant on each side. Nearest-then-lower -> the lower.
    # Word [268, 272): cut 270 mid-word; edges 268 (dist 2) and 272 (dist 2). Lower wins -> 268.
    words = [
        Word(start_frame=250, end_frame=268),
        Word(start_frame=268, end_frame=272),
        Word(start_frame=272, end_frame=300),
    ]
    frame, _score = joint_place(270, words, None, window=WINDOW)
    assert frame == 268


# === window is respected ========================================================================


def test_window_respected_clean_edge_outside_window_not_chosen() -> None:
    # The only clean frames are far outside a tiny window -> nothing reachable -> cut stays put.
    words = [Word(start_frame=0, end_frame=1000)]  # cut deep inside one long word
    frame, _score = joint_place(500, words, None, window=3)
    # Every frame in [497, 503] is mid-word (no clean candidate, no visual signal) -> all score 0;
    # the tie resolves to the nearest = the original cut.
    assert frame == 500


def test_window_caps_the_search_band() -> None:
    # A clean edge 5 frames away is NOT reachable with window=4, but IS with window=6.
    words = [Word(start_frame=200, end_frame=275), Word(start_frame=275, end_frame=320)]
    # cut 270 mid-word; nearest clean edge is 275 (dist 5).
    near = joint_place(270, words, None, window=4)[0]
    assert near == 270  # 275 out of a +/-4 window -> nothing clean reachable -> unchanged
    far = joint_place(270, words, None, window=6)[0]
    assert far == 275  # 275 within +/-6 -> snaps to the clean edge


# === frame_loader seam: decode the diff from an in-memory sequence (no ffmpeg) ==================

H, GW = 8, 8


def _step_sequence(peak: int, n: int) -> list[np.ndarray]:
    """Black before ``peak``, white from ``peak`` on -> a single diff peak at frame ``peak``."""
    return [np.full((H, GW), 0 if f < peak else 255, dtype=np.uint8) for f in range(n)]


def _loader_for(frames: list[np.ndarray]) -> FrameLoader:
    def loader(_video: Path | str, lo: int, hi: int) -> list[np.ndarray]:
        lo = max(0, lo)
        hi = min(len(frames), hi)
        return [frames[i] for i in range(lo, hi)]

    return loader


def test_frame_loader_seam_decodes_visual_peak() -> None:
    # A hard luma jump at frame 30; no words -> pure visual -> joint lands on 30. Cut placed 3 off.
    n = 60
    loader = _loader_for(_step_sequence(30, n))
    frame, score = joint_place(
        33, [], None, window=WINDOW, video_path=Path("x.mp4"), total_frames=n, frame_loader=loader
    )
    assert frame == 30
    # visual 1.0 at the peak, editorial vacuously clean (1.0 with empty words) -> blend 1.0.
    assert score == pytest.approx(1.0)


def test_frame_loader_io_error_degrades_to_editorial() -> None:
    # A loader that raises -> no visual signal -> editorial term alone decides (clean edge wins).
    def boom(_video: Path | str, _lo: int, _hi: int) -> list[np.ndarray]:
        raise OSError("decode exploded")

    frame, _score = joint_place(
        CUT, _HEADLINE_WORDS, None, window=WINDOW, video_path=Path("x.mp4"), frame_loader=boom
    )
    assert frame == EDGE


# === validation / guards ========================================================================


def test_negative_window_rejected() -> None:
    with pytest.raises(ValueError, match="window"):
        joint_place(10, [], None, window=-1)


def test_negative_weight_rejected() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        joint_place(10, [], None, w_visual=-1.0, w_editorial=1.0)


def test_zero_weight_sum_rejected() -> None:
    with pytest.raises(ValueError, match="positive"):
        joint_place(10, [], None, w_visual=0.0, w_editorial=0.0)


def test_bias_to_weights_mapping() -> None:
    assert bias_to_weights(None) == (0.6, 0.4)
    assert bias_to_weights(0.0) == (1.0, 0.0)
    assert bias_to_weights(1.0) == (0.0, 1.0)
    assert bias_to_weights(0.25) == (0.75, 0.25)
    # Out-of-range values clamp into [0, 1].
    assert bias_to_weights(-5.0) == (1.0, 0.0)
    assert bias_to_weights(5.0) == (0.0, 1.0)
