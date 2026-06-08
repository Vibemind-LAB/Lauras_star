"""Ground-truth cut benchmark: synthetic suite, GT comparator, knob sweep.

This is the *labelled* counterpart to :mod:`laura.analysis.eval_cut`. ``eval_cut`` scores how
frame-exact a detected boundary is against the luma signal (self-supervised — it has no idea
where the "right" cut is, only where the picture changes). Here we **construct** videos whose
true cut frames we know by definition (concatenate four 30-frame scenes -> true cuts at
``[30, 60, 90]``), so we can measure the two errors a self-supervised metric cannot:

* a **false positive** — a detected boundary with no true cut within tolerance (an invented cut);
* a **miss** — a true cut with no detected boundary within tolerance (a dropped cut).

plus the signed/absolute offset of each matched cut. That single :class:`GTReport` is what the
knob sweep (:func:`run_sweep`) minimises across the grid.

Three layers, deliberately separated:

* **Synthetic video suite** (:func:`build_suite`) — needs ffmpeg, generated into a temp dir via
  ``run_ffmpeg``. Hard-cut concatenations and one gradual xfade case, across a few content
  variations so a tuned knob is not over-fit to one clip.
* **GT comparator** (:func:`compare_to_ground_truth`) — PURE. Takes detected frames + true
  frames + a tolerance and returns the :class:`GTReport`. No ffmpeg, fully unit-testable.
* **Editorial scenarios** (:func:`editorial_scenarios`, :func:`editorial_pick_offset`) — PURE.
  Synthetic ``Word``/silence/speaker layouts where the editorially-ideal frame is known by
  construction (a speaker turn inside a silence at frame X); we measure whether
  :func:`laura.analysis.joint.joint_place` lands on it, and sweep ``w_editorial`` to draw the
  visual-vs-editorial trade-off curve that justifies the bias slider.

Every frame here is a source-frame index, ranges are end-exclusive, and matching is greedy
nearest-first — all consistent with the rest of Laura.
"""

from __future__ import annotations

import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from laura.analysis.editorial import Word
from laura.analysis.joint import joint_place
from laura.analysis.semantic import sentence_end_frames, speaker_turn_frames
from laura.ingest.ffmpeg import run_ffmpeg

# Default match tolerance (frames) for the GT comparator: a detected cut counts as hitting a
# true cut when it is within +/- this many frames. 2 frames mirrors eval_cut's pct_within2
# "good enough for editorial" band on hard cuts.
DEFAULT_TOL = 2

# A linear xfade has a near-constant per-frame delta, so there is no single luma *peak* on the
# transition midpoint — any frame inside the fade is an equally valid cut. We therefore score
# the gradual case against the transition midpoint with a wider tolerance equal to half the
# transition length: a cut anywhere inside the fade is "on" the gradual cut.
GRADUAL_TOL = 15


# ======================================================================================
# GT comparator (PURE — no ffmpeg, the heart of the benchmark)
# ======================================================================================


@dataclass(frozen=True)
class GTReport:
    """How a detected boundary list scored against KNOWN true cut frames.

    All distances are in frames; ``tol`` is the match half-window used to build the report.
    A detected frame matches the nearest unused true cut within ``tol``; unmatched detections
    are false positives and unmatched true cuts are misses.
    """

    tol: int
    n_true: int               # number of ground-truth cuts
    n_detected: int           # number of detected boundaries
    n_matched: int            # true cuts matched by some detection within tol
    false_positives: int      # detections with no true cut within tol
    misses: int               # true cuts with no detection within tol
    mean_abs_offset: float    # mean |detected - true| over matched pairs (0.0 if none)
    median_abs_offset: float  # median |detected - true| over matched pairs
    pct_exact: float          # share of matched pairs with offset 0
    pct_within1: float        # share of matched pairs with |offset| <= 1
    pct_within2: float        # share of matched pairs with |offset| <= 2
    matched_offsets: tuple[int, ...] = field(default_factory=tuple)  # signed, in match order

    @property
    def recall(self) -> float:
        """Fraction of true cuts that were matched (1.0 == no misses)."""
        return self.n_matched / self.n_true if self.n_true else 1.0

    @property
    def precision(self) -> float:
        """Fraction of detections that matched a true cut (1.0 == no false positives)."""
        if self.n_detected == 0:
            return 1.0 if self.n_true == 0 else 0.0
        return (self.n_detected - self.false_positives) / self.n_detected

    @property
    def f1(self) -> float:
        """Harmonic mean of precision and recall (1.0 == perfect detection set)."""
        p, r = self.precision, self.recall
        return (2 * p * r / (p + r)) if (p + r) > 0 else 0.0


