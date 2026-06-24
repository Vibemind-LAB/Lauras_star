"""QA gate for auto-shorts candidates — pure core + optional ffmpeg probe.

The pure gate :func:`qa_gate` checks a scored candidate against hard editorial constraints:

* No boundary lands on a uniformly-dark (black) frame.
* No boundary lands on a near-static (frozen) run.
* Neither cut frame bisects a spoken word (``pct_mid_word == 0``).
* Both cut frames are exact non-negative integers (invariant guard).

All checks consume data the caller already computed — no ffmpeg, no subprocess, no model.
``None`` values for ``on_black`` / ``on_freeze`` / ``in_silence`` mean "not probed" and are
treated as *not failing* so the gate degrades gracefully when visual probes are absent.

The OPTIONAL helper :func:`probe_boundaries` fills ``on_black`` / ``on_freeze`` by decoding a
few frames around each cut via an injected :data:`~laura.analysis.eval_cut.FrameLoader`.  It
degrades to ``(None, None)`` on any IO / decode error and **never raises**, so its absence
only weakens the gate — it can never break it.

.. note::
    The pure path (``qa_gate``, ``boundary_metrics``, ``qa_candidate``) imports nothing from
    ``subprocess`` or ``ffmpeg``.  The heavy imports are **local** to :func:`probe_boundaries`
    only, so the module is cleanly importable in ffmpeg-free environments.

Invariants honoured throughout (same as the rest of Laura's editorial layer):

* Integer source frames everywhere — never float seconds as state.
* Ranges are end-exclusive.
* Audio in samples; frames are a projection (not relevant here, but noted for consistency).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .editorial import Word, _covering_word, editorial_metrics
from .shorts_types import BoundaryMetrics, QAResult, ShortCandidate

if TYPE_CHECKING:
    from .eval_cut import FrameLoader

__all__ = [
    "FREEZE_DIFF_MAX",
    "qa_gate",
    "boundary_metrics",
    "qa_candidate",
    "probe_boundaries",
]

_log = logging.getLogger(__name__)

# Maximum per-frame luma-diff that counts as "frozen". A value ≤ this means
# there is essentially no change between consecutive frames → freeze / static run.
FREEZE_DIFF_MAX: float = 1.0

# Canonical issue-code order (deterministic for golden tests).
_ISSUE_ORDER: list[str] = [
    "start_on_black",
    "end_on_black",
    "start_on_freeze",
    "end_on_freeze",
    "mid_word_cut",
    "non_integer_boundary",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_valid_frame(frame: object) -> bool:
    """``True`` iff ``frame`` is a non-negative integer (int, not float/bool)."""
    # bool is a subclass of int in Python — we want *real* ints only.
    return isinstance(frame, int) and not isinstance(frame, bool) and frame >= 0


def _in_silence(frame: int, silence: list[tuple[int, int]] | None) -> bool:
    """``True`` when ``frame`` lies inside a half-open silence interval ``[start, end)``.

    ``silence`` ranges are end-exclusive source-frame spans (see
    :func:`laura.analysis.silence.detect_silence`). ``None``/empty -> no silence info -> ``False``.

    Inlined (rather than imported from :mod:`laura.analysis.joint`) so the pure QA gate
    stays free of any transitive ffmpeg/subprocess/numpy import — see the module docstring.
    """
    if not silence:
        return False
    return any(start <= frame < end for start, end in silence)


# ---------------------------------------------------------------------------
# Pure public API
# ---------------------------------------------------------------------------


def qa_gate(
    start_metrics: BoundaryMetrics,
    end_metrics: BoundaryMetrics,
    editorial: dict[str, float],
) -> QAResult:
    """Pure QA gate — no ffmpeg, no subprocess.

    Parameters
    ----------
    start_metrics:
        Per-boundary metrics for the candidate's *start* cut frame.
    end_metrics:
        Per-boundary metrics for the candidate's *end* cut frame
        (``end_frame_exclusive``).
    editorial:
        Dict from :func:`laura.analysis.editorial.editorial_metrics` over the two cut
        frames ``[start_frame, end_frame_exclusive]``.  Must contain ``pct_mid_word``.

    Returns
    -------
    QAResult
        ``passed=True`` iff every hard check holds; ``issues`` lists stable codes in
        deterministic order for each failing check.
    """
    issues_set: set[str] = set()

    # --- Visual checks (None == not probed -> not failing) -----------------
    if start_metrics.on_black is True:
        issues_set.add("start_on_black")
    if end_metrics.on_black is True:
        issues_set.add("end_on_black")
    if start_metrics.on_freeze is True:
        issues_set.add("start_on_freeze")
    if end_metrics.on_freeze is True:
        issues_set.add("end_on_freeze")

    # --- Transcript safety check (from editorial_metrics) ------------------
    # Subscript (not .get): a missing ``pct_mid_word`` is a programming error on the most
    # safety-critical transcript invariant — fail LOUD (KeyError) rather than silently pass.
    pct_mid: float = editorial["pct_mid_word"]
    if pct_mid > 0.0:
        issues_set.add("mid_word_cut")

    # --- Invariant guard: both frame values must be non-negative ints -------
    if not _is_valid_frame(start_metrics.frame) or not _is_valid_frame(end_metrics.frame):
        issues_set.add("non_integer_boundary")

    # Build in canonical deterministic order
    issues = [code for code in _ISSUE_ORDER if code in issues_set]
    return QAResult(passed=(len(issues) == 0), issues=issues)


def boundary_metrics(
    frame: int,
    words: list[Word],
    *,
    silence: list[tuple[int, int]] | None = None,
    on_black: bool | None = None,
    on_freeze: bool | None = None,
) -> BoundaryMetrics:
    """Build :class:`~laura.analysis.shorts_types.BoundaryMetrics` from already-loaded data.

    * ``severs_word`` is derived from :func:`laura.analysis.editorial._covering_word` —
      ``True`` iff ``frame`` strictly bisects a spoken word (``start < frame < end``).
    * ``in_silence`` is derived from the inlined :func:`_in_silence` half-open membership
      test over the supplied ``silence`` intervals (``None`` when no silence info is supplied).
    * ``on_black`` / ``on_freeze`` stay ``None`` unless the caller fills them (e.g. after
      running :func:`probe_boundaries`).

    Parameters
    ----------
    frame:
        Source-frame index of this boundary (a candidate's ``start_frame`` or
        ``end_frame_exclusive``).
    words:
        Transcript word list; used by :func:`_covering_word` to test word bisection.
    silence:
        Optional list of end-exclusive silence intervals ``[start, end)`` from
        :func:`laura.analysis.silence.detect_silence`. ``None`` → ``in_silence=None``.
    on_black:
        Caller-supplied visual probe result. ``None`` (default) → not probed.
    on_freeze:
        Caller-supplied visual probe result. ``None`` (default) → not probed.
    """
    severs: bool = _covering_word(frame, words) is not None
    in_sil: bool | None = _in_silence(frame, silence) if silence is not None else None
    return BoundaryMetrics(
        frame=frame,
        severs_word=severs,
        on_black=on_black,
        on_freeze=on_freeze,
        in_silence=in_sil,
    )


def qa_candidate(
    cand: ShortCandidate,
    words: list[Word],
    *,
    silence: list[tuple[int, int]] | None = None,
    sentence_frames: set[int] | None = None,
    speaker_frames: set[int] | None = None,
    start_black: bool | None = None,
    end_black: bool | None = None,
    start_freeze: bool | None = None,
    end_freeze: bool | None = None,
) -> QAResult:
    """Convenience wrapper: build both :class:`BoundaryMetrics` and call :func:`qa_gate`.

    Computes ``editorial_metrics`` over the two cut frames ``[start_frame,
    end_frame_exclusive]`` (reusing :func:`laura.analysis.editorial.editorial_metrics` so
    ``pct_mid_word`` is the single source of truth), builds ``BoundaryMetrics`` for each
    boundary, then delegates to :func:`qa_gate`.

    Parameters
    ----------
    cand:
        The candidate to gate.
    words:
        Transcript word list.
    silence:
        Optional end-exclusive silence intervals.
    sentence_frames, speaker_frames:
        Optional semantic frame sets (forwarded to ``editorial_metrics`` for the optional
        keys; not used by the hard gate itself but included for completeness).
    start_black, end_black, start_freeze, end_freeze:
        Visual probe results to inject into :class:`BoundaryMetrics`.  ``None`` means not
        probed.
    """
    cuts = [cand.start_frame, cand.end_frame_exclusive]
    ed = editorial_metrics(
        cuts,
        words,
        silence=silence,
        sentence_frames=sentence_frames,
        speaker_frames=speaker_frames,
    )
    start_bm = boundary_metrics(
        cand.start_frame,
        words,
        silence=silence,
        on_black=start_black,
        on_freeze=start_freeze,
    )
    end_bm = boundary_metrics(
        cand.end_frame_exclusive,
        words,
        silence=silence,
        on_black=end_black,
        on_freeze=end_freeze,
    )
    return qa_gate(start_bm, end_bm, ed)


# ---------------------------------------------------------------------------
# OPTIONAL ffmpeg-backed probe — heavy imports are LOCAL to this function so
# the pure gate above is importable without ffmpeg / subprocess present.
# ---------------------------------------------------------------------------


def probe_boundaries(
    video_path: Path | str,
    frames: list[int],
    *,
    total_frames: int | None = None,
    frame_loader: FrameLoader | None = None,
) -> dict[int, tuple[bool | None, bool | None]]:
    """Decode a few frames around each cut and fill ``on_black`` / ``on_freeze``.

    Parameters
    ----------
    video_path:
        Path to the video file.
    frames:
        List of source-frame indices (cut frames) to probe.
    total_frames:
        Total frame count of the asset; used to clamp the decode window.
    frame_loader:
        IO seam — a :data:`~laura.analysis.eval_cut.FrameLoader` callable
        ``(video_path, lo, hi_exclusive) -> list[np.ndarray]``. Defaults to
        :func:`~laura.analysis.eval_cut.load_gray_frames_ffmpeg`.

    Returns
    -------
    dict[int, tuple[bool | None, bool | None]]
        Maps each input frame to ``(on_black, on_freeze)``.  On any IO / decode error
        for a frame, that frame maps to ``(None, None)``.  **Never raises.**

    Notes
    -----
    * ``on_black`` — ``True`` when every sampled frame around the cut has
      ``mean(luma) < BLACK_LUMA`` AND ``max(luma) < BLACK_MAX``
      (matching :mod:`laura.analysis.quality` semantics).
    * ``on_freeze`` — ``True`` when the per-frame luma diff across the sampled window
      is everywhere ``≤ FREEZE_DIFF_MAX`` (near-static / frozen run).
    * Either field is ``None`` on decode error or insufficient frames.
    """
    # Heavy imports: local so the pure gate path never loads subprocess/numpy indirectly
    # through this module.
    import numpy as np  # noqa: PLC0415

    from .eval_cut import _diff_signal, load_gray_frames_ffmpeg
    from .quality import BLACK_LUMA, BLACK_MAX

    loader: FrameLoader = load_gray_frames_ffmpeg if frame_loader is None else frame_loader

    # Probe window: a few frames on each side of the cut is enough to detect a freeze run.
    PROBE_RADIUS = 3

    result: dict[int, tuple[bool | None, bool | None]] = {}

    for frame in frames:
        lo = max(0, frame - PROBE_RADIUS)
        hi = frame + PROBE_RADIUS + 1  # end-exclusive
        if total_frames is not None:
            hi = min(hi, total_frames)

        try:
            grey_frames: list[np.ndarray] = loader(video_path, lo, hi)
        except Exception:
            _log.debug("probe_boundaries: IO error for frame %d — degrading to (None, None)", frame)
            result[frame] = (None, None)
            continue

        if len(grey_frames) < 1:
            result[frame] = (None, None)
            continue

        # --- on_black: every decoded frame is uniformly dark ------------------
        on_black: bool | None = all(
            float(np.mean(f)) < BLACK_LUMA and float(np.max(f)) < BLACK_MAX
            for f in grey_frames
        )

        # --- on_freeze: diff signal is everywhere <= FREEZE_DIFF_MAX ----------
        if len(grey_frames) < 2:
            on_freeze: bool | None = None  # can't compute diff without at least 2 frames
        else:
            diffs = _diff_signal(grey_frames)
            on_freeze = all(d <= FREEZE_DIFF_MAX for d in diffs)

        result[frame] = (on_black, on_freeze)

    return result
