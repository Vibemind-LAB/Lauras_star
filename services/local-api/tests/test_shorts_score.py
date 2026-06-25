"""Unit tests for :mod:`laura.analysis.shorts_score`.

Pure and deterministic — no ffmpeg, no network, no DB.  Words, ShotResults, and silence ranges
are constructed in-memory exactly like test_joint.py / test_editorial.py.

Each test maps to the 14 concrete cases specified in the module contract.
"""

from __future__ import annotations

import math

import pytest

from laura.analysis.editorial import Word
from laura.analysis.shorts_score import (
    DEFAULT_WEIGHTS,
    IDEAL_DURATION_S,
    Z_CLAMP,
    robust_z,
    score_candidate_features,
    score_candidates,
)
from laura.analysis.shorts_types import ShortCandidate
from laura.analysis.types import ShotResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RATE_NUM = 25
RATE_DEN = 1  # 25 fps for simple mental arithmetic


def _fps() -> int:
    return RATE_NUM // RATE_DEN


def _candidate(
    start: int,
    end: int,
    *,
    start_kind: str = "sentence_end",
    end_kind: str = "sentence_end",
) -> ShortCandidate:
    return ShortCandidate(
        start_frame=start,
        end_frame_exclusive=end,
        start_boundary=start_kind,
        end_boundary=end_kind,
    )


def _words_dense(start: int, end: int, n: int = 10) -> list[Word]:
    """Create ``n`` evenly-spaced words spanning [start, end)."""
    total = end - start
    per_word = total // n
    words: list[Word] = []
    for i in range(n):
        ws = start + i * per_word
        we = ws + per_word
        words.append(Word(start_frame=ws, end_frame=we))
    return words


def _words_sparse(start: int, end: int, n: int = 2) -> list[Word]:
    """Create ``n`` words concentrated at the very start, rest is silence."""
    words: list[Word] = []
    per_word = 5  # 5-frame words
    for i in range(n):
        ws = start + i * per_word
        we = ws + per_word
        if we <= end:
            words.append(Word(start_frame=ws, end_frame=we))
    return words


# ---------------------------------------------------------------------------
# Test 1 — word_interruption_is_hard_reject
# ---------------------------------------------------------------------------


def test_word_interruption_is_hard_reject() -> None:
    """A candidate whose start_frame strictly bisects a word is hard-rejected."""
    # Word [10, 50); start_frame=30 sits INSIDE it (10 < 30 < 50) -> mid-word
    words = [Word(start_frame=10, end_frame=50), Word(start_frame=50, end_frame=100)]
    # Construct a candidate whose start bisects the word
    cand = _candidate(30, 750)  # start=30 bisects word [10,50)

    scores = score_candidates(
        [cand],
        words,
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
    )
    assert len(scores) == 1
    s = scores[0]
    assert s.rejected is True
    assert s.reject_reason == "word_interruption"
    assert math.isinf(s.total) and s.total < 0

    # Rejected candidate sorts last: a clean candidate must outrank it
    clean_cand = _candidate(0, 750)  # 0 is a safe boundary (before first word)
    scores2 = score_candidates(
        [cand, clean_cand],
        words,
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
    )
    rejected = next(sc for sc in scores2 if sc.rejected)
    clean = next(sc for sc in scores2 if not sc.rejected)
    assert clean.total > rejected.total


# ---------------------------------------------------------------------------
# Test 2 — clean_candidate_not_rejected
# ---------------------------------------------------------------------------


def test_clean_candidate_not_rejected() -> None:
    """A candidate with both cuts on word edges is not rejected and has a finite total."""
    # Words: [10, 50) and [50, 100); cuts at 10 (start of first word = safe) and 100 (end of last)
    words = [Word(start_frame=10, end_frame=50), Word(start_frame=50, end_frame=100)]
    cand = _candidate(10, 100)  # both cuts on word edges: 10=start, 100=end_frame of last word

    scores = score_candidates(
        [cand],
        words,
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
    )
    assert len(scores) == 1
    s = scores[0]
    assert s.rejected is False
    assert math.isfinite(s.total)


# ---------------------------------------------------------------------------
# Test 3 — transcript_safety_highest_weight
# ---------------------------------------------------------------------------