def _median(values: Sequence[float]) -> float:
    """Median of ``values`` (0.0 when empty); no numpy dependency in the pure path."""
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def compare_to_ground_truth(
    detected: Sequence[int], true_cuts: Sequence[int], *, tol: int = DEFAULT_TOL
) -> GTReport:
    """Score ``detected`` boundaries against KNOWN ``true_cuts`` with a match tolerance ``tol``.

    Matching is greedy and nearest-first: we consider every ``(detected, true)`` pair within
    ``tol`` frames, sorted by absolute distance (ties broken by the lower frames so it is
    deterministic), and lock in each pair whose two endpoints are both still free. After the
    pass, unmatched detections are false positives and unmatched true cuts are misses. Greedy
    nearest-first is optimal here because true cuts are well separated (>> ``tol`` apart) in
    every synthetic case, so there is never a contended assignment.

    ``tol`` must be ``>= 0``. The offset percentages are over the *matched* pairs only (an
    unmatched true cut is a miss, not a huge offset — those are different failures and we report
    them separately). With no true cuts the report is vacuously perfect except for any
    detections, which all count as false positives.
    """
    if tol < 0:
        raise ValueError("tol must be >= 0")

    det = list(detected)
    tru = list(true_cuts)
    pairs: list[tuple[int, int, int]] = []  # (abs_dist, detected_idx, true_idx)
    for di, d in enumerate(det):
        for ti, t in enumerate(tru):
            dist = abs(d - t)
            if dist <= tol:
                pairs.append((dist, di, ti))
    # Nearest-first; deterministic tie-break on the detected then true frame value.
    pairs.sort(key=lambda p: (p[0], det[p[1]], tru[p[2]]))

    used_det: set[int] = set()
    used_true: set[int] = set()
    matched_offsets: list[int] = []
    for _dist, di, ti in pairs:
        if di in used_det or ti in used_true:
            continue
        used_det.add(di)
        used_true.add(ti)
        matched_offsets.append(det[di] - tru[ti])  # signed: +ve == detection is late

    n_matched = len(matched_offsets)
    false_positives = len(det) - n_matched
    misses = len(tru) - n_matched

    abs_off = [abs(o) for o in matched_offsets]
    n = n_matched
    pct_exact = sum(1 for a in abs_off if a == 0) / n if n else 0.0
    pct_within1 = sum(1 for a in abs_off if a <= 1) / n if n else 0.0
    pct_within2 = sum(1 for a in abs_off if a <= 2) / n if n else 0.0

    return GTReport(
        tol=tol,
        n_true=len(tru),
        n_detected=len(det),
        n_matched=n_matched,
        false_positives=false_positives,
        misses=misses,
        mean_abs_offset=(sum(abs_off) / n) if n else 0.0,
        median_abs_offset=_median(abs_off),
        pct_exact=pct_exact,
        pct_within1=pct_within1,
        pct_within2=pct_within2,
        matched_offsets=tuple(matched_offsets),
    )


# ======================================================================================
# Synthetic video suite (needs ffmpeg)
# ======================================================================================


@dataclass(frozen=True)
class BenchCase:
    """One synthetic clip with its ground truth.

    ``video`` is the rendered file, ``true_cuts`` the internal cut frames (no leading 0),
    ``tol`` the match tolerance appropriate to the case (tight for hard cuts, wide for the
    gradual midpoint), and ``total_frames`` the asset length for window clamping.
    """

    name: str
    video: Path
    true_cuts: tuple[int, ...]
    tol: int
    total_frames: int
    kind: str  # "hard" | "gradual"


