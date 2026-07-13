"""Schema tests for the v2 production board artifacts."""

import pytest
from pydantic import ValidationError

from laura.short_creator.board_models import (
    BestWindow,
    BoardMeta,
    Chapter,
    Cutlist,
    CutSegment,
    QaReport,
    Roi,
    SceneReview,
    Script,
    ScriptLine,
    Storyline,
)


def _review(**overrides):
    base = dict(
        scene_number=3,
        src_start_frame=100,
        src_end_frame_exclusive=220,
        description="Agent farm dashboard with running agents",
        whats_happening="cursor scrolls the agent list",
        hook_score=7,
        best_window=BestWindow(offset_s=1.0, duration_s=4.0),
        roi=Roi(x=0.1, y=0.2, w=0.5, h=0.4),
    )
    base.update(overrides)
    return SceneReview(**base)


def test_scene_review_roundtrip() -> None:
    r = _review()
    again = SceneReview.model_validate_json(
        r.model_dump_json()
    )
    assert again == r
    assert again.version == 1 and again.degraded is False


def test_scene_review_rejects_non_exclusive_frames() -> None:
    with pytest.raises(ValidationError):
        _review(src_end_frame_exclusive=100)


def test_roi_must_stay_inside_frame() -> None:
    with pytest.raises(ValidationError):
        Roi(x=0.8, y=0.0, w=0.5, h=0.5)
    with pytest.raises(ValidationError):
        Roi(x=0.0, y=0.0, w=0.0, h=0.5)


def test_hook_score_range() -> None:
    with pytest.raises(ValidationError):
        _review(hook_score=11)


def test_storyline_roles_are_closed_set() -> None:
    ok = Storyline(
        red_thread="one app runs your whole team",
        arc=[
            Chapter(
                chapter=1,
                role="hook",
                message="stop scrolling",
                scene_numbers=[1],
                target_seconds=3.0,
            )
        ],
    )
    assert ok.version == 1
    with pytest.raises(ValidationError):
        Chapter(
            chapter=1,
            role="outro",
            message="x",
            scene_numbers=[1],
            target_seconds=3.0,
        )


def test_cutlist_orders_must_be_contiguous() -> None:
    seg = dict(scene_number=1, start_frame=0, end_frame_exclusive=120, roi=None, zoom_start_s=None)
    with pytest.raises(ValidationError):
        Cutlist(segments=[CutSegment(order=0, **seg), CutSegment(order=2, **seg)])
    ok = Cutlist(segments=[CutSegment(order=0, **seg), CutSegment(order=1, **seg)])
    assert [s.order for s in ok.segments] == [0, 1]


def test_script_needs_lines() -> None:
    with pytest.raises(ValidationError):
        Script(language="de", lines=[])
    s = Script(language="de", lines=[ScriptLine(chapter=1, scene_number=1, text="Stopp!")])
    assert s.lines[0].text == "Stopp!"


def test_qa_report_verdict_literal() -> None:
    with pytest.raises(ValidationError):
        QaReport(verdict="maybe", findings=[])
    assert QaReport(verdict="ship", findings=[]).verdict == "ship"


def test_board_meta_defaults() -> None:
    m = BoardMeta(
        session_id="s1",
        asset_id="a1",
        created_utc="2026-07-13T12:00:00Z",
        task="overview short",
        target_seconds=60.0,
    )
    assert m.format == "insta" and m.status == "active"