def test_transcript_safety_highest_weight() -> None:
    """Candidate on speaker_turns outscores one on bare sentence_ends (all else equal).

    We need two candidates with different cut frames:
    - cand_speaker: cuts at speaker_turn frames (100, 200) -> _editorial_score 1.0 each
    - cand_sentence: cuts at sentence_end-only frames (50, 150) -> _editorial_score 0.95 each

    Both are editorially clean (word edges), so the only difference is the tier.
    """
    # Words: gaps at frame edges 0,50,100,150,200
    words = [
        Word(start_frame=0, end_frame=50, text="hello.", speaker="A"),
        Word(start_frame=50, end_frame=100, text="world.", speaker="A"),
        Word(start_frame=100, end_frame=150, text="yes.", speaker="B"),
        Word(start_frame=150, end_frame=200, text="indeed.", speaker="B"),
    ]
    # Speaker turn at frame 100 (A->B boundary)
    speaker_turns: set[int] = {100}
    # Sentence ends at all word end_frames; 50,150 are sentence_end only (not speaker_turns)
    sentence_ends: set[int] = {50, 100, 150, 200}

    # Candidate 1: cuts at 100 (speaker_turn) and 200 (also speaker_turn+sentence_end)
    # Both frames are speaker_turns -> _editorial_score 1.0
    cand_speaker = _candidate(100, 200, start_kind="speaker_turn", end_kind="speaker_turn")
    # Candidate 2: cuts at 50 (sentence_end only) and 150 (sentence_end only)
    # Both frames are sentence_end only -> _editorial_score 0.95
    cand_sentence = _candidate(50, 150, start_kind="sentence_end", end_kind="sentence_end")

    scores = score_candidates(
        [cand_speaker, cand_sentence],
        words,
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
        sentence_frames=sentence_ends,
        speaker_frames=speaker_turns,
    )
    # speaker_turn candidate should score higher on transcript_safety in raw components
    score_speaker = scores[0]
    score_sentence = scores[1]
    # Compare raw components (pre-z) to see the tier difference
    ts_speaker = score_speaker.components["transcript_safety"]
    ts_sentence = score_sentence.components["transcript_safety"]
    assert ts_speaker > ts_sentence


# ---------------------------------------------------------------------------
# Test 4 — audio_silence_boost
# ---------------------------------------------------------------------------


def test_audio_silence_boost() -> None:
    """A candidate whose cuts fall in silence scores higher on audio_silence_at_boundaries."""
    words = [Word(start_frame=10, end_frame=50), Word(start_frame=60, end_frame=100)]
    # Cuts at 0 and 110 — both outside word spans, clean
    # Provide silence covering the cut frames
    silence = [(0, 5), (108, 115)]  # both cuts are inside silence
    cand = _candidate(0, 110)

    scores_with_silence = score_candidates(
        [cand],
        words,
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
        silence=silence,
    )
    scores_without_silence = score_candidates(
        [cand],
        words,
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
        silence=None,
    )

    # With silence, audio_silence_at_boundaries should be 1.0; without None/0.0
    with_s = scores_with_silence[0]
    without_s = scores_without_silence[0]
    asb_with = with_s.components["audio_silence_at_boundaries"]
    asb_without = without_s.components["audio_silence_at_boundaries"]
    assert asb_with > asb_without
    # Both are not rejected
    assert not with_s.rejected
    assert not without_s.rejected


# ---------------------------------------------------------------------------
# Test 5 — visual_boundary_graceful_without_shots
# ---------------------------------------------------------------------------


