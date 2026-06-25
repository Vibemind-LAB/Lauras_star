"""Pure, model-free cut-quality metrics for the QA golden-set harness.

All functions are deterministic and free of IO. numpy is used only for mean/std.

TODO(golden-set): these metrics are designed to work with 30-50 curated real creator
videos and their ideal cuts. The current harness ships only synthetic smoke fixtures.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from laura.analysis.editorial import Word, editorial_metrics

# ---------------------------------------------------------------------------
# match_cuts — greedy 1:1 matching
# ---------------------------------------------------------------------------


def match_cuts(
    predicted: Sequence[int],
    gold: Sequence[int],
    *,
    tol_frames: int,
) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Greedy 1:1 matching of predicted cuts to gold cuts within ``tol_frames``.

    Each gold cut is assigned to the nearest unmatched predicted cut within
    ``tol_frames`` (ties broken by lowest predicted index). Each predicted cut
    can be matched at most once.

    Returns:
        (matches, unmatched_pred, unmatched_gold) where matches is a list of
        ``(predicted_frame, gold_frame)`` pairs.
    """
    pred_list = list(predicted)
    gold_list = list(gold)

    used_pred: set[int] = set()  # indices into pred_list
    used_gold: set[int] = set()  # indices into gold_list
    matches: list[tuple[int, int]] = []

    for gi, gf in enumerate(gold_list):
        best_idx: int | None = None
        best_dist = tol_frames + 1
        for pi, pf in enumerate(pred_list):
            if pi in used_pred:
                continue
            dist = abs(pf - gf)
            if dist <= tol_frames and dist < best_dist:
                best_dist = dist
                best_idx = pi
        if best_idx is not None:
            used_pred.add(best_idx)
            used_gold.add(gi)
            matches.append((pred_list[best_idx], gf))

    # Unmatched lists are derived by index so duplicate frame values partition
    # correctly: match_cuts([30,30,30],[30],tol=0) → 2 unmatched_pred, not 0.
    unmatched_pred = [pred_list[pi] for pi in range(len(pred_list)) if pi not in used_pred]
    unmatched_gold = [gold_list[gi] for gi in range(len(gold_list)) if gi not in used_gold]

    return matches, unmatched_pred, unmatched_gold


# ---------------------------------------------------------------------------
# cut_f1
# ---------------------------------------------------------------------------


def cut_f1(
    predicted: Sequence[int],
    gold: Sequence[int],
    *,
    tol_frames: int,
) -> dict[str, float]:
    """F1 score for cut placement (frame-tolerance aware).

    Edge cases (no division by zero):
    - gold empty & pred empty  → precision=1.0, recall=1.0, f1=1.0
    - gold empty & pred>0      → precision=0.0, recall=1.0, f1=0.0
    - pred empty & gold>0      → precision=1.0, recall=0.0, f1=0.0
    """
    n_pred = len(list(predicted))
    n_gold = len(list(gold))

    if n_pred == 0 and n_gold == 0:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}

    if n_pred == 0:
        return {"precision": 1.0, "recall": 0.0, "f1": 0.0}

    if n_gold == 0:
        return {"precision": 0.0, "recall": 1.0, "f1": 0.0}

    matches, _, _ = match_cuts(predicted, gold, tol_frames=tol_frames)
    n_matches = len(matches)

    precision = n_matches / n_pred
    recall = n_matches / n_gold
    f1 = (
        2.0 * precision * recall / (precision + recall)
        if (precision + recall) > 0.0
        else 0.0
    )
    return {"precision": precision, "recall": recall, "f1": f1}


# ---------------------------------------------------------------------------
# boundary_offset
# ---------------------------------------------------------------------------


def boundary_offset(
    predicted: Sequence[int],
    gold: Sequence[int],
    *,
    tol_frames: int,
) -> dict[str, float]:
    """Frame-distance statistics over matched cut pairs.

    Returns:
        ``{mean_abs_offset, max_abs_offset}`` (in frames).
        No matches → both 0.0.
    """
    matches, _, _ = match_cuts(predicted, gold, tol_frames=tol_frames)
    if not matches:
        return {"mean_abs_offset": 0.0, "max_abs_offset": 0.0}

    offsets = [abs(pf - gf) for pf, gf in matches]
    return {
        "mean_abs_offset": float(np.mean(offsets)),
        "max_abs_offset": float(max(offsets)),
    }


# ---------------------------------------------------------------------------
# word_interruption_rate
# ---------------------------------------------------------------------------


def word_interruption_rate(cuts: Sequence[int], words: Sequence[Word]) -> float:
    """Fraction of cuts that bisect a word (lower is better; 0.0 = perfect).

    Uses ``editorial._covering_word`` directly. No cuts → 0.0.
    """
    cut_list = list(cuts)
    if not cut_list:
        return 0.0

    em = editorial_metrics(cut_list, list(words))
    return float(em["pct_mid_word"])


# ---------------------------------------------------------------------------
# audio_jump_score
# ---------------------------------------------------------------------------


def audio_jump_score(
    cuts: Sequence[int],
    rms: Sequence[float],
) -> dict[str, float]:
    """Audio discontinuity at cut points (lower is better).

    For each cut ``c``, computes ``|rms[c] - rms[c-1]|``.
    Index guards: ``c <= 0`` or ``c >= len(rms)`` → jump recorded as 0.0
    (the last valid index ``len(rms)-1`` IS measured normally when used as a cut).

    Returns:
        ``{mean_jump, max_jump, mean_jump_norm}``

        ``mean_jump_norm = mean_jump / std(rms)`` (std > 0). If std == 0 or
        rms is too short → 0.0. All values 0.0 when cuts or rms empty.
    """
    cut_list = list(cuts)
    rms_list = list(rms)

    if not cut_list or len(rms_list) < 2:
        return {"mean_jump": 0.0, "max_jump": 0.0, "mean_jump_norm": 0.0}

    n = len(rms_list)
    jumps: list[float] = []
    for c in cut_list:
        if c <= 0 or c >= n:
            # Edge guard — cut at/beyond boundary → no measurable jump
            jumps.append(0.0)
        else:
            jumps.append(abs(rms_list[c] - rms_list[c - 1]))

    mean_jump = float(np.mean(jumps))
    max_jump = float(max(jumps))

    rms_arr = np.array(rms_list, dtype=float)
    rms_std = float(np.std(rms_arr))
    mean_jump_norm = mean_jump / rms_std if rms_std > 0.0 else 0.0

    return {
        "mean_jump": mean_jump,
        "max_jump": max_jump,
        "mean_jump_norm": mean_jump_norm,
    }
