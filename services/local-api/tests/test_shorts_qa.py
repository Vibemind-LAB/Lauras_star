"""Unit tests for :mod:`laura.analysis.shorts_qa` — pure QA gate for short candidates.

All tests are deterministic and free of IO/ffmpeg. Words, silence ranges and ShortCandidates
are built in-memory. ``probe_boundaries`` is tested via an injected ``FrameLoader`` (no disk).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import numpy as np

from laura.analysis.editorial import Word
from laura.analysis.shorts_qa import (
    boundary_metrics,
    qa_candidate,
    qa_gate,
)
from laura.analysis.shorts_types import BoundaryMetrics, QAResult, ShortCandidate

# ---- helpers -----------------------------------------------------------------


def _bm(
    frame: int,
    severs_word: bool = False,
    on_black: bool | None = None,
    on_freeze: bool | None = None,
    in_silence: bool | None = None,
) -> BoundaryMetrics:
    return BoundaryMetrics(
        frame=frame,
        severs_word=severs_word,
        on_black=on_black,
        on_freeze=on_freeze,
        in_silence=in_silence,
    )


def _clean_start() -> BoundaryMetrics:
    return _bm(100, severs_word=False, on_black=False, on_freeze=False)


def _clean_end() -> BoundaryMetrics:
    return _bm(200, severs_word=False, on_black=False, on_freeze=False)


def _clean_editorial() -> dict[str, float]:
    return {"pct_mid_word": 0.0, "pct_clean": 1.0, "mean_dist_to_word_gap": 0.0}


# ============================================================
# Test 1 — pure_pass
# ============================================================

def test_pure_pass() -> None:
    """All-clean boundary metrics + pct_mid_word=0.0 -> passed=True, no issues."""
    result = qa_gate(_clean_start(), _clean_end(), _clean_editorial())
    assert result == QAResult(passed=True, issues=[])


# ============================================================
# Test 2 — black_boundary_fails
# ============================================================

def test_black_boundary_fails() -> None:
    """start_metrics.on_black=True -> passed=False, 'start_on_black' in issues."""
    start = _bm(100, severs_word=False, on_black=True, on_freeze=False)
    result = qa_gate(start, _clean_end(), _clean_editorial())
    assert not result.passed
    assert "start_on_black" in result.issues


def test_end_black_boundary_fails() -> None:
    """end_metrics.on_black=True -> passed=False, 'end_on_black' in issues."""
    end = _bm(200, severs_word=False, on_black=True, on_freeze=False)
    result = qa_gate(_clean_start(), end, _clean_editorial())
    assert not result.passed
    assert "end_on_black" in result.issues


# ============================================================
# Test 3 — freeze_boundary_fails
# ============================================================

def test_freeze_boundary_fails() -> None:
    """end_metrics.on_freeze=True -> passed=False, 'end_on_freeze' in issues."""
    end = _bm(200, severs_word=False, on_black=False, on_freeze=True)
    result = qa_gate(_clean_start(), end, _clean_editorial())
    assert not result.passed
    assert "end_on_freeze" in result.issues


def test_start_freeze_boundary_fails() -> None:
    """start_metrics.on_freeze=True -> passed=False, 'start_on_freeze' in issues."""
    start = _bm(100, severs_word=False, on_black=False, on_freeze=True)
    result = qa_gate(start, _clean_end(), _clean_editorial())
    assert not result.passed
    assert "start_on_freeze" in result.issues


# ============================================================
# Test 4 — mid_word_fails
# ============================================================

def test_mid_word_fails() -> None:
    """editorial['pct_mid_word']=0.5 -> passed=False, 'mid_word_cut' in issues."""
    editorial = {"pct_mid_word": 0.5, "pct_clean": 0.5, "mean_dist_to_word_gap": 3.0}
    result = qa_gate(_clean_start(), _clean_end(), editorial)
    assert not result.passed
    assert "mid_word_cut" in result.issues


# ============================================================
# Test 5 — none_metrics_treated_as_not_failing
# ============================================================

def test_none_metrics_treated_as_not_failing() -> None:
    """on_black=None, on_freeze=None, in_silence=None with severs_word=False -> passed=True.

    A pure run where only word-safety is known — missing probes must not penalise.
    """
    start = _bm(100, severs_word=False)  # all optional fields None
    end = _bm(200, severs_word=False)
    result = qa_gate(start, end, _clean_editorial())
    assert result == QAResult(passed=True, issues=[])


# ============================================================
# Test 6 — non_integer_boundary_guard
# ============================================================

def test_negative_frame_triggers_non_integer_boundary() -> None:
    """A BoundaryMetrics.frame that is negative -> 'non_integer_boundary' in issues."""
    start = _bm(-1, severs_word=False, on_black=False, on_freeze=False)
    result = qa_gate(start, _clean_end(), _clean_editorial())
    assert not result.passed
    assert "non_integer_boundary" in result.issues


def test_float_frame_triggers_non_integer_boundary() -> None:
    """A BoundaryMetrics.frame that is a float value masquerading causes the guard to fire.

    We simulate this by constructing BoundaryMetrics directly with frame=3.7
    (overriding the frozen int hint at runtime to test the defensive guard).
    """
    # Bypass the frozen-dataclass type-safety to inject a float to test the runtime guard.
    start = dataclasses.replace(_clean_start(), frame=3.7)  # type: ignore[arg-type]
    result = qa_gate(start, _clean_end(), _clean_editorial())
    assert not result.passed
    assert "non_integer_boundary" in result.issues


# ============================================================
# Test 7 — issue_order_is_deterministic
# ============================================================

def test_issue_order_is_deterministic() -> None:
    """Multiple failing checks -> issues list is in fixed canonical order."""
    # Trigger: start_on_black, end_on_black, start_on_freeze, end_on_freeze,
    #          mid_word_cut (pct_mid_word > 0). non_integer_boundary not triggered here.
    start = _bm(100, severs_word=False, on_black=True, on_freeze=True)
    end = _bm(200, severs_word=False, on_black=True, on_freeze=True)
    editorial = {"pct_mid_word": 1.0, "pct_clean": 0.0, "mean_dist_to_word_gap": 5.0}
    result = qa_gate(start, end, editorial)
    assert not result.passed
    expected_order = [
        "start_on_black",
        "end_on_black",
        "start_on_freeze",
        "end_on_freeze",
        "mid_word_cut",
    ]
    assert result.issues == expected_order


def test_issue_order_includes_non_integer_last() -> None:
    """'non_integer_boundary' appears LAST in the canonical issue order."""
    # Negative frame + on_black to get multiple issues.
    start = dataclasses.replace(
        _bm(100, on_black=True, on_freeze=False, severs_word=False), frame=-5
    )
    result = qa_gate(start, _clean_end(), _clean_editorial())
    assert not result.passed
    # non_integer_boundary must be last when present
    assert result.issues[-1] == "non_integer_boundary"
    # start_on_black comes before it
    assert "start_on_black" in result.issues
    assert result.issues.index("start_on_black") < result.issues.index("non_integer_boundary")


# ============================================================
# Test 8 — boundary_metrics_builder_severs_word
# ============================================================

def test_boundary_metrics_builder_severs_word_true() -> None:
    """boundary_metrics(frame inside word) -> severs_word=True via _covering_word."""
    words = [Word(start_frame=10, end_frame=30)]
    bm = boundary_metrics(20, words)  # 10 < 20 < 30 -> strictly inside -> severs
    assert bm.frame == 20
    assert bm.severs_word is True


def test_boundary_metrics_builder_severs_word_false_on_edge() -> None:
    """boundary_metrics(frame on word end edge) -> severs_word=False (edge is safe)."""
    words = [Word(start_frame=10, end_frame=30)]
    bm = boundary_metrics(30, words)  # frame == end_frame -> NOT strictly inside
    assert bm.frame == 30
    assert bm.severs_word is False


def test_boundary_metrics_builder_severs_word_false_on_start_edge() -> None:
    """boundary_metrics(frame on word start edge) -> severs_word=False."""
    words = [Word(start_frame=10, end_frame=30)]
    bm = boundary_metrics(10, words)  # frame == start_frame -> NOT strictly inside
    assert bm.severs_word is False


# ============================================================
# Test 9 — boundary_metrics_in_silence
# ============================================================

def test_boundary_metrics_in_silence_true() -> None:
    """boundary_metrics(frame inside silence interval) -> in_silence=True."""
    bm = boundary_metrics(50, [], silence=[(40, 60)])  # 40 <= 50 < 60
    assert bm.in_silence is True


def test_boundary_metrics_in_silence_false_outside() -> None:
    """boundary_metrics(frame outside silence interval) -> in_silence=False."""
    bm = boundary_metrics(65, [], silence=[(40, 60)])  # 65 >= 60
    assert bm.in_silence is False


def test_boundary_metrics_in_silence_none_when_no_silence() -> None:
    """boundary_metrics without silence arg -> in_silence=None (not probed)."""
    bm = boundary_metrics(50, [])
    assert bm.in_silence is None


def test_boundary_metrics_on_black_none_by_default() -> None:
    """on_black and on_freeze remain None unless caller fills them."""
    bm = boundary_metrics(50, [])
    assert bm.on_black is None
    assert bm.on_freeze is None


def test_boundary_metrics_caller_fills_on_black() -> None:
    """Caller can inject on_black/on_freeze when they have visual probe data."""
    bm = boundary_metrics(50, [], on_black=True, on_freeze=False)
    assert bm.on_black is True
    assert bm.on_freeze is False


# ============================================================
# Test 10 — qa_candidate_uses_editorial_metrics
# ============================================================

def test_qa_candidate_safe_yields_pass() -> None:
    """A safe ShortCandidate over clean words -> passed=True, pct_mid_word=0."""
    # Words: [0, 50) then [50, 100); candidate [0, 100) uses word boundaries as cuts.
    words = [Word(start_frame=0, end_frame=50), Word(start_frame=50, end_frame=100)]
    cand = ShortCandidate(
        start_frame=0,
        end_frame_exclusive=100,
        start_boundary="sentence_end",
        end_boundary="sentence_end",
    )
    result = qa_candidate(cand, words)
    assert result.passed
    assert result.issues == []


# ============================================================
# Test 11 — qa_candidate_rejects_severing_cut
# ============================================================

def test_qa_candidate_rejects_severing_cut() -> None:
    """A candidate whose end_frame_exclusive bisects a word -> passed=False, 'mid_word_cut'."""
    # Word spans [50, 150). end_frame_exclusive=100 is strictly inside the word.
    words = [Word(start_frame=50, end_frame=150)]
    cand = ShortCandidate(
        start_frame=0,          # safe (before word)
        end_frame_exclusive=100, # 50 < 100 < 150 -> bisects word
        start_boundary="sentence_end",
        end_boundary="sentence_end",
    )
    result = qa_candidate(cand, words)
    assert not result.passed
    assert "mid_word_cut" in result.issues


# ============================================================
# Test 12 — probe_is_pure_testable
# ============================================================

def _make_black_frame() -> np.ndarray:
    """An all-zero (uniformly black) grayscale frame."""
    return np.zeros((36, 64), dtype=np.uint8)


def _make_gray_frame(luma: int = 128) -> np.ndarray:
    """A uniform mid-gray frame (non-black, non-freeze-triggering by content)."""
    return np.full((36, 64), luma, dtype=np.uint8)


def test_probe_all_black_returns_on_black_true() -> None:
    """probe_boundaries with all-black FrameLoader -> on_black=True for that frame."""
    from laura.analysis.shorts_qa import probe_boundaries

    def all_black_loader(video: Path | str, lo: int, hi: int) -> list[np.ndarray]:
        return [_make_black_frame() for _ in range(hi - lo)]

    results = probe_boundaries("fake.mp4", [10], total_frames=100, frame_loader=all_black_loader)
    on_black, _ = results[10]
    assert on_black is True


def test_probe_static_run_returns_on_freeze_true() -> None:
    """probe_boundaries with a zero-diff (static) run -> on_freeze=True."""
    from laura.analysis.shorts_qa import probe_boundaries

    def static_loader(video: Path | str, lo: int, hi: int) -> list[np.ndarray]:
        # All identical frames -> diff signal is everywhere 0 -> freeze
        return [_make_gray_frame(100) for _ in range(hi - lo)]

    results = probe_boundaries("fake.mp4", [10], total_frames=100, frame_loader=static_loader)
    _, on_freeze = results[10]
    assert on_freeze is True


def test_probe_non_black_non_freeze() -> None:
    """probe_boundaries with changing non-black frames -> on_black=False, on_freeze=False."""
    from laura.analysis.shorts_qa import probe_boundaries

    call_count = [0]

    def varying_loader(video: Path | str, lo: int, hi: int) -> list[np.ndarray]:
        count = hi - lo
        frames = []
        for i in range(count):
            # Alternate between 100 and 200 luma to produce diff > FREEZE_DIFF_MAX
            luma = 100 if (i + lo) % 2 == 0 else 200
            frames.append(_make_gray_frame(luma))
        call_count[0] += 1
        return frames

    results = probe_boundaries("fake.mp4", [10], total_frames=100, frame_loader=varying_loader)
    on_black, on_freeze = results[10]
    assert on_black is False
    assert on_freeze is False


# ============================================================
# Test 13 — probe_io_error_degrades
# ============================================================

def test_probe_io_error_degrades_gracefully() -> None:
    """A FrameLoader that raises -> probe_boundaries returns (None, None), never raises."""
    from laura.analysis.shorts_qa import probe_boundaries

    def exploding_loader(video: Path | str, lo: int, hi: int) -> list[np.ndarray]:
        raise OSError("no disk")

    results = probe_boundaries("fake.mp4", [5, 10, 15], frame_loader=exploding_loader)
    for frame in [5, 10, 15]:
        assert results[frame] == (None, None)


def test_probe_empty_frames_degrades_gracefully() -> None:
    """A FrameLoader returning too few frames -> (None, None) for the frame, no raise."""
    from laura.analysis.shorts_qa import probe_boundaries

    def empty_loader(video: Path | str, lo: int, hi: int) -> list[np.ndarray]:
        return []  # not enough to compute diff

    results = probe_boundaries("fake.mp4", [10], total_frames=100, frame_loader=empty_loader)
    assert results[10] == (None, None)


# ============================================================
# Test 14 — core_gate_has_no_ffmpeg_import
# ============================================================

def test_core_gate_importable_without_subprocess_in_namespace() -> None:
    """Importing the pure QA gate must NOT pull subprocess/numpy/eval_cut into sys.modules.

    The module's central invariant: the pure path (``qa_gate`` / ``boundary_metrics`` /
    ``qa_candidate``) is importable in ffmpeg-free environments. The heavy imports live
    LOCALLY inside ``probe_boundaries`` only. We assert this structurally by importing the
    module in a FRESH interpreter and checking that the forbidden modules are absent from
    ``sys.modules`` — a module-level (even transitive) import of any of them fails this test.
    """
    import subprocess  # noqa: PLC0415
    import sys  # noqa: PLC0415

    probe = (
        "import sys; import laura.analysis.shorts_qa; "
        "assert 'subprocess' not in sys.modules, 'subprocess leaked'; "
        "assert 'numpy' not in sys.modules, 'numpy leaked'; "
        "assert 'laura.analysis.eval_cut' not in sys.modules, 'eval_cut leaked'"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"pure import is not clean (stdout={proc.stdout!r} stderr={proc.stderr!r})"
    )
