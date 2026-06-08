"""Unit tests for semantic & speaker-aware cut placement (:mod:`laura.analysis.semantic`).

Pure and deterministic — no IO, no ffmpeg. Words are hand-built in source-frame space with the new
optional ``text`` / ``speaker`` enrichments. These tests pin:

* sentence-end detection from punctuation (``.?!…``) and the long-pause clause fallback;
* speaker-turn detection on a label change between consecutive words;
* graceful degradation — both return empty sets when no word carries text / speaker;
* the joint scorer preferring a speaker turn over a bare gap and a sentence end over a bare gap,
  while the silence > word-edge > mid-word ordering is preserved;
* the new ``editorial_metrics`` fields ``pct_on_sentence_end`` / ``pct_on_speaker_turn``.

NB: the test *module* name is ``test_semantic_cut`` because ``test_semantic`` already covers the
unrelated Qdrant transcript-search feature (:mod:`laura.semantic`).
"""

from __future__ import annotations

import pytest

from laura.analysis.editorial import Word, editorial_metrics
from laura.analysis.joint import joint_place
from laura.analysis.semantic import (
    DEFAULT_CLAUSE_GAP_FRAMES,
    sentence_end_frames,
    speaker_turn_frames,
)

WINDOW = 12


# === sentence-end detection =====================================================================


def test_sentence_end_from_punctuation() -> None:
    # Whisper-style tokens: leading space, trailing punctuation. The end frame of the word whose
    # text ends in ".?!…" is a sentence boundary. "world." ends a sentence -> its end_frame (40).
    words = [
        Word(start_frame=10, end_frame=20, text=" Hello"),
        Word(start_frame=20, end_frame=40, text=" world."),
        Word(start_frame=40, end_frame=60, text=" Next"),
    ]
    assert sentence_end_frames(words) == {40}


def test_sentence_end_recognises_all_terminators() -> None:
    # "?", "!", and the single-codepoint ellipsis "…" all close a sentence; "..." ends in "." too.
    words = [
        Word(start_frame=0, end_frame=10, text="really?"),
        Word(start_frame=10, end_frame=20, text="stop!"),
        Word(start_frame=20, end_frame=30, text="well…"),
        Word(start_frame=30, end_frame=40, text="hmm..."),
        Word(start_frame=40, end_frame=50, text="plain"),  # no terminator -> not an end
    ]
    assert sentence_end_frames(words, clause_gap_frames=0) == {10, 20, 30, 40}


def test_sentence_end_long_pause_fallback() -> None:
    # No punctuation anywhere, but a long pause (>= clause gap) after word 0 marks a clause/sentence
    # boundary at its end frame; the short gap after word 1 does not.
    words = [
        Word(start_frame=0, end_frame=10, text="alpha"),     # gap 10->40 == 30 frames (>= 15)
        Word(start_frame=40, end_frame=50, text="beta"),     # gap 50->55 == 5 frames (< 15)
        Word(start_frame=55, end_frame=70, text="gamma"),
    ]
    assert sentence_end_frames(words, clause_gap_frames=DEFAULT_CLAUSE_GAP_FRAMES) == {10}


def test_sentence_end_empty_without_text() -> None:
    # Timing-only words (no text) -> no sentence signal (graceful degrade). Disable the pause
    # fallback so this isolates the punctuation path; even abutting words yield nothing.
    words = [
        Word(start_frame=10, end_frame=20),
        Word(start_frame=20, end_frame=40),
    ]
    assert sentence_end_frames(words, clause_gap_frames=0) == set()


def test_sentence_end_empty_word_list() -> None:
    assert sentence_end_frames([]) == set()


# === speaker-turn detection =====================================================================


def test_speaker_turn_on_label_change() -> None:
    # A change A -> B between word 1 and word 2 marks a turn at word 1's end (20). No change A -> A
    # earlier, and B -> B later -> only one turn frame.
    words = [
        Word(start_frame=0, end_frame=10, text="a", speaker="SPEAKER_00"),
        Word(start_frame=10, end_frame=20, text="b", speaker="SPEAKER_00"),
        Word(start_frame=24, end_frame=40, text="c", speaker="SPEAKER_01"),
        Word(start_frame=40, end_frame=50, text="d", speaker="SPEAKER_01"),
    ]
    assert speaker_turn_frames(words) == {20}


def test_speaker_turn_multiple_changes() -> None:
    words = [
        Word(start_frame=0, end_frame=10, speaker="A"),
        Word(start_frame=10, end_frame=20, speaker="B"),  # turn at 10
        Word(start_frame=20, end_frame=30, speaker="A"),  # turn at 20
    ]
    assert speaker_turn_frames(words) == {10, 20}


def test_speaker_turn_ignores_missing_labels() -> None:
    # A word with an unknown (None) speaker neither opens nor closes a turn: we never invent a
    # change against missing data. Only the real A -> B (both labelled) contributes.
    words = [
        Word(start_frame=0, end_frame=10, speaker="A"),
        Word(start_frame=10, end_frame=20, speaker=None),   # unknown -> no turn either side
        Word(start_frame=20, end_frame=30, speaker="B"),
    ]
    assert speaker_turn_frames(words) == set()


def test_speaker_turn_empty_without_speaker() -> None:
    words = [
        Word(start_frame=0, end_frame=10, text="a"),
        Word(start_frame=10, end_frame=20, text="b"),
    ]
    assert speaker_turn_frames(words) == set()


def test_speaker_turn_empty_word_list() -> None:
    assert speaker_turn_frames([]) == set()


# === joint placement prefers semantic seams =====================================================


