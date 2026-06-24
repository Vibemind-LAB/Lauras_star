"""Shared frozen result types for the auto-shorts MVP backbone (pure, no IO).

These types are the interface contract between the three short-extraction modules:

* :mod:`laura.analysis.shorts_segments` produces :class:`ShortCandidate` windows whose start
  and end fall ONLY on a sentence-end / speaker-turn frame (transcript-safe BY CONSTRUCTION).
* :mod:`laura.analysis.shorts_score` attaches a multimodal :class:`ShortScore` to each candidate,
  generalising :func:`laura.analysis.joint.joint_place`'s blended placement to a whole window.
* :mod:`laura.analysis.shorts_qa` gates a scored candidate with a pure :class:`QAResult`.

Invariants honoured here, identical to the rest of Laura's editorial layer:

* **Integer source frames** everywhere — never float seconds as state. Seconds are a derived
  projection for the UI only and are deliberately absent from these dataclasses.
* **Ranges are end-exclusive**: a candidate spans ``[start_frame, end_frame_exclusive)``
  exactly like a shot's ``[src_in_frame, src_out_frame_exclusive)`` and a word's
  ``[start_frame, end_frame)``. The candidate's *cut frames* are ``start_frame`` and
  ``end_frame_exclusive`` — both must be a member of the sentence-end / speaker-turn frame
  sets that produced them.
* **Frozen** dataclasses so a candidate is hashable and never mutated downstream.

Nothing in this module does IO, decodes frames, or imports a heavy model — it is the typed
vocabulary the three pure modules speak.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "BoundaryKind",
    "ShortCandidate",
    "BoundaryMetrics",
    "ShortScore",
    "QAResult",
]

# How a candidate boundary frame was justified as transcript-safe. A candidate's start and end
# each carry one of these. ``speaker_turn`` is the strongest narrative seam, ``sentence_end`` the
# next; both are always also editorially clean (they are word ``end_frame`` values, end-exclusive,
# so they sit *between* words, never inside one).
BoundaryKind = str  # one of: "speaker_turn", "sentence_end"


@dataclass(frozen=True)
class ShortCandidate:
    """One candidate short window, transcript-safe by construction.

    Spans the half-open range ``[start_frame, end_frame_exclusive)`` in the asset's source-frame
    space (the same space as :class:`laura.analysis.editorial.Word`, shots and silences). Both
    cut frames are guaranteed members of the sentence-end / speaker-turn frame sets, so neither
    severs a spoken word: ``start_frame`` is where the short begins (a clean seam) and
    ``end_frame_exclusive`` is the frame just past its last content frame (also a clean seam).

    ``start_boundary`` / ``end_boundary`` record *why* each cut is safe (``"speaker_turn"`` or
    ``"sentence_end"``) for explainability and for the scorer's ``hook_position`` term.
    ``duration_frames`` is a derived convenience (``end_frame_exclusive - start_frame``); it is
    always ``> 0``.
    """

    start_frame: int
    end_frame_exclusive: int
    start_boundary: BoundaryKind
    end_boundary: BoundaryKind

    @property
    def duration_frames(self) -> int:
        """Length of the window in whole frames (end-exclusive); always positive."""
        return self.end_frame_exclusive - self.start_frame


@dataclass(frozen=True)
class BoundaryMetrics:
    """Per-boundary acoustic / visual facts at ONE cut frame, the QA gate's pure input.

    All fields are computed by the caller (or by :func:`laura.analysis.shorts_qa` from
    already-loaded analysis data) and handed to the pure gate, so QA needs no ffmpeg. ``frame``
    is the source-frame index of this boundary (a candidate's ``start_frame`` or
    ``end_frame_exclusive``).

    * ``on_black`` — the boundary frame lands on a uniformly-dark frame (see
      :func:`laura.analysis.quality` ``BLACK_LUMA`` / ``BLACK_MAX``). ``None`` == not probed.
    * ``on_freeze`` — the boundary frame sits inside a near-static run (a freeze / glitch).
      ``None`` == not probed.
    * ``in_silence`` — the boundary lies inside a detected real audio silence interval
      (a clean acoustic cut point). ``None`` == no silence info supplied.
    * ``severs_word`` — the boundary strictly bisects a spoken word (HARD failure). Derived from
      :func:`laura.analysis.editorial._covering_word`.
    """

    frame: int
    severs_word: bool
    on_black: bool | None = None
    on_freeze: bool | None = None
    in_silence: bool | None = None


@dataclass(frozen=True)
class ShortScore:
    """Multimodal cut-quality score for one :class:`ShortCandidate`, with explainable breakdown.

    ``total`` is the final weighted, robust-z-normalised score (higher is better) — only meaningful
    when ``rejected`` is ``False``. ``breakdown`` maps each component / penalty name to its
    *post-normalisation, pre-weight* contribution so a UI or agent can explain the ranking
    (e.g. ``{"transcript_safety": 1.2, "audio_silence_at_boundaries": 0.4, "word_interruption": 0.0,
    ...}``). ``components`` keeps the raw (pre-z) feature values for debugging / golden-set diffing.

    ``rejected`` is ``True`` iff a HARD constraint fired (currently: any boundary severs a word).
    A rejected candidate carries ``reject_reason`` (a short stable code, e.g.
    ``"word_interruption"``) and a sentinel ``total`` of ``-inf`` so it always sorts last; it is
    never silently kept.
    """

    total: float
    breakdown: dict[str, float]
    components: dict[str, float]
    rejected: bool
    reject_reason: str | None = None


@dataclass(frozen=True)
class QAResult:
    """Outcome of the pure QA gate for one candidate.

    ``passed`` is ``True`` only when every hard check holds: no boundary on black, none on a freeze,
    ``pct_mid_word == 0`` (no severed words across both cuts, from
    :func:`laura.analysis.editorial.editorial_metrics`), and both cut frames are exact non-negative
    integers (an invariant guard, defensive against any float leakage). ``issues`` lists a stable
    code per failed check (empty iff ``passed``), e.g. ``"start_on_black"``, ``"end_on_freeze"``,
    ``"mid_word_cut"``, ``"non_integer_boundary"`` — in deterministic order for golden tests.
    """

    passed: bool
    issues: list[str] = field(default_factory=list)