def test_visual_boundary_graceful_without_shots() -> None:
    """shots=None yields visual_boundary=0.0; a shot edge exactly on a cut is maximal."""
    words = [Word(start_frame=10, end_frame=50), Word(start_frame=60, end_frame=100)]
    cand = _candidate(0, 110)

    # Without shots: visual_boundary should be 0.0 (neutral)
    scores_no_shots = score_candidates(
        [cand],
        words,
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
        shots=None,
    )
    assert scores_no_shots[0].components["visual_boundary"] == pytest.approx(0.0)

    # With a shot boundary exactly at start_frame=0 and end_frame=110
    shots = [ShotResult(src_in_frame=0, src_out_frame_exclusive=110, method="transnet")]
    scores_with_shots = score_candidates(
        [cand],
        words,
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
        shots=shots,
    )
    # Shot edge at 0 (src_in_frame) and 110 (src_out_frame_exclusive) — both exactly on cuts
    assert scores_with_shots[0].components["visual_boundary"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Test 6 — length_fit_peaks_at_ideal
# ---------------------------------------------------------------------------


def test_length_fit_peaks_at_ideal() -> None:
    """~30s candidate has higher length_fit than 16s or 58s; all values in [0,1]."""
    fps = _fps()
    ideal_frames = round(IDEAL_DURATION_S * fps)  # 750 frames at 25fps
    short_frames = round(16.0 * fps)   # 400 frames
    long_frames = round(58.0 * fps)    # 1450 frames

    words: list[Word] = []

    def _cand_for_dur(dur: int) -> ShortCandidate:
        return _candidate(0, dur)

    cand_ideal = _cand_for_dur(ideal_frames)
    cand_short = _cand_for_dur(short_frames)
    cand_long = _cand_for_dur(long_frames)

    all_cands = [cand_ideal, cand_short, cand_long]
    scores = score_candidates(
        all_cands,
        words,
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
    )

    lf_ideal = scores[0].components["length_fit"]
    lf_short = scores[1].components["length_fit"]
    lf_long = scores[2].components["length_fit"]

    assert lf_ideal > lf_short
    assert lf_ideal > lf_long
    for lf in (lf_ideal, lf_short, lf_long):
        assert 0.0 <= lf <= 1.0


# ---------------------------------------------------------------------------
# Test 7 — speech_density_from_words
# ---------------------------------------------------------------------------


def test_speech_density_from_words() -> None:
    """Dense-word window scores higher speech_density than a mostly-silent window."""
    start = 0
    end = 750  # 30s at 25fps

    cand = _candidate(start, end)
    dense_words = _words_dense(start, end, n=20)
    sparse_words = _words_sparse(start, end, n=2)

    scores_dense = score_candidates(
        [cand],
        dense_words,
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
    )
    scores_sparse = score_candidates(
        [cand],
        sparse_words,
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
    )

    sd_dense = scores_dense[0].components["speech_density"]
    sd_sparse = scores_sparse[0].components["speech_density"]
    assert sd_dense > sd_sparse


# ---------------------------------------------------------------------------
# Test 8 — robust_z_handles_zero_mad
# ---------------------------------------------------------------------------


def test_robust_z_handles_zero_mad() -> None:
    """robust_z([5,5,5]) == [0,0,0]; empty -> []; singleton -> [0.0]."""
    result = robust_z([5.0, 5.0, 5.0])
    assert result == [0.0, 0.0, 0.0]

    assert robust_z([]) == []
    assert robust_z([3.0]) == [0.0]


# ---------------------------------------------------------------------------
# Test 9 — robust_z_centers_on_median
# ---------------------------------------------------------------------------


def test_robust_z_centers_on_median() -> None:
    """The median element maps near 0; an extreme outlier is clamped to Z_CLAMP.

    Before winsorisation [1,2,3,4,100] would produce z≈32 for the outlier (MAD=1, so
    scale≈1.48, z(100)≈(100-3)/1.48≈65).  After clamping the outlier lands exactly at
    Z_CLAMP and the median is near 0, while the lowest value is negative but also bounded.
    """
    values = [1.0, 2.0, 3.0, 4.0, 100.0]
    result = robust_z(values)
    # median is 3.0 -> result[2] should be near 0
    assert abs(result[2]) < 0.5
    # outlier (100.0) -> clamped to the upper winsorisation limit
    assert result[4] == pytest.approx(Z_CLAMP)
    # lowest value (1.0) -> negative z (but within bounds)
    assert result[0] < 0.0
    assert result[0] >= -Z_CLAMP


# ---------------------------------------------------------------------------
# Test 9b — robust_z_sparse_spike_clamped
# ---------------------------------------------------------------------------


def test_robust_z_sparse_spike_clamped() -> None:
    """A sparse binary feature (all-zero + one spike) is winsorised to Z_CLAMP.

    This is the real-world scenario that triggered the live-test bug: ``visual_boundary``
    was mostly 0.0 with rare non-zero values → MAD ≈ 0 → z-scores in the tens of
    thousands → one feature dominated the entire multimodal blend.

    Contract:
    - The spike's z is clamped to exactly Z_CLAMP (not tens of thousands).
    - The zero cluster's z is ≥ -Z_CLAMP (also bounded).
    - A normal-spread column (all values well within ±4 standard units) is not affected
      by the clamp — its values remain unchanged (max |z| well within Z_CLAMP).
    """
    # Model visual_boundary in the live-test failure: most candidates are far from a shot
    # edge → small non-zero values, a few closer candidates are slightly higher, and one
    # cut lands right on a boundary → 1.0.  The tiny spread produces a very small MAD,
    # so the raw z for the spike would be in the tens of thousands without the clamp.
    # Use small distinct near-zero values so MAD is non-zero.
    sparse = [0.001 * i for i in range(20)] + [1.0]
    result_sparse = robust_z(sparse)

    # The spike is at index 20 — without the clamp it would be ~thousands; clamped to Z_CLAMP.
    assert result_sparse[20] == pytest.approx(Z_CLAMP), (
        f"sparse spike should be clamped to Z_CLAMP={Z_CLAMP}, got {result_sparse[20]}"
    )
    # All values must be within bounds.
    for z in result_sparse:
        assert -Z_CLAMP <= z <= Z_CLAMP

    # Normal-spread column: values uniformly spread → max |z| well inside ±4.
    normal = [float(i) for i in range(10)]  # 0..9, median=4.5, MAD≈2.5
    result_normal = robust_z(normal)
    max_z = max(abs(z) for z in result_normal)
    # For a uniform 0..9 column, robust-z extremes are ≈ ±3.0 — well within Z_CLAMP.
    assert max_z < Z_CLAMP, (
        f"normal-spread column should not hit the clamp; max |z|={max_z}"
    )


# ---------------------------------------------------------------------------
# Test 10 — breakdown_is_explainable_and_complete
# ---------------------------------------------------------------------------


def test_breakdown_is_explainable_and_complete() -> None:
    """Every non-rejected ShortScore.breakdown contains all component+penalty keys."""
    words = [Word(start_frame=10, end_frame=50), Word(start_frame=60, end_frame=100)]
    cand = _candidate(0, 750)

    scores = score_candidates(
        [cand],
        words,
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
    )
    s = scores[0]
    assert not s.rejected

    expected_keys = {
        "transcript_safety",
        "audio_silence_at_boundaries",
        "visual_boundary",
        "semantic",
        "hook_position",
        "length_fit",
        "speech_density",
        "visual_shift",
        "visual_continuity",
        "word_interruption",
        "audio_jump",
        "face_motion",
        "duplicate_penalty",
    }
    assert set(s.breakdown.keys()) == expected_keys
    assert set(s.components.keys()) == expected_keys


# ---------------------------------------------------------------------------
# Test 11 — soft_penalties_subtract
# ---------------------------------------------------------------------------


def test_soft_penalties_subtract() -> None:
    """Non-zero audio_jump lowers total vs audio_jump=0 but does NOT reject.

    Use two candidates so that robust-z produces non-zero z-scores — one with
    audio_jump=0 (index 0) and one with audio_jump=1 (index 1).  The candidate
    with no penalty should outscore the one with a penalty.
    """
    words = [Word(start_frame=10, end_frame=50), Word(start_frame=60, end_frame=100)]
    cand_clean = _candidate(0, 750)
    cand_penalized = _candidate(0, 750)

    scores = score_candidates(
        [cand_clean, cand_penalized],
        words,
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
        audio_jumps={0: 0.0, 1: 1.0},  # penalized candidate has audio_jump=1
    )

    s_clean = scores[0]
    s_penalized = scores[1]

    assert not s_clean.rejected
    assert not s_penalized.rejected
    # The penalized candidate has a higher raw audio_jump -> larger z -> more subtraction
    assert s_clean.total > s_penalized.total


# ---------------------------------------------------------------------------
# Test 12 — order_preserved
# ---------------------------------------------------------------------------


def test_order_preserved() -> None:
    """score_candidates returns one ShortScore per input candidate in the same order.

    Each candidate has a distinct start_frame; we assert that the i-th score corresponds
    to the i-th candidate by cross-checking speech_density (no words → 0.0 for all) and
    length_fit, which is uniquely determined by each candidate's duration.
    """
    words: list[Word] = []
    cands = [
        _candidate(0, 750),    # duration 750 frames — closest to ideal (750 at 25fps)
        _candidate(0, 400),    # duration 400 frames — short (16s)
        _candidate(0, 1450),   # duration 1450 frames — long (58s)
    ]
    scores = score_candidates(
        cands,
        words,
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
    )
    assert len(scores) == 3

    # Per-index correspondence: each score's length_fit component must match its candidate.
    # We score each candidate individually to get the expected raw length_fit value.
    for i, cand in enumerate(cands):
        solo = score_candidates([cand], words, rate_num=RATE_NUM, rate_den=RATE_DEN)
        expected_lf = solo[0].components["length_fit"]
        actual_lf = scores[i].components["length_fit"]
        assert actual_lf == pytest.approx(expected_lf), (
            f"Index {i}: expected length_fit {expected_lf}, got {actual_lf}"
        )


# ---------------------------------------------------------------------------
# Test 13 — empty_input_returns_empty
# ---------------------------------------------------------------------------


def test_empty_input_returns_empty() -> None:
    """score_candidates([], ...) == []."""
    result = score_candidates(
        [],
        [],
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
    )
    assert result == []


# ---------------------------------------------------------------------------
# Test 14 — single_candidate_neutral_z
# ---------------------------------------------------------------------------


def test_single_candidate_neutral_z() -> None:
    """With one candidate every robust-z family is 0.0 so total is finite and deterministic."""
    words = [Word(start_frame=10, end_frame=50), Word(start_frame=60, end_frame=100)]
    cand = _candidate(0, 750)

    scores = score_candidates(
        [cand],
        words,
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
    )
    assert len(scores) == 1
    s = scores[0]
    assert not s.rejected
    assert math.isfinite(s.total)
    # With a single candidate all z-scores are 0.0, so the weighted total is 0.0
    assert s.total == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Additional edge-case tests for score_candidate_features
# ---------------------------------------------------------------------------


def test_score_candidate_features_returns_correct_shape() -> None:
    """score_candidate_features returns (dict, rejected, reason) with all keys."""
    words = [Word(start_frame=10, end_frame=50), Word(start_frame=60, end_frame=100)]
    cand = _candidate(0, 750)

    raw, rejected, reason = score_candidate_features(
        cand,
        words,
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
    )
    assert isinstance(raw, dict)
    assert isinstance(rejected, bool)
    assert not rejected
    assert reason is None
    expected_keys = {
        "transcript_safety",
        "audio_silence_at_boundaries",
        "visual_boundary",
        "semantic",
        "hook_position",
        "length_fit",
        "speech_density",
        "visual_shift",
        "visual_continuity",
        "word_interruption",
        "audio_jump",
        "face_motion",
        "duplicate_penalty",
    }
    assert set(raw.keys()) == expected_keys


def test_score_candidate_features_mid_word_rejected() -> None:
    """score_candidate_features correctly identifies a mid-word start."""
    words = [Word(start_frame=10, end_frame=50)]
    cand = _candidate(30, 750)  # 30 is strictly inside [10, 50)

    raw, rejected, reason = score_candidate_features(
        cand,
        words,
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
    )
    assert rejected is True
    assert reason == "word_interruption"


def test_score_weights_default() -> None:
    """DEFAULT_WEIGHTS has transcript_safety as highest weight."""
    w = DEFAULT_WEIGHTS
    assert w.transcript_safety >= w.audio_silence_at_boundaries
    assert w.transcript_safety >= w.visual_boundary
    assert w.transcript_safety >= w.semantic
    assert w.transcript_safety >= w.hook_position
    assert w.transcript_safety >= w.length_fit
    assert w.transcript_safety >= w.speech_density
    assert w.transcript_safety >= w.audio_jump
    assert w.transcript_safety >= w.face_motion


def test_hook_position_speaker_turn_start_beats_sentence_end() -> None:
    """hook_position rewards a speaker_turn start over a sentence_end start."""
    words = [Word(start_frame=0, end_frame=50), Word(start_frame=50, end_frame=100)]

    cand_speaker_start = _candidate(0, 750, start_kind="speaker_turn", end_kind="sentence_end")
    cand_sentence_start = _candidate(0, 750, start_kind="sentence_end", end_kind="sentence_end")

    scores = score_candidates(
        [cand_speaker_start, cand_sentence_start],
        words,
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
    )
    hook_speaker = scores[0].components["hook_position"]
    hook_sentence = scores[1].components["hook_position"]
    assert hook_speaker > hook_sentence


# ---------------------------------------------------------------------------
# Test NEW-1 — rate_num=0 does not raise ZeroDivisionError
# ---------------------------------------------------------------------------


def test_length_fit_rate_num_zero_no_crash() -> None:
    """A candidate scored with rate_num=0 must not raise and must yield a finite score."""
    words = [Word(start_frame=0, end_frame=50), Word(start_frame=60, end_frame=100)]
    cand = _candidate(0, 750)

    # Should not raise ZeroDivisionError
    scores = score_candidates(
        [cand],
        words,
        rate_num=0,   # degenerate clock
        rate_den=1,
    )
    assert len(scores) == 1
    s = scores[0]
    # Must not be rejected on technical grounds, and total must be finite
    assert math.isfinite(s.total)
    # length_fit must be in [0, 1] — no inf/nan
    lf = s.components["length_fit"]
    assert math.isfinite(lf)
    assert 0.0 <= lf <= 1.0


# ---------------------------------------------------------------------------
# Test NEW-2 — mislabeled hook does not receive full hook credit
# ---------------------------------------------------------------------------


def test_hook_position_mislabeled_speaker_turn_no_full_credit() -> None:
    """A candidate labeled 'speaker_turn' whose start_frame ∉ speaker_frames must NOT
    receive the full speaker_turn hook score when speaker_frames is provided.

    This guards against the pre-fix behaviour where hook_position trusted the label
    string rather than verifying actual frame membership.

    Word layout (all edges are safe cut points):
      [0,  50)  speaker A  — edge at 0 and 50
      [50, 100) speaker B  — edge at 50 and 100
      speaker turn at frame 50 (A→B boundary, word edge so not mid-word)
    """
    # Words with clean edges at 0, 50, 100, 750, 800
    words = [
        Word(start_frame=0, end_frame=50),
        Word(start_frame=50, end_frame=100),
        Word(start_frame=100, end_frame=750),
        Word(start_frame=750, end_frame=800),
    ]

    # frame 50 IS a speaker turn (A→B boundary); frame 0 is NOT
    speaker_frames: set[int] = {50}
    sentence_frames: set[int] = {50, 100}

    # Mislabeled: label says 'speaker_turn' but start_frame=0 ∉ speaker_frames.
    # start_frame=0 is a safe cut (word edge) so not rejected as mid-word.
    cand_mislabeled = _candidate(0, 800, start_kind="speaker_turn")
    # Correctly labeled: start_frame=50 ∈ speaker_frames and is a word edge.
    cand_correct = _candidate(50, 800, start_kind="speaker_turn")

    scores = score_candidates(
        [cand_mislabeled, cand_correct],
        words,
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
        speaker_frames=speaker_frames,
        sentence_frames=sentence_frames,
    )

    # Neither candidate should be hard-rejected (both cuts are on word edges)
    assert not scores[0].rejected, "mislabeled candidate was unexpectedly hard-rejected"
    assert not scores[1].rejected, "correct candidate was unexpectedly hard-rejected"

    hook_mislabeled = scores[0].components["hook_position"]
    hook_correct = scores[1].components["hook_position"]

    from laura.analysis.joint import _SCORE_SPEAKER_TURN

    # The correctly labeled+membership candidate gets full speaker turn score.
    assert hook_correct == pytest.approx(_SCORE_SPEAKER_TURN)
    # The mislabeled candidate must NOT get the full speaker turn score.
    assert hook_mislabeled < _SCORE_SPEAKER_TURN
