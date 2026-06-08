"""Unified rough-cut quality — one headline score blending picture and speech.

The two earlier stages each answer half the question of "is this a good cut?":

* :func:`laura.analysis.eval_cut.evaluate_boundaries` scores **visual frame-exactness** —
  does every boundary sit on the frame where the picture actually changes? (``exactness_score``,
  ``∈[0,1]``.)
* :func:`laura.analysis.editorial.editorial_metrics` scores **editorial cleanliness** — does
  every cut avoid slicing through a spoken word? (``pct_clean``, ``∈[0,1]``.)

A great rough cut is *both*: frame-exact on the image AND not mid-word on the audio. This module
folds the two into a single :class:`RoughCutQuality` — the metric the whole "exact cut" effort
has been building toward — as a weighted blend ``overall = w_visual*visual + w_editorial*clean``
(weights normalised). It carries the full sub-reports alongside the headline so a caller can drill
into *why* a score is what it is.

Purity: this is pure aside from the visual frame IO, which is entirely owned by
``evaluate_boundaries`` (whose ``frame_loader`` seam tests inject). The editorial half is already
pure. ``cuts`` and ``words`` live in the same source-frame space as everywhere else in Laura.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .editorial import Word, editorial_metrics
from .eval_cut import DEFAULT_WINDOW, CutEvalReport, FrameLoader, evaluate_boundaries

# Editorial alignment window default mirrors ``editorial.DEFAULT_WINDOW`` (~0.4s @ 30fps); kept
# as a separate symbol so the visual and editorial windows can diverge per call.
DEFAULT_EDITORIAL_WINDOW = 12


@dataclass(frozen=True)
class RoughCutQuality:
    """Single quality verdict for a rough cut: visual exactness fused with editorial cleanliness.

    ``overall`` is the normalised weighted blend of ``visual_exactness`` and ``editorial_clean``;
    both sub-scores live in ``[0, 1]`` and ``overall`` therefore does too. The full ``visual``
    report and ``editorial`` metric dict are carried alongside so a caller can explain the headline.
    """

    n_cuts: int
    visual_exactness: float      # CutEvalReport.exactness_score (0..1)
    editorial_clean: float       # pct_clean (0..1): share of cuts NOT mid-word
    overall: float               # normalised weighted blend of the two above
    visual: CutEvalReport
    editorial: dict[str, float]


def evaluate_rough_cut(
    video_path: Path | str,
    cuts: list[int],
    words: list[Word],
    *,
    window: int = DEFAULT_WINDOW,
    editorial_window: int = DEFAULT_EDITORIAL_WINDOW,
    w_visual: float = 0.6,
    w_editorial: float = 0.4,
    frame_loader: FrameLoader | None = None,
) -> RoughCutQuality:
    """Blend visual frame-exactness and editorial cleanliness into one rough-cut quality score.

    * ``visual_exactness`` = ``evaluate_boundaries(video, cuts, window=window).exactness_score``
      — share of boundaries landing within one frame of the true luma peak.
    * ``editorial_clean`` = ``editorial_metrics(cuts, words)["pct_clean"]`` — share of cuts that
      do *not* bisect a spoken word. With ``words`` empty there is no transcript to violate, so
      this defaults to ``1.0`` (no mid-word penalty) and ``overall`` collapses to the visual score.
    * ``overall`` = ``w_visual*visual_exactness + w_editorial*editorial_clean`` with the weights
      normalised to sum to 1, so the caller may pass any non-negative pair (defaults 0.6 / 0.4 —
      a great cut is both frame-exact and not mid-word).

    ``window`` is the visual search half-window; ``editorial_window`` is accepted for symmetry with
    the alignment stage (the metric itself is window-free, so it is currently informational only).
    ``frame_loader`` is the visual IO seam forwarded to ``evaluate_boundaries``; when ``None`` the
    default ffmpeg loader is used, so tests can stub the visual half end-to-end.

    Raises ``ValueError`` if both weights are non-positive (the blend would be undefined).
    """
    if w_visual < 0 or w_editorial < 0:
        raise ValueError("weights must be non-negative")
    weight_sum = w_visual + w_editorial
    if weight_sum <= 0:
        raise ValueError("at least one weight must be positive")

    if frame_loader is None:
        visual = evaluate_boundaries(video_path, cuts, window=window)
    else:
        visual = evaluate_boundaries(
            video_path, cuts, window=window, frame_loader=frame_loader
        )
    visual_exactness = visual.exactness_score

    editorial = editorial_metrics(cuts, words)

    if not words:
        # No transcript -> no word can be bisected, so there is no editorial signal at all. We
        # report ``editorial_clean = 1.0`` (vacuously clean, no mid-word penalty) but drop the
        # editorial term from the blend entirely, so ``overall`` collapses to the pure visual
        # score exactly: ``overall == visual_exactness``. Folding 1.0 in via ``w_editorial``
        # instead would bias every transcript-less cut upward, which is not "no penalty".
        editorial_clean = 1.0
        overall = visual_exactness
    else:
        editorial_clean = editorial["pct_clean"]
        overall = (
            w_visual * visual_exactness + w_editorial * editorial_clean
        ) / weight_sum

    return RoughCutQuality(
        n_cuts=len(cuts),
        visual_exactness=visual_exactness,
        editorial_clean=editorial_clean,
        overall=overall,
        visual=visual,
        editorial=editorial,
    )
