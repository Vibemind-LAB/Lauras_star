"""TDD for Task 3 -- word-timing sidecar for narration captions.

Spec: docs/superpowers/specs/2026-08-21-narrated-reel-design.md §4.

Test plan (brief (a)-(e)):
  (a) authored_words drops standalone dash tokens, keeps punctuation attached to words.
  (b) map_words_to_slots: 1:1 when counts match, proportional formula
      ``min(n_w-1, j*n_w//n_a)`` when they don't (both compression and expansion), and
      every span is at least 1 frame wide.
  (c) Whisper mocked (monkeypatched module-level loader) -> sidecar source "whisper",
      frames converted from seconds via the project rate (floor start / ceil end).
  (d) Whisper loader returns None (import/availability failure) -> source "even", words
      evenly distributed across measured_frames.
  (e) Exception raised inside the whisper path -> even fallback, never raises.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from laura.ai import vo_words

# ---------------------------------------------------------------------------
# (a) authored_words
# ---------------------------------------------------------------------------


def test_authored_words_drops_standalone_dash_tokens_keeps_punctuation() -> None:
    text = "Line one - line two – line three — done!"
    assert vo_words.authored_words(text) == [
        "Line",
        "one",
        "line",
        "two",
        "line",
        "three",
        "done!",
    ]


def test_authored_words_keeps_punctuation_attached_when_no_dashes() -> None:
    assert vo_words.authored_words("Hello, world! It's Laura.") == [
        "Hello,",
        "world!",
        "It's",
        "Laura.",
    ]


def test_authored_words_empty_text_is_empty_list() -> None:
    assert vo_words.authored_words("") == []
    assert vo_words.authored_words("   ") == []


# ---------------------------------------------------------------------------
# (b) map_words_to_slots
# ---------------------------------------------------------------------------


def test_map_words_to_slots_one_to_one() -> None:
    words = ["a", "b", "c"]
    slots = [(0, 1), (1, 2), (2, 4)]
    assert vo_words.map_words_to_slots(words, slots) == [
        {"text": "a", "start_frame": 0, "end_frame_exclusive": 1},
        {"text": "b", "start_frame": 1, "end_frame_exclusive": 2},
        {"text": "c", "start_frame": 2, "end_frame_exclusive": 4},
    ]


def test_map_words_to_slots_proportional_more_slots_than_words() -> None:
    """One output entry PER AUTHORED WORD (never per slot): n_w=2 authored words,
    n_a=5 timing slots (whisper split the speech finer than the authored text) ->
    exactly 2 entries, both authored words present in order, each picking up the
    proportionally-chosen slot's timing: word j -> slot min(n_a-1, j*n_a//n_w)."""
    words = ["hello", "world"]
    slots = [(0, 2), (2, 4), (4, 6), (6, 8), (8, 10)]
    result = vo_words.map_words_to_slots(words, slots)
    assert len(result) == len(words) == 2
    assert [entry["text"] for entry in result] == ["hello", "world"]
    # word 0 -> slot min(4, 0*5//2)=0 ; word 1 -> slot min(4, 1*5//2)=2
    assert (result[0]["start_frame"], result[0]["end_frame_exclusive"]) == slots[0]
    assert (result[1]["start_frame"], result[1]["end_frame_exclusive"]) == slots[2]


def test_map_words_to_slots_proportional_fewer_slots_than_words() -> None:
    """One output entry PER AUTHORED WORD, no drops: n_w=5 authored words, n_a=2 timing
    slots (whisper under-split the speech) -> exactly 5 entries, all five authored words
    present in order (none dropped) -- several words legitimately share one slot's
    timing: word j -> slot min(n_a-1, j*n_a//n_w)."""
    words = ["a", "b", "c", "d", "e"]
    slots = [(0, 3), (3, 6)]
    result = vo_words.map_words_to_slots(words, slots)
    assert len(result) == len(words) == 5
    assert [entry["text"] for entry in result] == ["a", "b", "c", "d", "e"]
    # word j -> slot min(1, j*2//5): j=0,1,2 -> slot0 ; j=3,4 -> slot1
    assert result == [
        {"text": "a", "start_frame": 0, "end_frame_exclusive": 3},
        {"text": "b", "start_frame": 0, "end_frame_exclusive": 3},
        {"text": "c", "start_frame": 0, "end_frame_exclusive": 3},
        {"text": "d", "start_frame": 3, "end_frame_exclusive": 6},
        {"text": "e", "start_frame": 3, "end_frame_exclusive": 6},
    ]


def test_map_words_to_slots_zero_width_slot_becomes_one_frame() -> None:
    result = vo_words.map_words_to_slots(["x"], [(5, 5)])
    assert result == [{"text": "x", "start_frame": 5, "end_frame_exclusive": 6}]


def test_map_words_to_slots_empty_words_or_slots_is_empty() -> None:
    assert vo_words.map_words_to_slots([], [(0, 1)]) == []
    assert vo_words.map_words_to_slots(["a"], []) == []


# ---------------------------------------------------------------------------
# (c) whisper mocked -> source "whisper", frames from seconds via project rate
# ---------------------------------------------------------------------------


def test_write_word_sidecar_uses_whisper_timings_when_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wav_path = tmp_path / "voice.wav"
    wav_path.write_bytes(b"RIFF....WAVEfmt ")

    monkeypatch.setattr(
        vo_words,
        "_transcribe_words",
        lambda _wav_path, _language: [("Hallo", 0.0, 0.5), ("Laura", 0.5, 1.0)],
    )

    result_path = vo_words.write_word_sidecar(
        wav_path,
        text="Hallo Laura",
        measured_frames=999,  # must be ignored -- whisper timings win
        rate_num=30,
        rate_den=1,
        language="de",
    )

    assert result_path == Path(f"{wav_path}.words.json")
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["source"] == "whisper"
    assert payload["words"] == [
        {"text": "Hallo", "start_frame": 0, "end_frame_exclusive": 15},
        {"text": "Laura", "start_frame": 15, "end_frame_exclusive": 30},
    ]


