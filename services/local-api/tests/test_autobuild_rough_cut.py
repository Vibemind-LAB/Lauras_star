"""Smart handling: auto-build a rough cut + scenes after a successful analysis run.

After ``analysis.run`` succeeds, ``autobuild_asset_edit_ready`` lands the asset edit-ready
(rough-cut timeline filled from kept shots, grouped into scenes) with zero user clicks. The
build is idempotent and preserves existing scene edits; opt out via ``LAURA_AUTO_ROUGH_CUT=0``.
"""

from __future__ import annotations

from typing import Any

import pytest

from laura.analysis.handlers import _auto_rough_cut_enabled
from laura.db import repos
from laura.db.database import Database
from laura.scenes.build import autobuild_asset_edit_ready


def _seed(db: Database) -> tuple[str, str, str]:
    """Create a project + asset + analysis run. Returns (project_id, asset_id, run_id)."""
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="a",
        source_path="/tmp/a.mp4",
    )
    run = repos.create_analysis_run(db, asset_id=asset["id"], pipeline_version="t", config={})
    return str(project["id"]), str(asset["id"]), str(run["id"])


def _shot(src_in: int, src_out: int, *, keep: bool = True) -> dict[str, Any]:
    """A shot row mirroring the dict built in analysis/handlers._run_scene."""
    return {
        "src_in_frame": src_in,
        "src_out_frame_exclusive": src_out,
        "method": "adaptive",
        "confidence": None,
        "thumbnail_path": None,
        "black_ratio": None,
        "static_score": None,
        "phash": None,
        "blur_score": None,
        "keep": keep,
        "drop_reason": None if keep else "test",
    }


def test_autobuild_builds_rough_cut_and_scenes_from_kept_shots(db: Database) -> None:
    project_id, asset_id, run_id = _seed(db)
    # 3 kept shots + 1 dropped — only the kept ones must reach the timeline.
    repos.insert_shots(
        db,
        asset_id=asset_id,
        run_id=run_id,
        shots=[
            _shot(0, 100),
            _shot(100, 250),
            _shot(250, 250 + 90, keep=False),  # dropped
            _shot(340, 500),
        ],
    )

    count = autobuild_asset_edit_ready(db, project_id=project_id, asset_id=asset_id, run_id=run_id)

    assert count > 0
    timeline = repos.get_or_create_asset_rough_cut(db, project_id, asset_id)
    clips = repos.list_timeline_clips(db, timeline["id"])
    # Only the 3 kept shots became clips, gapless and in source order.
    assert [(c["src_in_frame"], c["src_out_frame_exclusive"]) for c in clips] == [
        (0, 100),
        (100, 250),
        (340, 500),
    ]
    assert [(c["seq_in_frame"], c["seq_out_frame_exclusive"]) for c in clips] == [
        (0, 100),
        (100, 250),
        (250, 410),
    ]
    scenes = repos.list_scenes(db, timeline["id"])
    assert len(scenes) == count >= 1


def test_autobuild_is_idempotent(db: Database) -> None:
    project_id, asset_id, run_id = _seed(db)
    repos.insert_shots(
        db,
        asset_id=asset_id,
        run_id=run_id,
        shots=[_shot(0, 100), _shot(100, 250)],
    )

    first = autobuild_asset_edit_ready(db, project_id=project_id, asset_id=asset_id, run_id=run_id)
    timeline = repos.get_or_create_asset_rough_cut(db, project_id, asset_id)
    clips_after_first = repos.list_timeline_clips(db, timeline["id"])

    second = autobuild_asset_edit_ready(db, project_id=project_id, asset_id=asset_id, run_id=run_id)
    clips_after_second = repos.list_timeline_clips(db, timeline["id"])

    # No duplicate clips, same scene count.
    assert len(clips_after_second) == len(clips_after_first)
    assert second == first


def test_autobuild_preserves_existing_scene_edits(db: Database) -> None:
    project_id, asset_id, run_id = _seed(db)
    repos.insert_shots(
        db,
        asset_id=asset_id,
        run_id=run_id,
        shots=[_shot(0, 100), _shot(100, 250), _shot(250, 400)],
    )
    timeline = repos.get_or_create_asset_rough_cut(db, project_id, asset_id)
    # Populate clips, then pre-insert a hand-tuned single-scene layout (a "user edit").
    from laura.scenes.build import populate_rough_cut_from_shots

    clips = populate_rough_cut_from_shots(db, timeline["id"], asset_id, run_id)
    full_range = [(clips[0]["seq_in_frame"], clips[-1]["seq_out_frame_exclusive"])]
    repos.replace_scenes(db, project_id, timeline["id"], full_range)
    before = repos.list_scenes(db, timeline["id"])
    assert len(before) == 1

    count = autobuild_asset_edit_ready(db, project_id=project_id, asset_id=asset_id, run_id=run_id)

    after = repos.list_scenes(db, timeline["id"])
    # Existing scenes are NOT re-grouped: same ids, same ranges, same count returned.
    assert count == 1
    assert [s["id"] for s in after] == [s["id"] for s in before]
    assert [(s["seq_in_frame"], s["seq_out_frame_exclusive"]) for s in after] == full_range