_SIZE = "256x144"
_RATE = 30


def _encode_scene(out: Path, source: str, *, frames: int) -> None:
    """Render ``frames`` frames of an lavfi ``source`` (e.g. ``color=c=red`` / ``testsrc``).

    ``source`` is the base lavfi filter with any of its own options (``color=c=red``,
    ``testsrc``). The common ``size``/``rate``/``duration`` options are appended with the lavfi
    option syntax — the *first* option after the filter name uses ``=`` and the rest use ``:``.
    So a bare source (``testsrc``) becomes ``testsrc=size=...`` while one that already carries an
    option (``color=c=red``) becomes ``color=c=red:size=...``; both are valid filtergraphs.
    """
    duration = frames / _RATE
    sep = ":" if "=" in source else "="
    opts = f"size={_SIZE}:rate={_RATE}:duration={duration:.4f}"
    spec = f"{source}{sep}{opts}"
    run_ffmpeg([
        "-f", "lavfi", "-i", spec,
        # Short GOP + all-intra keeps decode exact and avoids B-frame reordering surprises in
        # the per-frame diff the detectors/snappers rely on.
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "1", "-frames:v", str(frames),
        str(out),
    ])


def _concat(parts: Sequence[Path], out: Path, work: Path) -> None:
    """Concatenate ``parts`` losslessly into ``out`` via the ffmpeg concat demuxer."""
    listing = work / f"{out.stem}_list.txt"
    listing.write_text("\n".join(f"file '{p.as_posix()}'" for p in parts), encoding="utf-8")
    run_ffmpeg([
        "-f", "concat", "-safe", "0", "-i", str(listing),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "1", str(out),
    ])


def _build_hard_case(
    work: Path, name: str, sources: Sequence[str], *, scene_frames: int = 30
) -> BenchCase:
    """Concatenate ``len(sources)`` scenes of ``scene_frames`` -> true cuts at the join frames."""
    parts: list[Path] = []
    for i, src in enumerate(sources):
        part = work / f"{name}_{i}.mp4"
        _encode_scene(part, src, frames=scene_frames)
        parts.append(part)
    out = work / f"{name}.mp4"
    _concat(parts, out, work)
    true_cuts = tuple(scene_frames * i for i in range(1, len(sources)))
    return BenchCase(
        name=name,
        video=out,
        true_cuts=true_cuts,
        tol=DEFAULT_TOL,
        total_frames=scene_frames * len(sources),
        kind="hard",
    )


def _build_gradual_case(
    work: Path,
    name: str,
    *,
    color_a: str = "black",
    color_b: str = "white",
    pre_frames: int = 45,
    fade_frames: int = 30,
    post_frames: int = 45,
) -> BenchCase:
    """An xfade between two solids: ground truth = the transition MIDPOINT.

    Scene A plays for ``pre_frames``, then a ``fade_frames``-long crossfade to scene B, then B
    for ``post_frames``. A linear fade has no single luma peak, so the editorially-correct cut
    is the transition midpoint (``pre_frames + fade_frames // 2``) and we score it with the wider
    :data:`GRADUAL_TOL` — any cut inside the fade is acceptable.
    """
    a = work / f"{name}_a.mp4"
    b = work / f"{name}_b.mp4"
    _encode_scene(a, f"color=c={color_a}", frames=pre_frames + fade_frames)
    _encode_scene(b, f"color=c={color_b}", frames=fade_frames + post_frames)
    out = work / f"{name}.mp4"
    offset_sec = pre_frames / _RATE
    dur_sec = fade_frames / _RATE
    run_ffmpeg([
        "-i", str(a), "-i", str(b),
        "-filter_complex",
        f"xfade=transition=fade:duration={dur_sec:.4f}:offset={offset_sec:.4f},format=yuv420p",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "1", str(out),
    ])
    midpoint = pre_frames + fade_frames // 2
    total = pre_frames + fade_frames + post_frames
    return BenchCase(
        name=name,
        video=out,
        true_cuts=(midpoint,),
        tol=GRADUAL_TOL,
        total_frames=total,
        kind="gradual",
    )