def test_write_word_sidecar_whisper_authored_text_wins_over_asr_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The TEXT in the sidecar always comes from the payload, never from ASR (ASR
    mishears names) -- only the timing comes from whisper."""
    wav_path = tmp_path / "voice.wav"
    wav_path.write_bytes(b"RIFF....WAVEfmt ")

    monkeypatch.setattr(
        vo_words,
        "_transcribe_words",
        lambda _wav_path, _language: [("Hallow", 0.0, 1.0)],  # ASR misheard "Hallo"
    )

    result_path = vo_words.write_word_sidecar(
        wav_path,
        text="Hallo",
        measured_frames=30,
        rate_num=30,
        rate_den=1,
        language=None,
    )
    assert result_path is not None
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["words"][0]["text"] == "Hallo"


# ---------------------------------------------------------------------------
# (d) whisper loader returns None -> even fallback over measured_frames
# ---------------------------------------------------------------------------


def test_write_word_sidecar_falls_back_to_even_when_whisper_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wav_path = tmp_path / "voice.wav"
    wav_path.write_bytes(b"RIFF....WAVEfmt ")

    monkeypatch.setattr(vo_words, "_transcribe_words", lambda _wav_path, _language: None)

    result_path = vo_words.write_word_sidecar(
        wav_path,
        text="Hallo Laura",
        measured_frames=60,
        rate_num=30,
        rate_den=1,
        language=None,
    )
    assert result_path is not None
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["source"] == "even"
    assert payload["words"] == [
        {"text": "Hallo", "start_frame": 0, "end_frame_exclusive": 30},
        {"text": "Laura", "start_frame": 30, "end_frame_exclusive": 60},
    ]


def test_write_word_sidecar_empty_whisper_words_falls_back_to_even(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A whisper call that succeeds but returns zero words is treated as a failure too."""
    wav_path = tmp_path / "voice.wav"
    wav_path.write_bytes(b"RIFF....WAVEfmt ")

    monkeypatch.setattr(vo_words, "_transcribe_words", lambda _wav_path, _language: [])

    result_path = vo_words.write_word_sidecar(
        wav_path,
        text="Hallo",
        measured_frames=30,
        rate_num=30,
        rate_den=1,
        language=None,
    )
    assert result_path is not None
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["source"] == "even"


# ---------------------------------------------------------------------------
# (e) exception inside the whisper path -> even fallback, never raises
# ---------------------------------------------------------------------------


def test_write_word_sidecar_whisper_exception_falls_back_to_even_without_raising(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wav_path = tmp_path / "voice.wav"
    wav_path.write_bytes(b"RIFF....WAVEfmt ")

    def _boom(_wav_path: Path, _language: str | None) -> list[tuple[str, float, float]]:
        raise RuntimeError("faster-whisper blew up")

    monkeypatch.setattr(vo_words, "_transcribe_words", _boom)

    result_path = vo_words.write_word_sidecar(
        wav_path,
        text="Hallo Laura",
        measured_frames=60,
        rate_num=30,
        rate_den=1,
        language=None,
    )
    assert result_path is not None
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["source"] == "even"
    assert [w["text"] for w in payload["words"]] == ["Hallo", "Laura"]


def test_write_word_sidecar_never_raises_on_write_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A sidecar write failure (e.g. an unwritable path) is logged and swallowed --
    it must never propagate and fail the voiceover job."""
    wav_path = tmp_path / "missing_dir" / "voice.wav"  # parent dir does not exist
    monkeypatch.setattr(vo_words, "_transcribe_words", lambda _wav_path, _language: None)

    result = vo_words.write_word_sidecar(
        wav_path,
        text="Hallo",
        measured_frames=30,
        rate_num=30,
        rate_den=1,
        language=None,
    )
    assert result is None


def test_write_word_sidecar_never_raises_on_unicode_encode_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A payload that cannot be UTF-8 encoded (e.g. a lone/unpaired surrogate that
    survived json.dumps) must not raise -- the write guard also catches
    ValueError/UnicodeError (UnicodeEncodeError is a ValueError subclass), not just
    OSError."""
    wav_path = tmp_path / "voice.wav"
    wav_path.write_bytes(b"RIFF....WAVEfmt ")
    monkeypatch.setattr(vo_words, "_transcribe_words", lambda _wav_path, _language: None)
    # A lone surrogate cannot be encoded as UTF-8 -- forces write_text() to raise
    # UnicodeEncodeError regardless of what the real json.dumps would have produced.
    # `json` is the same module object vo_words.py imported, so patching it here
    # reaches vo_words's call site too.
    monkeypatch.setattr(json, "dumps", lambda _payload: "\ud800")

    result = vo_words.write_word_sidecar(
        wav_path,
        text="Hallo",
        measured_frames=30,
        rate_num=30,
        rate_den=1,
        language=None,
    )
    assert result is None


# ---------------------------------------------------------------------------
# real (unmocked) _transcribe_words: absence of faster-whisper must not raise
# ---------------------------------------------------------------------------


def test_transcribe_words_returns_none_without_raising(tmp_path: Path) -> None:
    """No monkeypatching: exercises the real import-and-transcribe attempt. Whether or
    not faster-whisper happens to be installed in this environment, a nonsense/missing
    WAV path must never raise -- it must degrade to None."""
    missing_wav = tmp_path / "does-not-exist.wav"
    assert vo_words._transcribe_words(missing_wav, None) is None