def test_joint_prefers_speaker_turn_over_bare_gap() -> None:
    # Two clean word edges reachable from the cut: 30 (a speaker turn) and 33 (a bare gap). No
    # visual signal -> the editorial tier decides. Speaker turn (1.0) beats a bare word edge, so
    # the cut lands on the turn even though 33 is the same kind of clean edge.
    words = [
        Word(start_frame=10, end_frame=30, speaker="A"),
        Word(start_frame=33, end_frame=60, speaker="B"),
    ]
    speaker_frames = speaker_turn_frames(words)  # {30}
    assert speaker_frames == {30}
    # cut 32 is in the gap [30, 33]; both 30 and 33 are clean. Turn at 30 must win.
    frame, _score = joint_place(
        32, words, None, window=WINDOW, speaker_frames=speaker_frames
    )
    assert frame == 30


def test_joint_prefers_sentence_end_over_bare_gap() -> None:
    # Sentence end at 30 (end of "thought.") vs a bare gap edge at 33. Sentence end (0.95) beats a
    # bare word edge (0.70) -> the cut snaps to the sentence boundary.
    words = [
        Word(start_frame=10, end_frame=30, text=" thought."),
        Word(start_frame=33, end_frame=60, text=" Next"),
    ]
    sentence_frames = sentence_end_frames(words, clause_gap_frames=0)  # {30}
    assert sentence_frames == {30}
    frame, _score = joint_place(
        32, words, None, window=WINDOW, sentence_frames=sentence_frames
    )
    assert frame == 30


def test_speaker_turn_outscores_sentence_end_outscores_silence() -> None:
    # Pin one frame each (window=0, no visual) so the score is purely the editorial tier, then check
    # the strict ordering speaker-turn > sentence-end > silence > word-edge.
    words = [
        Word(start_frame=0, end_frame=10, text="x.", speaker="A"),
        Word(start_frame=10, end_frame=20, text="y", speaker="B"),
    ]
    turn = {10}
    sentence = {10}
    silence = [(40, 50)]
    # Frame 10 is a clean edge that is a turn AND a sentence end; isolate each tier in turn.
    _f, s_turn = joint_place(10, words, None, window=0, speaker_frames=turn)
    _f, s_sentence = joint_place(10, words, None, window=0, sentence_frames=sentence)
    _f, s_silence = joint_place(45, words, None, window=0, silence=silence)  # 45 in silence
    _f, s_edge = joint_place(10, words, None, window=0, silence=silence)  # 10 clean, has context
    assert s_turn > s_sentence > s_silence > s_edge


def test_semantic_frames_preserve_silence_over_edge_ordering() -> None:
    # With semantics present but the candidate frames carrying neither a turn nor a sentence end,
    # the existing silence (0.85) > word-edge (0.70) ordering must still hold.
    words = [Word(start_frame=20, end_frame=34, text="word"), Word(start_frame=46, end_frame=60)]
    silence = [(38, 46)]
    # frame 40 in silence, frame 34 a bare clean edge. Pass empty semantic sets (no turn/sentence).
    _f, s_silence = joint_place(40, words, None, window=0, silence=silence, sentence_frames=set())
    _f, s_edge = joint_place(34, words, None, window=0, silence=silence, sentence_frames=set())
    assert s_silence > s_edge


def test_no_semantic_no_silence_is_backward_compatible() -> None:
    # No silence, no semantic sets -> the clean word edge scores the historical 1.0 editorial, i.e.
    # exactly the pre-semantic behaviour. cut 30 mid-word [10,33); nearest clean edge 33.
    words = [Word(start_frame=10, end_frame=33), Word(start_frame=48, end_frame=70)]
    frame, score = joint_place(30, words, None, window=WINDOW)
    assert frame == 33
    # editorial clean == 1.0 (bare), visual 0 -> blended 0.4 * 1.0 with default weights.
    assert score == pytest.approx(0.4)


def test_semantic_seam_never_rewards_mid_word() -> None:
    # A sentence end / speaker turn that coincides with a frame INSIDE a word must not be rewarded:
    # we never cut into speech just because a seam frame is there. Here 25 is mid-word [10,40) and
    # also (artificially) in the semantic sets; with window 0 it must score mid-word (0.0), not 1.0.
    words = [Word(start_frame=10, end_frame=40, text="straddle", speaker="A")]
    _f, score = joint_place(
        25, words, None, window=0, speaker_frames={25}, sentence_frames={25}
    )
    assert score == pytest.approx(0.0)


# === metrics fields =============================================================================


def test_editorial_metrics_reports_semantic_fields() -> None:
    words = [Word(start_frame=10, end_frame=30), Word(start_frame=33, end_frame=60)]
    sentence_frames = {30}
    speaker_frames = {30}
    cuts = [30, 33, 20]  # 30 on both seams, 33 a bare edge, 20 mid-word
    m = editorial_metrics(
        cuts, words, sentence_frames=sentence_frames, speaker_frames=speaker_frames
    )
    assert m["pct_on_sentence_end"] == pytest.approx(1 / 3)
    assert m["pct_on_speaker_turn"] == pytest.approx(1 / 3)
    # Without the inputs the keys are absent (backward compatible).
    bare = editorial_metrics(cuts, words)
    assert "pct_on_sentence_end" not in bare
    assert "pct_on_speaker_turn" not in bare


def test_editorial_metrics_semantic_fields_with_no_cuts() -> None:
    m = editorial_metrics([], [], sentence_frames={1}, speaker_frames={2})
    assert m["pct_on_sentence_end"] == 0.0
    assert m["pct_on_speaker_turn"] == 0.0