def build_suite(work_dir: Path | None = None) -> list[BenchCase]:
    """Generate the full synthetic GT suite into ``work_dir`` (a temp dir if ``None``).

    Cases (each with exactly-known true cuts):

    * ``hard_colors`` — four solid colours -> true cuts ``[30, 60, 90]``.
    * ``hard_testsrc`` — four distinct test patterns (testsrc / smptebars / testsrc2 /
      smptehdbars) -> ``[30, 60, 90]``; richer texture than solids, a different failure surface.
    * ``hard_lowmotion`` — near-identical adjacent scenes (dark greys a few levels apart) ->
      ``[30, 60, 90]``; a low-contrast stress case where weak cuts are easy to miss.
    * ``gradual_fade`` — a 30-frame black->white xfade; ground truth = midpoint ``60`` at the
      wider :data:`GRADUAL_TOL`.

    Requires ffmpeg on PATH (via ``run_ffmpeg``). Returns the cases in a stable order.
    """
    work = work_dir or Path(tempfile.mkdtemp(prefix="laura-cutbench-"))
    work.mkdir(parents=True, exist_ok=True)
    cases: list[BenchCase] = [
        _build_hard_case(work, "hard_colors", ["color=c=red", "color=c=green",
                                               "color=c=blue", "color=c=yellow"]),
        _build_hard_case(work, "hard_testsrc", ["testsrc", "smptebars",
                                                "testsrc2", "smptehdbars"]),
        _build_hard_case(work, "hard_lowmotion", ["color=c=0x202020", "color=c=0x282828",
                                                  "color=c=0x303030", "color=c=0x383838"]),
        _build_gradual_case(work, "gradual_fade"),
    ]
    return cases


# ======================================================================================
# Detector runs against the suite
# ======================================================================================


@dataclass(frozen=True)
class KnobConfig:
    """One point in the visual knob grid."""

    detector: str        # "adaptive" | "hybrid"
    snap_window: int     # refine.snap_boundaries window
    fuse_tol: int        # fuse.fuse_shots tol (only meaningful for hybrid)

    def label(self) -> str:
        if self.detector == "hybrid":
            return f"hybrid(snap={self.snap_window},fuse={self.fuse_tol})"
        return f"adaptive(snap={self.snap_window})"


def detect_with_knobs(case: BenchCase, knob: KnobConfig) -> list[int]:
    """Detect internal cut frames for ``case`` under ``knob`` (adaptive or fused+snapped hybrid).

    For ``adaptive`` we run PySceneDetect's adaptive detector, then apply
    :func:`laura.analysis.refine.snap_boundaries` at ``knob.snap_window`` so the snap window is
    actually exercised on the single-engine path too. For ``hybrid`` we fuse adaptive +
    TransNetV2 at ``knob.fuse_tol`` and snap the fused boundaries at ``knob.snap_window`` —
    mirroring the production ``detect_shots_hybrid`` pipeline but with the two knobs injected so
    the sweep can vary them. TransNetV2 absence degrades to adaptive-only (the fuse is a no-op).

    Returns the internal boundary frames (each shot start except the leading 0), sorted.
    """
    from laura.analysis.fuse import fuse_shots
    from laura.analysis.refine import snap_boundaries
    from laura.analysis.shots import detect_shots
    from laura.analysis.transnet import detect_shots_transnet, transnetv2_available

    adaptive = detect_shots(case.video, detector="adaptive")

    if knob.detector == "hybrid" and transnetv2_available():
        try:
            transnet = detect_shots_transnet(case.video)
            shots = fuse_shots(adaptive, transnet, tol=knob.fuse_tol)
        except (ImportError, RuntimeError):
            shots = adaptive
    else:
        shots = adaptive

    boundaries = sorted({s.src_in_frame for s in shots if s.src_in_frame > 0})
    snapped = snap_boundaries(
        case.video, boundaries, window=knob.snap_window, total_frames=case.total_frames
    )
    return sorted(snapped)


