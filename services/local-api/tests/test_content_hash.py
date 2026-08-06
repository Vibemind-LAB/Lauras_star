"""One identity for every artifact: content is what it says, version is bookkeeping.

The review-killed restore keyed a render's identity on script TEXT alone; a render is
equally a projection of the concrete cutlist and the concrete voice. content_hash gives every
artifact a single canonical identity so derived artifacts can record exactly which parent
instances they were built from (spec 2026-07-20-provenance-chain-design.md §1).
"""

from __future__ import annotations

import json

from laura.short_creator.board_models import (
    QaReport,
    Script,
    ScriptLine,
    VoiceArtifact,
    VoiceSegment,
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


# --- VoiceArtifact.segments: back-compat serialization (VS2 review finding) --------------------


def test_voice_artifact_without_segments_omits_the_key() -> None:
    """``segments`` defaults to None (every board written before per-scene voice). A dumped
    artifact must not carry a ``"segments": null`` key, or every pre-VS2 board's content_hash
    shifts the moment this field was added."""
    voice = VoiceArtifact(script_hash="h", mp3_path="voiceovers/aaa.mp3")

    dumped = json.loads(voice.model_dump_json())

    assert "segments" not in dumped


def test_content_hash_of_legacy_json_matches_a_freshly_built_twin() -> None:
    """A VoiceArtifact validated from a pre-VS2-shaped JSON dict (no ``segments`` key at all)
    must hash identically to the same-fielded artifact built today (which now HAS a ``segments``
    attribute, just unset) — otherwise every existing board's downstream ``parents["voice"]``
    stamp reads stale and restore_coherent_suffix refuses old, healthy archives."""
    legacy_json = {
        "version": 1,
        "script_hash": "h",
        "mp3_path": "voiceovers/aaa.mp3",
        "timings_path": None,
        "voice_s": 1.2,
        "parents": {},
    }
    legacy = VoiceArtifact.model_validate(legacy_json)
    today = VoiceArtifact(script_hash="h", mp3_path="voiceovers/aaa.mp3", voice_s=1.2)

    assert content_hash(legacy) == content_hash(today)


def test_voice_artifact_with_segments_roundtrips_and_hashes_differently() -> None:
    """An artifact WITH per-line segments keeps them through a dump/validate round-trip (the
    key is only dropped when segments is None) and hashes DIFFERENTLY from its segments=None
    twin — the per-line data is real content, not incidental."""
    segment = VoiceSegment(
        scene_number=1, chapter=1, line_hash="lh", mp3_path="voiceovers/lines/lh.mp3",
        duration_s=1.2, offset_s=0.0,
    )
    with_segments = VoiceArtifact(
        script_hash="h", mp3_path="voiceovers/aaa.mp3", segments=[segment]
    )
    without_segments = VoiceArtifact(script_hash="h", mp3_path="voiceovers/aaa.mp3")

    round_tripped = VoiceArtifact.model_validate_json(with_segments.model_dump_json())

    assert round_tripped.segments == [segment]
    assert content_hash(with_segments) != content_hash(without_segments)
