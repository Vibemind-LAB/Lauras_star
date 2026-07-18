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
    budget_words_for,
    chapter_word_budgets,
    estimate_voice_seconds,
    storyline_material_seconds,
    usable_budget_seconds,
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


# --- the usable length: never more than the target, never more than the material ----------
# Live finding: script_budget budgeted against material, get_script's shortfall against
# target. When the footage (142s) fell short of the target (180s), the shortfall drove the
# author PAST the material — the voice ran longer than any cut of the scenes could cover,
# voice_fits failed unsatisfiably, and the loop thrashed the whole render chain trying to
# fix an impossible task. The two tools must agree, and they must agree on this number.


def test_short_material_caps_the_budget_at_the_footage() -> None:
    """142s of footage for a 180s target: the script may only fill the footage."""
    assert usable_budget_seconds(material_seconds=142.0, target_seconds=180.0) == 142.0


def test_plenty_of_material_caps_the_budget_at_the_target() -> None:
    """250s of footage for a 180s target: the script must not overshoot the target."""
    assert usable_budget_seconds(material_seconds=250.0, target_seconds=180.0) == 180.0


def test_no_material_falls_back_to_the_target() -> None:
    """Without a storyline there is no material yet — the target is the only bound."""
    assert usable_budget_seconds(material_seconds=0.0, target_seconds=180.0) == 180.0


def test_the_usable_budget_is_symmetric_in_the_bound_that_binds() -> None:
    """It is exactly the smaller of the two whenever both are positive."""
    assert usable_budget_seconds(material_seconds=90.0, target_seconds=120.0) == 90.0
    assert usable_budget_seconds(material_seconds=120.0, target_seconds=90.0) == 90.0


# --- headroom: the two directions are not equally bad -------------------------------------
# Live finding: with usable=170s the budget asked for ~415 words. The synthesis came back at
# 171.6s (0.431 s/word against the table's 0.41 — inside the +-20% tolerance, but 5% over),
# so the voice ran 2s past the video and voice_fits failed. The same overshoot pressure also
# pushed the author to pad: it wrote a grounded first line per chapter, then a second line of
# invented capabilities to reach the count.
#
# Overshooting truncates the ending. Undershooting holds the last frames a moment longer.
# Those are not equally bad, so the budget must not aim at the middle of the estimate.


def test_the_budget_leaves_room_for_the_rate_to_run_slow() -> None:
    """Asking for exactly usable-seconds-worth of words overshoots half the time."""
    assert budget_words_for(170.0, "English") < word_budget_for(170.0, "English")


def test_the_headroom_covers_the_overshoot_that_shipped() -> None:
    """The run that failed: 170s usable, synthesis at the measured 0.431 s/word."""
    words = budget_words_for(170.0, "English")
    assert words * 0.431 <= 170.0, "the budget must fit even when the rate runs slow"


def test_the_headroom_does_not_waste_the_material() -> None:
    """Headroom is insurance, not a haircut — most of the footage still gets used."""
    words = budget_words_for(170.0, "English")
    assert words * 0.41 >= 170.0 * 0.85, "at the table rate it should still fill ~90%"


def test_headroom_applies_to_every_language() -> None:
    for language in ("German", "English"):
        assert budget_words_for(120.0, language) < word_budget_for(120.0, language)


# --- per chapter: a global budget can be right while every chapter is wrong ----------------
# Live finding: 161s of voice against 170s of material looked healthy, and the film still
# failed voice_fits by 13s. Per chapter it was badly skewed — chapter 3 carried 26.8s of
# narration against a 1.0s reviewed window, chapter 4 18.6s against 5.0s, while chapters 5
# and 6 left 48s of reviewed material unused. The cutlist cannot cover voice that its scenes
# do not hold, so the video came out short no matter how the total added up.


def test_each_chapter_gets_the_budget_its_own_material_holds() -> None:
    """The 1-second window that broke the run: its chapter may carry almost no words."""
    budgets = chapter_word_budgets({1: 45.0, 3: 1.0}, "English")

    assert budgets[3] < 5, "a 1s window cannot carry a 27s beat — say so"
    assert budgets[1] > 80


def test_the_chapter_budgets_add_up_to_the_whole() -> None:
    """Splitting must not invent or lose budget: the parts are the whole."""
    materials = {1: 15.0, 2: 15.0, 3: 40.0}
    parts = sum(chapter_word_budgets(materials, "English").values())
    whole = budget_words_for(sum(materials.values()), "English")

    assert abs(parts - whole) <= len(materials), "only integer rounding may differ"


def test_every_chapter_carries_the_same_headroom() -> None:
    """A chapter is budgeted like the film: below its material, not at it."""
    budgets = chapter_word_budgets({1: 100.0}, "English")
    assert budgets[1] == budget_words_for(100.0, "English")


def test_an_empty_storyline_budgets_nothing() -> None:
    assert chapter_word_budgets({}, "English") == {}