@dataclass(frozen=True)
class CaseResult:
    """A GTReport for one (case, knob) pair, with the detected frames kept for the table."""

    case: str
    knob: KnobConfig
    detected: tuple[int, ...]
    report: GTReport


def run_case(case: BenchCase, knob: KnobConfig) -> CaseResult:
    """Detect ``case`` under ``knob`` and score against its ground truth at the case tolerance."""
    detected = detect_with_knobs(case, knob)
    report = compare_to_ground_truth(detected, case.true_cuts, tol=case.tol)
    return CaseResult(case=case.name, knob=knob, detected=tuple(detected), report=report)


# ======================================================================================
# Visual knob sweep
# ======================================================================================

# The grid the task pins: snap window {4,6,8,10}, fuse tol {4,8,12}, detector {adaptive, hybrid}.
SNAP_WINDOWS = (4, 6, 8, 10)
FUSE_TOLS = (4, 8, 12)
DETECTORS = ("adaptive", "hybrid")


def knob_grid(*, include_hybrid: bool = True) -> list[KnobConfig]:
    """Enumerate the visual knob grid.

    Adaptive ignores ``fuse_tol`` (single engine), so it contributes one config per snap window
    rather than per (snap, fuse) pair — we fix its ``fuse_tol`` to the production default ``8``
    purely as a label placeholder. Hybrid spans the full (snap, fuse) cross-product. When
    ``include_hybrid`` is ``False`` (no TransNetV2) only the adaptive rows are returned.
    """
    grid: list[KnobConfig] = [
        KnobConfig(detector="adaptive", snap_window=w, fuse_tol=8) for w in SNAP_WINDOWS
    ]
    if include_hybrid:
        grid += [
            KnobConfig(detector="hybrid", snap_window=w, fuse_tol=t)
            for w in SNAP_WINDOWS
            for t in FUSE_TOLS
        ]
    return grid


def _nearest_drift(detected: Sequence[int], true_cuts: Sequence[int]) -> float:
    """Total |detected - nearest true cut| over all detections (0.0 when either list is empty).

    This is the *un-tolerated* drift: a boundary that snapped 30 -> 26 on a solid-colour clip
    contributes 4 here even though the GT comparator (tol 2) records it as a false positive +
    miss. It is exactly the signal that separates snap windows — a wider window lets the snap
    wander further into compression flicker on a flat scene — which the tolerance-gated FP/miss
    count saturates away. Used only as the sweep's tiebreaker, never to redefine FP/miss.
    """
    if not detected or not true_cuts:
        return 0.0
    return float(sum(min(abs(d - t) for t in true_cuts) for d in detected))


@dataclass(frozen=True)
class KnobScore:
    """Aggregate score of one knob config across all hard-cut cases (lower ``cost`` is better)."""

    knob: KnobConfig
    mean_abs_offset: float
    total_false_positives: int
    total_misses: int
    mean_pct_within1: float
    total_drift: float  # sum of |detection - nearest true cut| across hard cases (tiebreaker)

    @property
    def cost(self) -> float:
        """Single scalar to minimise: GT offset + a heavy FP/miss penalty + a small drift term.

        Matched offsets are sub-frame to a few frames; a false positive or a miss is a
        *qualitatively* worse failure (an invented or dropped cut), so each is worth 5 frames of
        penalty. The drift term (``0.1`` per frame of un-tolerated boundary wander) is a light
        tiebreaker: when two configs tie on offset and FP/miss it prefers the one whose snap
        strayed least from the true cuts — i.e. the smaller, less adventurous snap window on the
        flat-scene pathological case. The sweep picks the lowest cost; remaining ties fall to the
        smaller snap window then the smaller fuse tol.
        """
        return (
            self.mean_abs_offset
            + 5.0 * (self.total_false_positives + self.total_misses)
            + 0.1 * self.total_drift
        )


