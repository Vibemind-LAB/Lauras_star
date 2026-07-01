"""QA golden-set regression harness for cut quality.

Loads JSON fixture files, runs the pure metrics from ``qa_metrics``, and
compares against per-case baselines so regressions are caught per commit.

TODO(golden-set): The real suite needs 30-50 curated real creator videos with
hand-annotated ideal cuts. The fixtures bundled here are synthetic smoke tests
only and cannot validate production cut quality on real speech/music content.
The evaluation framework is intentionally model-free so it can run in CI without
GPU or heavy ML dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

try:
    import json as _json
except ImportError:  # pragma: no cover — stdlib always present
    raise

from laura.analysis.editorial import Word
from laura.analysis.qa_metrics import (
    audio_jump_score,
    boundary_offset,
    cut_f1,
    word_interruption_rate,
)

# Keys whose metric is "lower is better" (regression = metric rises above baseline).
# All other keys are "higher is better" (regression = metric falls below baseline).
_LOWER_IS_BETTER: frozenset[str] = frozenset(
    {
        "word_interruption_rate",
        "mean_abs_offset",
        "max_abs_offset",
        "mean_jump",
        "max_jump",
        "mean_jump_norm",
        # Allow callers to use the flattened alias "audio_jump_mean_norm" as well
        "audio_jump_mean_norm",
    }
)


# ---------------------------------------------------------------------------
# GoldenCase
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GoldenCase:
    """One labelled cut-quality test case (synthetic or real).

    ``baselines`` maps metric key → threshold value:
    - higher-is-better keys (f1, precision, recall): regression when
      ``metric < baseline - tol``.
    - lower-is-better keys (word_interruption_rate, audio_jump_*, offset):
      regression when ``metric > baseline + tol``.

    ``tol_frames`` controls how far a predicted cut can be from its gold
    counterpart and still count as a match (default 6 frames ≈ 0.2 s at 30 fps).
    """

    name: str
    words: list[Word]
    predicted_cuts: list[int]
    gold_cuts: list[int]
    rms: list[float]
    baselines: dict[str, float]
    tol_frames: int = 6


# ---------------------------------------------------------------------------
# evaluate_case
# ---------------------------------------------------------------------------


def evaluate_case(case: GoldenCase) -> dict[str, float]:
    """Run all pure metrics for *case* and return a flat result dict.

    Keys returned:
    - f1, precision, recall  (from cut_f1)
    - mean_abs_offset, max_abs_offset  (from boundary_offset)
    - word_interruption_rate
    - mean_jump, max_jump, mean_jump_norm  (from audio_jump_score)
    """
    f1_result = cut_f1(
        case.predicted_cuts, case.gold_cuts, tol_frames=case.tol_frames
    )
    offset_result = boundary_offset(
        case.predicted_cuts, case.gold_cuts, tol_frames=case.tol_frames
    )
    wir = word_interruption_rate(case.predicted_cuts, case.words)
    jump_result = audio_jump_score(case.predicted_cuts, case.rms)

    return {
        "f1": f1_result["f1"],
        "precision": f1_result["precision"],
        "recall": f1_result["recall"],
        "mean_abs_offset": offset_result["mean_abs_offset"],
        "max_abs_offset": offset_result["max_abs_offset"],
        "word_interruption_rate": wir,
        "mean_jump": jump_result["mean_jump"],
        "max_jump": jump_result["max_jump"],
        "mean_jump_norm": jump_result["mean_jump_norm"],
        # Flattened alias used in baseline JSON fixtures and GoldenCase docs.
        # Kept in sync with mean_jump_norm so baselines referencing either key work.
        "audio_jump_mean_norm": jump_result["mean_jump_norm"],
    }


# ---------------------------------------------------------------------------
# check_regressions
# ---------------------------------------------------------------------------


def check_regressions(
    metrics: dict[str, float],
    baselines: dict[str, float],
    *,
    tol: float = 0.02,
) -> list[str]:
    """Detect regressions against *baselines*.

    For each baseline key:
    - higher-is-better: regression when ``metrics[key] < baseline - tol``.
    - lower-is-better: regression when ``metrics[key] > baseline + tol``.

    Missing metric keys are silently skipped (the case may not produce all
    metrics if cuts or words are empty — that is not itself a regression).

    Returns a list of human-readable regression descriptions (empty = all OK).
    """
    regressions: list[str] = []
    for key, threshold in baselines.items():
        if key not in metrics:
            continue
        value = metrics[key]
        if key in _LOWER_IS_BETTER:
            if value > threshold + tol:
                regressions.append(
                    f"{key}: {value:.4f} > baseline {threshold:.4f} + tol {tol:.4f}"
                    f" (lower-is-better regression)"
                )
        else:
            if value < threshold - tol:
                regressions.append(
                    f"{key}: {value:.4f} < baseline {threshold:.4f} - tol {tol:.4f}"
                    f" (higher-is-better regression)"
                )
    return regressions


# ---------------------------------------------------------------------------
# load_cases
# ---------------------------------------------------------------------------


def load_cases(fixtures_dir: Path) -> list[GoldenCase]:
    """Load all ``*.json`` fixture files from *fixtures_dir* as GoldenCase objects.

    JSON schema per file:
    {
        "name": str,
        "words": [[text_or_null, start_frame, end_frame], ...],
        "predicted_cuts": [int, ...],
        "gold_cuts": [int, ...],
        "rms": [float, ...],
        "baselines": {str: float, ...},
        "tol_frames": int   // optional, default 6
    }

    Word items may be either ``[start_frame, end_frame]`` (2-element, no text)
    or ``[text_or_null, start_frame, end_frame]`` (3-element).
    """
    cases: list[GoldenCase] = []
    for path in sorted(fixtures_dir.glob("*.json")):
        raw = _json.loads(path.read_text(encoding="utf-8"))

        words: list[Word] = []
        for entry in raw.get("words", []):
            if len(entry) == 2:  # [start_frame, end_frame]
                words.append(Word(start_frame=int(entry[0]), end_frame=int(entry[1])))
            else:  # [text_or_null, start_frame, end_frame]
                text = str(entry[0]) if entry[0] is not None else None
                words.append(
                    Word(
                        start_frame=int(entry[1]),
                        end_frame=int(entry[2]),
                        text=text,
                    )
                )

        cases.append(
            GoldenCase(
                name=raw["name"],
                words=words,
                predicted_cuts=[int(c) for c in raw.get("predicted_cuts", [])],
                gold_cuts=[int(c) for c in raw.get("gold_cuts", [])],
                rms=[float(v) for v in raw.get("rms", [])],
                baselines={k: float(v) for k, v in raw.get("baselines", {}).items()},
                tol_frames=int(raw.get("tol_frames", 6)),
            )
        )
    return cases


# ---------------------------------------------------------------------------
# run_suite
# ---------------------------------------------------------------------------


def run_suite(fixtures_dir: Path) -> dict[str, list[str]]:
    """Run the full QA suite: evaluate every case and check against baselines.

    Returns a dict ``{case_name: [regression_strings]}`` where an empty list
    means the case passed all baseline checks.
    """
    results: dict[str, list[str]] = {}
    for case in load_cases(fixtures_dir):
        metrics = evaluate_case(case)
        regressions = check_regressions(metrics, case.baselines)
        results[case.name] = regressions
    return results
