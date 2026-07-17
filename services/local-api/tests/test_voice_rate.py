"""Speech rate is a property of the language, not a constant.

Measured on real ElevenLabs syntheses of this project's own scripts: German 0.58 s/word
(four scripts, +-20% spread), English 0.340 s/word (one script: 308 words -> 104.77s,
verified by script_hash). German runs slower because its compounds are long words. A single
shared constant made script_budget ask an English author for 300 words where 174 seconds
needed 512 — and the delivered film came out at half its target length.
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
    """1.7x faster — far outside the +-20% tolerance the single constant claimed."""
    assert seconds_per_word("English") == pytest.approx(0.340)
    assert seconds_per_word("English") < seconds_per_word("German") * 0.8


def test_an_unmeasured_language_falls_back_to_german() -> None:
    """Guessing a rate for a language nobody measured is worse than the shipped default."""
    assert seconds_per_word("Klingon") == seconds_per_word("German")


def test_the_english_budget_is_the_measured_one() -> None:
    """174s of English needs ~512 words; the old single constant said 300."""
    assert word_budget_for(174.0, "English") == 511
    assert word_budget_for(174.0, "German") == 300


def test_estimate_round_trips_against_the_live_measurement() -> None:
    """308 English words really did synthesize to 104.77s."""
    assert estimate_voice_seconds(308, "English") == pytest.approx(104.7, abs=0.5)


def test_both_budget_directions_agree() -> None:
    for language in ("German", "English"):
        words = word_budget_for(120.0, language)
        assert estimate_voice_seconds(words, language) == pytest.approx(120.0, abs=1.0)


def test_the_language_argument_defaults_to_german_so_old_callers_are_unchanged() -> None:
    assert word_budget_for(174.0) == word_budget_for(174.0, "German")
    assert estimate_voice_seconds(100) == estimate_voice_seconds(100, "German")