def run_sweep(
    cases: Sequence[BenchCase], *, include_hybrid: bool = True
) -> tuple[list[CaseResult], list[KnobScore]]:
    """Run every knob config over every case; return the per-(case,knob) results and aggregates.

    Aggregation is over the **hard-cut** cases only — the gradual case has no luma peak to snap
    to, so it characterises detector recall, not snap/fuse accuracy, and would only add noise to
    the knob ranking. The returned ``KnobScore`` list is sorted by ``cost`` (best first).
    """
    grid = knob_grid(include_hybrid=include_hybrid)
    results: list[CaseResult] = []
    for case in cases:
        for knob in grid:
            results.append(run_case(case, knob))

    hard = {c.name for c in cases if c.kind == "hard"}
    true_by_case = {c.name: c.true_cuts for c in cases}
    scores: list[KnobScore] = []
    for knob in grid:
        rows = [r for r in results if r.knob == knob and r.case in hard]
        if not rows:
            continue
        n = len(rows)
        scores.append(
            KnobScore(
                knob=knob,
                mean_abs_offset=sum(r.report.mean_abs_offset for r in rows) / n,
                total_false_positives=sum(r.report.false_positives for r in rows),
                total_misses=sum(r.report.misses for r in rows),
                mean_pct_within1=sum(r.report.pct_within1 for r in rows) / n,
                total_drift=sum(
                    _nearest_drift(r.detected, true_by_case[r.case]) for r in rows
                ),
            )
        )
    scores.sort(key=lambda s: (s.cost, s.knob.snap_window, s.knob.fuse_tol))
    return results, scores


# ======================================================================================
# Editorial half (PURE — no ffmpeg)
# ======================================================================================


@dataclass(frozen=True)
class EditorialScenario:
    """A synthetic speech layout whose editorially-ideal cut frame is known by construction.

    ``cut_frame`` is where the visual stage placed the cut (the visual peak). ``ideal_frame`` is
    the editorially-correct frame (a speaker turn inside a silence, a sentence end, ...).
    ``visual_peak_frame`` records where the picture actually changes most, so a sweep can show
    the trade-off as ``w_editorial`` rises: low weight -> lands on the visual peak, high weight
    -> lands on the editorial ideal. ``diff`` is the per-frame visual signal over the candidate
    band (``diff[i]`` is ``d`` at frame ``lo+i`` with ``lo == max(cut-window, 1)``), so the whole
    scenario is pure — no video needed.
    """

    name: str
    cut_frame: int
    ideal_frame: int
    visual_peak_frame: int
    words: tuple[Word, ...]
    silence: tuple[tuple[int, int], ...]
    diff: tuple[float, ...]
    window: int = 12


def _ramp_diff(lo: int, hi: int, peak_frame: int, *, peak: float = 1.0,
               floor: float = 0.02) -> tuple[float, ...]:
    """A flat ``floor`` diff over ``[lo, hi]`` with a single ``peak`` at ``peak_frame``."""
    n = hi - lo + 1
    diff = [floor] * n
    diff[peak_frame - lo] = peak
    return tuple(diff)


def _diverge_scenario() -> EditorialScenario:
    """The headline DIVERGENCE: the visual peak is mid-word; the clean editorial seam is elsewhere.

    A word spans ``[260, 273)`` so the visual peak at frame ``270`` sits mid-word (clipped, bad
    audio). A real silence ``[273, 279)`` follows, and a speaker turn lands at frame ``273`` — the
    editorially-ideal cut. As ``w_editorial`` rises the chosen frame moves from the visual peak
    ``270`` to the editorial ideal ``273``: exactly the trade-off the bias slider exposes.
    """
    window = 12
    cut = 270
    lo = max(cut - window, 1)
    hi = cut + window
    words = (
        Word(start_frame=260, end_frame=273, text="thought", speaker="A"),
        Word(start_frame=279, end_frame=290, text="Next.", speaker="B"),
    )
    return EditorialScenario(
        name="diverge_speaker_in_silence",
        cut_frame=cut,
        ideal_frame=273,                 # speaker turn on a clean edge, start of the silence
        visual_peak_frame=270,           # picture changes most here — but it is mid-word
        words=words,
        silence=((273, 279),),           # a real breath between the two speakers
        diff=_ramp_diff(lo, hi, peak_frame=270),
        window=window,
    )


