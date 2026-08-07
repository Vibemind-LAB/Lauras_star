"""Speech rate is a property of the language, not a constant.

Measured on real ElevenLabs syntheses of this project's own scripts: German 0.58 s/word
(four scripts, +-20% spread), English 0.41 s/word (aggregate over three: 308w->104.8s,
407w->156.4s, 462w->219.9s = 481.1s/1177w = 0.409). An earlier English figure of 0.340 came
from ONE terse hand-written script and was optimistic — natural agent prose runs slower.
German runs slower still because its compounds are long words. A single shared constant made
script_budget ask an English author for the wrong count and the delivered film missed length.
"""

from __future__ import annotations

import pytest

from laura.short_creator.production_tools import (
    estimate_voice_seconds,
    seconds_per_word,
    word_budget_for,
)


def test_german_keeps_the_rate_that_shipped() -> None:
    assert seconds_per_word("German") == pytest.approx(0.58)


def test_english_is_measurably_faster() -> None:
    """Faster than German — outside the +-20% tolerance the single constant claimed."""
    assert seconds_per_word("English") == pytest.approx(0.41)
    assert seconds_per_word("English") < seconds_per_word("German") * 0.8


def test_an_unmeasured_language_falls_back_to_english() -> None:
    """Guessing a rate for a language nobody measured is worse than the shipped default."""
    assert seconds_per_word("Klingon") == seconds_per_word("English")


def test_the_english_budget_is_the_measured_one() -> None:
    """174s of English needs ~424 words at the aggregate 0.41 rate."""
    assert word_budget_for(174.0, "English") == 424
    assert word_budget_for(174.0, "German") == 300


def test_estimate_tracks_the_aggregate_english_measurement() -> None:
    """The three real syntheses averaged 0.409 s/word — a 400-word script speaks ~164s."""
    assert estimate_voice_seconds(400, "English") == pytest.approx(164.0, abs=1.0)


def test_both_budget_directions_agree() -> None:
    for language in ("German", "English"):
        words = word_budget_for(120.0, language)
        assert estimate_voice_seconds(words, language) == pytest.approx(120.0, abs=1.0)


def test_the_language_argument_defaults_to_english() -> None:
    assert word_budget_for(174.0) == word_budget_for(174.0, "English")
    assert estimate_voice_seconds(100) == estimate_voice_seconds(100, "English")
