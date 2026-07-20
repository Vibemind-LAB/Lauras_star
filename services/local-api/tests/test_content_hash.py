"""One identity for every artifact: content is what it says, version is bookkeeping.

The review-killed restore keyed a render's identity on script TEXT alone; a render is
equally a projection of the concrete cutlist and the concrete voice. content_hash gives every
artifact a single canonical identity so derived artifacts can record exactly which parent
instances they were built from (spec 2026-07-20-provenance-chain-design.md §1).
"""

from __future__ import annotations

from laura.short_creator.board_models import (
    QaReport,
    Script,
    ScriptLine,
    VoiceArtifact,
    content_hash,
)


def _script(text: str) -> Script:
    return Script(language="English", lines=[ScriptLine(chapter=1, scene_number=1, text=text)])


def test_same_content_same_hash_across_versions() -> None:
    """The motivating case: revise A -> B -> back to A must match A's original hash."""
    v1 = _script("the rendered line").model_copy(update={"version": 1})
    v3 = _script("the rendered line").model_copy(update={"version": 3})

    assert content_hash(v1) == content_hash(v3)


def test_different_content_different_hash() -> None:
    assert content_hash(_script("line a")) != content_hash(_script("line b"))


def test_a_new_synthesis_of_the_same_text_is_a_different_voice() -> None:
    """The distinction the killed restore lacked: the cutlist cut against THIS mp3."""
    take_1 = VoiceArtifact(script_hash="h", mp3_path="voiceovers/aaa.mp3")
    take_2 = VoiceArtifact(script_hash="h", mp3_path="voiceovers/bbb.mp3")

    assert content_hash(take_1) != content_hash(take_2)


def test_hash_is_deterministic_across_calls() -> None:
    s = _script("stable")
    assert content_hash(s) == content_hash(s)


def test_parents_defaults_empty_so_old_boards_still_load() -> None:
    """Pre-provenance JSON has no parents key — the default keeps it loading."""
    raw = '{"version": 2, "verdict": "ship", "findings": []}'
    loaded = QaReport.model_validate_json(raw)

    assert loaded.parents == {}


def test_parents_roundtrip() -> None:
    qa = QaReport(verdict="ship", findings=[], parents={"render_report": "abc123"})
    again = QaReport.model_validate_json(qa.model_dump_json())

    assert again.parents == {"render_report": "abc123"}