def _aligned_scenario() -> EditorialScenario:
    """A control where visual and editorial AGREE: the peak already sits on a clean silence edge.

    The visual peak at frame ``272`` coincides with a word edge that opens a silence and a
    speaker turn. Any ``w_editorial`` should keep the cut on ``272`` — there is nothing to trade.
    """
    window = 12
    cut = 272
    lo = max(cut - window, 1)
    hi = cut + window
    words = (
        Word(start_frame=260, end_frame=272, text="done.", speaker="A"),
        Word(start_frame=278, end_frame=290, text="Right.", speaker="B"),
    )
    return EditorialScenario(
        name="aligned_peak_on_seam",
        cut_frame=cut,
        ideal_frame=272,
        visual_peak_frame=272,
        words=words,
        silence=((272, 278),),
        diff=_ramp_diff(lo, hi, peak_frame=272),
        window=window,
    )


def editorial_scenarios() -> list[EditorialScenario]:
    """The pure editorial scenarios: one divergence (drives the curve) and one aligned control."""
    return [_diverge_scenario(), _aligned_scenario()]


def editorial_pick_offset(scenario: EditorialScenario, w_editorial: float) -> tuple[int, int, int]:
    """Run :func:`joint_place` at the given ``w_editorial`` and return placement offsets.

    Returns ``(chosen_frame, offset_to_ideal, offset_to_visual_peak)`` — both offsets absolute.
    The sentence-end and speaker-turn frame sets are derived from the scenario's words exactly as
    the production joint placer would, so this measures the real decision, not a stub.
    """
    sentences = sentence_end_frames(scenario.words)
    speakers = speaker_turn_frames(scenario.words)
    chosen, _score = joint_place(
        scenario.cut_frame,
        list(scenario.words),
        list(scenario.diff),
        window=scenario.window,
        w_visual=max(0.0, 1.0 - w_editorial),
        w_editorial=w_editorial,
        silence=list(scenario.silence),
        sentence_frames=sentences,
        speaker_frames=speakers,
    )
    return (
        chosen,
        abs(chosen - scenario.ideal_frame),
        abs(chosen - scenario.visual_peak_frame),
    )


# Editorial blend trade-off grid (the bias-slider justification, NOT a tuned ground truth).
W_EDITORIAL_GRID = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)


@dataclass(frozen=True)
class TradeoffPoint:
    """One point on the editorial trade-off curve for the divergence scenario."""

    w_editorial: float
    chosen_frame: int
    offset_to_visual_peak: int   # frames away from the picture-best cut (rises with w_editorial)
    offset_to_editorial_ideal: int  # frames away from the clean seam (falls with w_editorial)


def editorial_tradeoff_curve(
    scenario: EditorialScenario | None = None,
    *,
    grid: Sequence[float] = W_EDITORIAL_GRID,
) -> list[TradeoffPoint]:
    """Sweep ``w_editorial`` over ``grid`` on the divergence scenario -> the trade-off curve.

    As ``w_editorial`` rises the chosen frame slides from the visual peak (picture-clean, audio
    clipped) to the editorial ideal (audio-clean, slightly off the visual peak). The two offset
    columns are the curve: ``offset_to_visual_peak`` climbs while ``offset_to_editorial_ideal``
    drops. There is no single "correct" weight — the curve characterises the preference, which is
    precisely what the UI bias slider lets an editor choose.
    """
    sc = scenario or _diverge_scenario()
    points: list[TradeoffPoint] = []
    for w in grid:
        chosen, off_ideal, off_peak = editorial_pick_offset(sc, w)
        points.append(
            TradeoffPoint(
                w_editorial=w,
                chosen_frame=chosen,
                offset_to_visual_peak=off_peak,
                offset_to_editorial_ideal=off_ideal,
            )
        )
    return points
