"""Semantic & speaker-aware cut boundaries (pure, no IO) — sentence ends and speaker turns.

Editorial cut placement (:mod:`laura.analysis.editorial`, :mod:`laura.analysis.joint`) ranks a
candidate cut frame by how *safe* it is: a real audio silence beats a clean word-gap, which beats
a mid-word frame. That captures the acoustics of a cut, but not its *meaning*. A cut that lands on
a **sentence boundary** ("… and that was the plan.|" → next sentence) or on a **speaker change**
(the gap where speaker A stops and speaker B starts) is editorially far stronger than an arbitrary
word-gap in the middle of a thought — it falls on a natural narrative seam, exactly where a human
editor would cut.

This module derives those two semantic seams from the transcript words alone, as source-frame
sets that the joint scorer can prefer:

* :func:`sentence_end_frames` — the end frame of any word whose text ends in sentence punctuation
  (``.?!…``), i.e. the frame *after* the last spoken sample of that word (``end_frame``,
  end-exclusive). A long trailing pause before the next word is taken as a fallback clause/sentence
  boundary even when ASR dropped the punctuation.
* :func:`speaker_turn_frames` — the boundary frame between two consecutive words spoken by
  *different* speakers: the gap between the last word of speaker A and the first of speaker B.

Both are **decoupled and side-effect free**, mirroring :func:`laura.analysis.editorial._gap_frames`
and ``_nearest_within``. They degrade gracefully: with no per-word ``text`` there are no sentence
ends, and with no per-word ``speaker`` labels there are no speaker turns — each returns an empty
set, so a transcript that carries neither yields no semantic signal and placement behaves exactly
as it does today.

Frames are **source-frame indices** of the asset, like everywhere else in Laura, and word ranges
are the half-open ``[start_frame, end_frame)`` (end-exclusive) used across the editorial layer.
"""

from __future__ import annotations

from collections.abc import Sequence

from .editorial import Word

# Characters that close a sentence. ``…`` (single-codepoint ellipsis) is included; a run of plain
# dots ("...") ends in "." and is therefore covered by the "." member already.
_SENTENCE_PUNCT = ".?!…"

# Fallback clause/sentence boundary: when ASR drops the punctuation, a long pause before the next
# word still marks a narrative seam. ~0.5s at 30fps — long enough to be a genuine beat between
# sentences, not a within-sentence micro-gap between words.
DEFAULT_CLAUSE_GAP_FRAMES = 15


def _ends_sentence(text: str | None) -> bool:
    """``True`` when ``text`` ends with sentence-closing punctuation (``.?!…``).

    Trailing whitespace is ignored (Whisper emits words like ``" word."`` with leading space and
    occasional trailing space). ``None`` / empty / whitespace-only -> ``False`` (no signal).
    """
    if not text:
        return False
    stripped = text.rstrip()
    if not stripped:
        return False
    return stripped[-1] in _SENTENCE_PUNCT


def sentence_end_frames(
    words: Sequence[Word], *, clause_gap_frames: int = DEFAULT_CLAUSE_GAP_FRAMES
) -> set[int]:
    """Frames that end a sentence: word ``end_frame`` where the word's text closes a sentence.

    For every word whose ``text`` ends in ``.?!…`` the word's ``end_frame`` (end-exclusive — the
    frame just past its last sample) is a sentence-boundary cut point. As a fallback for ASR that
    dropped the punctuation, a word followed by a pause of at least ``clause_gap_frames`` before the
    next word also contributes its ``end_frame`` (a long silence between words is itself a clause /
    sentence seam).

    Returns an empty set when no word carries text (graceful: a transcript with only timings yields
    no sentence signal). Word order is taken as given by ``start_frame`` so a pre-sorted or
    unsorted list both behave; the result is a plain set of source frames.
    """
    if not words:
        return set()
    ordered = sorted(words, key=lambda w: w.start_frame)
    ends: set[int] = set()
    for i, word in enumerate(ordered):
        if _ends_sentence(word.text):
            ends.add(word.end_frame)
            continue
        # Fallback: a long trailing pause before the next word marks a clause/sentence boundary
        # even without punctuation. Only meaningful when there *is* a next word to gap against.
        if clause_gap_frames > 0 and i + 1 < len(ordered):
            nxt = ordered[i + 1]
            if nxt.start_frame - word.end_frame >= clause_gap_frames:
                ends.add(word.end_frame)
    return ends


def speaker_turn_frames(words: Sequence[Word]) -> set[int]:
    """Frames where the speaker changes between two consecutive words (a diarization turn).

    Walking the words in time order, whenever the ``speaker`` label of one word differs from the
    next, the boundary between them is a speaker turn. The returned frame is the *end* of the last
    word of the outgoing speaker (``prev.end_frame``) — the start of the gap before the incoming
    speaker's first word, which is where a cut switches cleanly between voices. (When the words
    abut, ``prev.end_frame == next.start_frame``, so the turn frame is the shared boundary.)

    Only consecutive words that *both* carry a non-``None`` ``speaker`` contribute: a word with an
    unknown speaker neither starts nor ends a turn (we never invent a change against missing data).
    Returns an empty set when no word carries a speaker label (graceful: undiarized transcripts
    yield no speaker signal).
    """
    if not words:
        return set()
    ordered = sorted(words, key=lambda w: w.start_frame)
    turns: set[int] = set()
    for prev, nxt in zip(ordered, ordered[1:], strict=False):
        if prev.speaker is None or nxt.speaker is None:
            continue
        if prev.speaker != nxt.speaker:
            turns.add(prev.end_frame)
    return turns
