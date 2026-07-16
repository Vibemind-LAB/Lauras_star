"""The script's length is arithmetic — but only to about +/-20%, and that honesty matters.

Live finding: the scene_author burned a whole job (script v206 -> v240, 34 saves) trying
to hit a length by feel and never reached the render. The fix is a starting budget from
the code plus ONE measured correction — not a formula pretending to be exact.

The rates here are fitted over every synthesis this board produced. An earlier two-term
model (words*0.55 + lines*0.38) was asserted to be exact after fitting a single sample;
it missed the others by up to 23%. These tests pin BOTH the useful accuracy and the
honest limit, so nobody re-derives false precision from one lucky sample.
"""

from __future__ import annotations

import pytest

from laura.short_creator.board_models import BestWindow
from laura.short_creator.production_tools import (
    _VOICE_RATE_TOLERANCE,
    estimate_voice_seconds,
    storyline_material_seconds,
    word_budget_for,
)

# (words, measured voice_s) for every ElevenLabs synthesis on the live board
LIVE_SYNTHESES = [(78, 38.453), (89, 57.261), (179, 108.391), (228, 157.989)]


@pytest.mark.parametrize(("words", "measured"), LIVE_SYNTHESES)
def test_estimate_lands_within_the_documented_tolerance(words: int, measured: float) -> None:
    estimate = estimate_voice_seconds(words)
    relative_error = abs(estimate - measured) / measured
    assert relative_error <= _VOICE_RATE_TOLERANCE, (
        f"{words} words: estimated {estimate:.1f}s vs measured {measured:.1f}s "
        f"({relative_error:.0%} off) — the rate or the documented tolerance is wrong"
    )


def test_the_rate_really_does_vary_across_scripts() -> None:
    """Guards the honesty: if every sample fitted perfectly, someone dropped a sample."""
    rates = [measured / words for words, measured in LIVE_SYNTHESES]
    assert max(rates) - min(rates) > 0.1, "the spread is real — do not claim an exact formula"


def test_budget_is_the_inverse_of_the_estimate() -> None:
    for target in (40.0, 60.0, 160.0):
        assert estimate_voice_seconds(word_budget_for(target)) <= target


def test_word_budget_is_never_negative() -> None:
    assert word_budget_for(0.0) == 0


def test_material_is_the_sum_of_the_reviewed_windows() -> None:
    windows = [
        (BestWindow(offset_s=0.0, duration_s=2.5), 11.8),
        (BestWindow(offset_s=0.0, duration_s=10.0), 76.1),
    ]
    assert storyline_material_seconds(windows) == pytest.approx(12.5)


def test_a_tiny_window_still_earns_the_segment_floor() -> None:
    assert storyline_material_seconds([(BestWindow(offset_s=0.0, duration_s=0.4), 30.0)]) == 2.0


def test_material_never_exceeds_the_scene_itself() -> None:
    assert storyline_material_seconds([(BestWindow(offset_s=0.0, duration_s=5.0), 3.0)]) == 3.0


def test_empty_storyline_has_no_material() -> None:
    assert storyline_material_seconds([]) == 0.0
