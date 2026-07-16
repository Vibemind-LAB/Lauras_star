"""A narration line must be speakable text — nothing the voice would read out wrong.

Live finding (3-minute run): the scene_author wrote the scene number into every single
line ("3 59 Agenten jetzt sichtbar?", scene_number=3) and appended a meta line
("Bonuszeile nicht erlaubt"). Both are valid strings, so the schema waved them through
and ElevenLabs would have spoken "drei neunundfuenfzig Agenten".
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from laura.short_creator.board_models import ScriptLine


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


def test_plain_line_passes() -> None:
    line = ScriptLine(chapter=2, scene_number=4, text="E-Mails und Git automatisch beantworten")
    assert line.chapter == 2
