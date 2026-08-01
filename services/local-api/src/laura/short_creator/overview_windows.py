"""Transcript hits -> short candidate windows for the auto-overview (spec
2026-07-31-auto-overview-design.md §3).

Pure: no DB, no agent, no clock. Scene bounds and frame rates are passed in, so every rule
here is testable in isolation — and the auto-overview endpoint can resolve everything BEFORE
it writes a single row.

All arithmetic is in integer frames, end-exclusive (CLAUDE.md invariants 1 + 2). The seconds
below are constants converted through each asset's own rate; they never become state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Padding keeps a window from cutting the first/last word of the matched segment.
_PAD_S = 1.0
# Two windows closer than this read as one thought — join them instead of hard-cutting twice.
_MERGE_GAP_S = 1.5
# Below this a clip is a blip, not a statement.
_MIN_S = 4.0
# Above this one source starts dominating the overview.
_MAX_S = 20.0
# How much longer than target_seconds the final cut may run.
_TARGET_TOLERANCE = 1.2


@dataclass(frozen=True)
class Candidate:
    """One watchable window: a source-frame range of ONE asset, inside ONE of its scenes."""

    asset_id: str
    display_name: str
    scene_number: int
    start_frame: int
    end_frame_exclusive: int
    snippet: str

    @property
    def length_frames(self) -> int:
        return self.end_frame_exclusive - self.start_frame


def _frames(seconds: float, fps_num: int, fps_den: int) -> int:
    """Seconds -> frames at ``fps_num/fps_den``, rounded to the nearest frame.

    ROUND (not floor) so 29.97 gives the same 30-frame second a viewer expects.
    """
    return int(round(seconds * fps_num / fps_den))


def _fps(fps_by_asset: dict[str, tuple[int, int]], asset_id: str) -> tuple[int, int]:
    """Look up *asset_id*'s frame rate, or raise.

    A missing entry here is a CALLER bug, not a race: the auto-overview endpoint builds
    ``fps_by_asset`` from the same ranking it passes to :func:`build_candidates`, with a
    project-rate fallback per asset, so every asset id in *ranking* is always present.
    Silently defaulting (e.g. to 25fps) would apply the wrong rate to the padding,
    merge-gap and length math for that asset without anyone noticing -- the exact class of
    bug that cuts a clip mid-word. Loud beats silent here.

    Contrast this with the missing ``scene_bounds`` entry in :func:`build_candidates`,
    which IS a race (the ranking and the bounds are read separately, so a scene can vanish
    between the two reads) and is therefore a deliberate drop, not a raise.
    """
    try:
        return fps_by_asset[asset_id]
    except KeyError:
        raise ValueError(f"no frame rate for asset {asset_id!r}") from None


def build_candidates(
    ranking: list[dict[str, Any]],
    *,
    scene_bounds: dict[tuple[str, int], tuple[int, int]],
    fps_by_asset: dict[str, tuple[int, int]],
) -> list[Candidate]:
    """``search_material``'s ranking -> ordered candidate windows.

    Order is the deterministic one the fallback relies on: assets in ranking order (by search
    score), chronological within an asset.

    A hit whose ``(asset_id, scene_number)`` has no entry in *scene_bounds* is dropped, not
    raised on — the ranking and the bounds are read separately, and a scene can disappear
    between the two reads.
    """
    out: list[Candidate] = []
    for entry in ranking:
        asset_id = str(entry["asset_id"])
        display_name = str(entry.get("display_name") or "")
        fps_num, fps_den = _fps(fps_by_asset, asset_id)
        pad = _frames(_PAD_S, fps_num, fps_den)
        merge_gap = _frames(_MERGE_GAP_S, fps_num, fps_den)
        min_len = _frames(_MIN_S, fps_num, fps_den)
        max_len = _frames(_MAX_S, fps_num, fps_den)

        padded: list[tuple[int, int, int, str]] = []  # (scene, start, end_excl, snippet)
        for hit in entry["scene_hits"]:
            scene_number = int(hit["scene_number"])
            bounds = scene_bounds.get((asset_id, scene_number))
            if bounds is None:
                continue
            lo, hi = bounds
            start = max(lo, int(hit["start_frame"]) - pad)
            end = min(hi, int(hit["end_frame_exclusive"]) + pad)
            if end <= start:
                continue
            padded.append((scene_number, start, end, str(hit.get("snippet") or "")))

        # Merge within the same scene only: a merged window must stay inside one scene's
        # bounds, which is exactly what the clamp above guarantees per scene.
        padded.sort(key=lambda p: (p[0], p[1]))
        merged: list[list[Any]] = []
        for scene_number, start, end, snippet in padded:
            if merged and merged[-1][0] == scene_number and start - merged[-1][2] < merge_gap:
                merged[-1][2] = max(merged[-1][2], end)
                continue
            merged.append([scene_number, start, end, snippet])

        for scene_number, start, end, snippet in merged:
            length = end - start
            if length < min_len:
                continue
            if length > max_len:
                end = start + max_len  # cut at the end; the start carries the cue
            out.append(
                Candidate(
                    asset_id=asset_id,
                    display_name=display_name,
                    scene_number=scene_number,
                    start_frame=start,
                    end_frame_exclusive=end,
                    snippet=snippet,
                )
            )
    return out


def duration_seconds(
    candidates: list[Candidate], *, fps_by_asset: dict[str, tuple[int, int]]
) -> float:
    """Total seconds of *candidates*, each converted at its own asset's rate."""
    total = 0.0
    for candidate in candidates:
        fps_num, fps_den = _fps(fps_by_asset, candidate.asset_id)
        total += candidate.length_frames * fps_den / fps_num
    return total


def trim_to_target(
    candidates: list[Candidate],
    *,
    target_seconds: int,
    fps_by_asset: dict[str, tuple[int, int]],
) -> list[Candidate]:
    """Keep candidates from the front until ``target_seconds * 1.2`` is exceeded.

    Cutting from the END preserves the chosen opening — the scout put its strongest clip
    first, and an overview that loses its own opening is worse than one that runs short.
    """
    budget = target_seconds * _TARGET_TOLERANCE
    kept: list[Candidate] = []
    for candidate in candidates:
        if duration_seconds([*kept, candidate], fps_by_asset=fps_by_asset) > budget:
            break
        kept.append(candidate)
    return kept