def test_autobuild_returns_zero_when_no_kept_shots(db: Database) -> None:
    project_id, asset_id, run_id = _seed(db)
    repos.insert_shots(
        db,
        asset_id=asset_id,
        run_id=run_id,
        shots=[_shot(0, 100, keep=False), _shot(100, 250, keep=False)],
    )

    count = autobuild_asset_edit_ready(db, project_id=project_id, asset_id=asset_id, run_id=run_id)

    assert count == 0
    timeline = repos.get_or_create_asset_rough_cut(db, project_id, asset_id)
    assert repos.list_timeline_clips(db, timeline["id"]) == []
    assert repos.list_scenes(db, timeline["id"]) == []


def test_auto_rough_cut_enabled_default_and_opt_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LAURA_AUTO_ROUGH_CUT", raising=False)
    assert _auto_rough_cut_enabled() is True
    monkeypatch.setenv("LAURA_AUTO_ROUGH_CUT", "0")
    assert _auto_rough_cut_enabled() is False
    monkeypatch.setenv("LAURA_AUTO_ROUGH_CUT", "off")
    assert _auto_rough_cut_enabled() is False


def test_autobuild_applies_editorial_placement_to_auto_cut(db: Database) -> None:
    """The zero-click auto-build refines a raw shot cut onto a transcript seam (the SAME joint
    placement the from-shots endpoint runs), not just the bare shot boundary. A word straddling the
    cut pulls it to the clean gap edge — proving the unified placement reaches the default path."""
    from laura.scenes.build import populate_rough_cut_from_shots

    project_id, asset_id, run_id = _seed(db)
    # Two source-contiguous shots sharing a cut at frame 50.
    repos.insert_shots(db, asset_id=asset_id, run_id=run_id, shots=[_shot(0, 50), _shot(50, 120)])
    # A word [48,60) straddles the cut at 50 -> editorial placement pulls the cut to the edge 48.
    repos.insert_segment_with_words(
        db,
        asset_id=asset_id,
        run_id=run_id,
        speaker_id=None,
        segment={
            "start_sample": 0,
            "end_sample": 1,
            "start_frame": 48,
            "end_frame": 60,
            "text": "straddle",
        },
        words=[
            {
                "idx": 0,
                "start_sample": 0,
                "end_sample": 1,
                "start_frame": 48,
                "end_frame": 60,
                "text": "straddle",
            }
        ],
    )
    timeline = repos.get_or_create_asset_rough_cut(db, project_id, asset_id)
    clips = populate_rough_cut_from_shots(db, timeline["id"], asset_id, run_id)
    # The shared cut moved off frame 50 (mid-word) onto the edge 48; first clip's start untouched.
    assert [(c["src_in_frame"], c["src_out_frame_exclusive"]) for c in clips] == [
        (0, 48),
        (48, 120),
    ]
    assert [(c["seq_in_frame"], c["seq_out_frame_exclusive"]) for c in clips] == [
        (0, 48),
        (48, 120),
    ]


def test_autobuild_editorial_placement_opt_out(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``LAURA_EDITORIAL_AUTOCUT=0`` lands the raw shot boundaries (pre-unification behaviour)."""
    from laura.scenes.build import populate_rough_cut_from_shots

    monkeypatch.setenv("LAURA_EDITORIAL_AUTOCUT", "0")
    project_id, asset_id, run_id = _seed(db)
    repos.insert_shots(db, asset_id=asset_id, run_id=run_id, shots=[_shot(0, 50), _shot(50, 120)])
    repos.insert_segment_with_words(
        db,
        asset_id=asset_id,
        run_id=run_id,
        speaker_id=None,
        segment={
            "start_sample": 0,
            "end_sample": 1,
            "start_frame": 48,
            "end_frame": 60,
            "text": "straddle",
        },
        words=[
            {
                "idx": 0,
                "start_sample": 0,
                "end_sample": 1,
                "start_frame": 48,
                "end_frame": 60,
                "text": "straddle",
            }
        ],
    )
    timeline = repos.get_or_create_asset_rough_cut(db, project_id, asset_id)
    clips = populate_rough_cut_from_shots(db, timeline["id"], asset_id, run_id)
    assert [(c["src_in_frame"], c["src_out_frame_exclusive"]) for c in clips] == [
        (0, 50),
        (50, 120),
    ]
