"""A narration line must be speakable text — nothing the voice would read out wrong.

Live finding (3-minute run): the scene_author wrote the scene number into every single
line ("3 59 Agenten jetzt sichtbar?", scene_number=3) and appended a meta line
("Bonuszeile nicht erlaubt"). Both are valid strings, so the schema waved them through
and ElevenLabs would have spoken "drei neunundfuenfzig Agenten".
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from laura.short_creator.board_models import ScriptLine, stage_direction_label


def test_leading_scene_number_is_rejected() -> None:
    """The exact live defect: the scene number leaked in front of the narration."""
    with pytest.raises(ValidationError) as excinfo:
        ScriptLine(chapter=1, scene_number=3, text="3 59 Agenten jetzt sichtbar?")

    assert "scene number" in str(excinfo.value).lower()


def test_leading_scene_number_is_rejected_for_multi_digit_scenes() -> None:
    with pytest.raises(ValidationError):
        ScriptLine(chapter=14, scene_number=37, text="37 Jetzt testen - Agenten starten!")


def test_content_number_that_is_not_the_scene_number_is_fine() -> None:
    """'59' leads the hook and has nothing to do with scene 3 — must pass."""
    line = ScriptLine(chapter=1, scene_number=3, text="59 Agenten jetzt sichtbar?")
    assert line.text == "59 Agenten jetzt sichtbar?"


def test_scene_number_inside_the_sentence_is_fine() -> None:
    line = ScriptLine(chapter=1, scene_number=3, text="Wir starten 3 Agenten sofort")
    assert line.text.endswith("sofort")


def test_stage_direction_in_parentheses_is_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        ScriptLine(chapter=1, scene_number=3, text="(zeigt das Dashboard) Agenten laufen")

    assert "narration" in str(excinfo.value).lower()


def test_bracketed_stage_direction_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ScriptLine(chapter=1, scene_number=3, text="[Cut] Agenten laufen")


def test_blank_text_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ScriptLine(chapter=1, scene_number=3, text="   ")


# --- spoken stage directions -------------------------------------------------------------
# Live finding: the bracket rule was the right instinct but the wrong shape. Three autonomous
# runs labelled their lines instead of bracketing them, and the label went straight into the
# voice: every synthesis contains "Narration:" 8x and "CAPTION:" 8x, spoken aloud. The films
# literally say "CAPTION: Cold open, org chart on screen" to a viewer looking at the screen.
# The strings below are the real ones, taken off the board.
#
# The detector is checked here; save_script_chapter is what rejects on it (see
# test_production_tools_write). It deliberately does NOT live in the model validator: that
# also runs on load, and a board written before this rule must stay readable.


def test_the_narration_label_that_shipped_is_detected() -> None:
    """Verbatim from the autonomous script — the voice really said this."""
    assert (
        stage_direction_label("Narration: One input, one mission produced a full org chart.")
        == "Narration"
    )


def test_the_caption_label_mid_sentence_is_detected() -> None:
    """CAPTION: did not start the line — it sat mid-text, so a prefix check would miss it."""
    found = stage_direction_label("One input, one mission. CAPTION: Cold open, org chart.")
    assert found == "CAPTION"


@pytest.mark.parametrize(
    "text",
    [
        "VO: the agents are running",
        "Voiceover: the agents are running",
        "On-screen: 36 agents, 9 teams",
        "SFX: a soft chime",
        "TITLE: Captain Cook",
        "B-ROLL: the dashboard scrolls",
    ],
)
def test_other_stage_direction_labels_are_detected(text: str) -> None:
    assert stage_direction_label(text) is not None


def test_a_colon_in_normal_narration_is_not_a_label() -> None:
    """The rule must catch labels, not punctuation — a spoken colon is ordinary prose."""
    assert (
        stage_direction_label(
            "It writes down exactly why it wants each one: filesystem, memory, reasoning."
        )
        is None
    )


def test_a_sentence_merely_mentioning_a_caption_is_not_a_label() -> None:
    """'caption' as a word is not a label — only 'CAPTION:' as a marker is."""
    assert stage_direction_label("The caption under the graph names every agent.") is None


def test_a_stored_line_with_a_label_still_loads() -> None:
    """Backward compatibility: boards written before this rule must stay readable. Rejecting
    in the model validator broke exactly this — the live board would not load at all."""
    line = ScriptLine(chapter=1, scene_number=1, text="Narration: the agents are running")
    assert line.text.startswith("Narration:")


def test_plain_line_passes() -> None:
    line = ScriptLine(chapter=2, scene_number=4, text="E-Mails und Git automatisch beantworten")
    assert line.chapter == 2
