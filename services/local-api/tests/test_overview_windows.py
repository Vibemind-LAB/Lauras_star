"""Candidate windows: transcript hits -> short, clamped, merged clips (spec
2026-07-31-auto-overview-design.md §3). Pure functions — no DB, no agent."""

from __future__ import annotations

from typing import Any

import pytest

from laura.short_creator.overview_windows import (
    Candidate,
    build_candidates,
    duration_seconds,
    trim_to_target,
)

FPS = {"a": (30, 1), "b": (30, 1)}
# One scene per asset, generous bounds unless a test overrides them.
BOUNDS = {("a", 1): (0, 100_000), ("b", 1): (0, 100_000)}


def _ranking(asset_id: str, name: str, hits: list[tuple[int, int]]) -> dict[str, Any]:
    return {
        "asset_id": asset_id,
        "display_name": name,
        "score": 1.0,
        "scene_hits": [
            {
                "scene_number": 1,
                "snippet": f"hit {start}",
                "score": 1.0,
                "start_frame": start,
                "end_frame_exclusive": end,
            }
            for start, end in hits
        ],
    }


def test_pads_by_one_second_on_both_sides() -> None:
    out = build_candidates(
        [_ranking("a", "A", [(300, 450)])], scene_bounds=BOUNDS, fps_by_asset=FPS
    )
    assert len(out) == 1
    # 30fps -> 30 frames of padding each side.
    assert out[0].start_frame == 270
    assert out[0].end_frame_exclusive == 480


def test_clamps_to_the_scene_bounds() -> None:
    """A window never leaves the scene its hit was mapped into."""
    out = build_candidates(
        [_ranking("a", "A", [(10, 200)])],
        scene_bounds={("a", 1): (5, 205)},
        fps_by_asset=FPS,
    )
    assert out[0].start_frame == 5
    assert out[0].end_frame_exclusive == 205


def test_merges_overlapping_and_near_neighbours_of_the_same_asset() -> None:
    # Two hits 30 frames (1.0s) apart after padding -> below the 1.5s merge gap.
    out = build_candidates(
        [_ranking("a", "A", [(300, 400), (460, 560)])],
        scene_bounds=BOUNDS,
        fps_by_asset=FPS,
    )
    assert len(out) == 1
    assert out[0].start_frame == 270
    assert out[0].end_frame_exclusive == 590


def test_does_not_merge_across_assets() -> None:
    out = build_candidates(
        [_ranking("a", "A", [(300, 400)]), _ranking("b", "B", [(300, 400)])],
        scene_bounds=BOUNDS,
        fps_by_asset=FPS,
    )
    assert len(out) == 2
    assert {c.asset_id for c in out} == {"a", "b"}


def test_drops_windows_below_the_minimum() -> None:
    """4.0s floor: a 1.0s hit padded to 3.0s is still too short to watch."""
    out = build_candidates(
        [_ranking("a", "A", [(300, 330)])], scene_bounds=BOUNDS, fps_by_asset=FPS
    )
    assert out == []


def test_caps_windows_at_the_maximum() -> None:
    """20.0s ceiling, cut at the END so the window keeps its start."""
    out = build_candidates(
        [_ranking("a", "A", [(1000, 3000)])], scene_bounds=BOUNDS, fps_by_asset=FPS
    )
    assert out[0].start_frame == 970
    assert out[0].end_frame_exclusive == 970 + 600  # 20s * 30fps


def test_non_integer_frame_rate_rounds_the_padding() -> None:
    """29.97 (30000/1001): 1.0s of padding is 30 frames, not 29 or 31."""
    out = build_candidates(
        [_ranking("a", "A", [(300, 450)])],
        scene_bounds=BOUNDS,
        fps_by_asset={"a": (30000, 1001)},
    )
    assert out[0].start_frame == 270
    assert out[0].end_frame_exclusive == 480


def test_unknown_scene_bounds_drop_the_hit_instead_of_raising() -> None:
    out = build_candidates(
        [_ranking("a", "A", [(300, 450)])], scene_bounds={}, fps_by_asset=FPS
    )
    assert out == []


def test_never_merges_across_scenes_even_when_the_gap_is_small() -> None:
    """Two hits of the SAME asset in DIFFERENT scenes must never merge, even when the
    padded gap between them is well under the 1.5s merge gap. Merging across scenes would
    let the merged window escape its own scene's clamp -- exactly the bug the
    ``merged[-1][0] == scene_number`` guard exists to prevent."""
    bounds = {("a", 1): (0, 500), ("a", 2): (500, 1000)}
    ranking = [
        {
            "asset_id": "a",
            "display_name": "A",
            "score": 1.0,
            "scene_hits": [
                {
                    "scene_number": 1,
                    "snippet": "end of scene one",
                    "score": 1.0,
                    "start_frame": 400,
                    "end_frame_exclusive": 490,
                },
                {
                    "scene_number": 2,
                    "snippet": "start of scene two",
                    "score": 1.0,
                    "start_frame": 510,
                    "end_frame_exclusive": 600,
                },
            ],
        }
    ]

    out = build_candidates(ranking, scene_bounds=bounds, fps_by_asset=FPS)

    # If the scene guard were missing, the 0-frame gap between the padded windows (both
    # clamp to the shared 500 boundary) is well under the 45-frame (1.5s) merge gap, and
    # they'd fuse into one candidate spanning 370-630 -- 130 frames past scene 1's own
    # bound of 500.
    assert len(out) == 2
    assert [c.scene_number for c in out] == [1, 2]
    scene_one, scene_two = out
    assert scene_one.start_frame == 370
    assert scene_one.end_frame_exclusive == 500
    assert scene_two.start_frame == 500
    assert scene_two.end_frame_exclusive == 630
    # Each candidate stays inside its own scene's bounds.
    assert bounds[("a", 1)][0] <= scene_one.start_frame
    assert scene_one.end_frame_exclusive <= bounds[("a", 1)][1]
    assert bounds[("a", 2)][0] <= scene_two.start_frame
    assert scene_two.end_frame_exclusive <= bounds[("a", 2)][1]


def test_missing_fps_entry_raises_naming_the_asset() -> None:
    """A missing ``fps_by_asset`` entry is a caller bug, not a race: the auto-overview
    endpoint builds that map from the same ranking it hands to ``build_candidates`` (with a
    project-rate fallback per asset), so every asset id is always present. Silently
    defaulting to 25fps would mis-size the pad/merge-gap/length math for that asset --
    exactly the class of bug that cuts a word in half -- so this must fail loudly instead."""
    with pytest.raises(ValueError) as exc_info:
        build_candidates(
            [_ranking("missing-asset", "M", [(300, 450)])],
            scene_bounds={("missing-asset", 1): (0, 100_000)},
            fps_by_asset=FPS,  # "missing-asset" is not a key here
        )
    assert "missing-asset" in str(exc_info.value)


def test_trim_to_target_drops_from_the_end() -> None:
    made = [
        Candidate("a", "A", 1, 0, 300, "one"),    # 10s
        Candidate("b", "B", 1, 0, 300, "two"),    # 10s
        Candidate("a", "A", 1, 600, 900, "three"),  # 10s
    ]
    kept = trim_to_target(made, target_seconds=20, fps_by_asset=FPS)
    # 20s target + 20% tolerance = 24s -> two clips fit, the third does not.
    assert [c.snippet for c in kept] == ["one", "two"]


def test_duration_seconds_sums_across_assets() -> None:
    made = [Candidate("a", "A", 1, 0, 300, "one"), Candidate("b", "B", 1, 0, 150, "two")]
    assert duration_seconds(made, fps_by_asset=FPS) == 15.0
