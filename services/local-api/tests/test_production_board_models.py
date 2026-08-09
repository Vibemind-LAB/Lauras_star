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
    SceneWindowRef,
    Script,
    ScriptLine,
    Storyline,
    VisualBeatPlan,
    VisualPlan,
    VisualSceneCandidate,
    VisualSceneChoice,
    VisualShotCandidate,
    as_scene_window,
)


def _review(**overrides: object) -> SceneReview:
    base: dict[str, object] = dict(
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
    return SceneReview(**base)  # type: ignore[arg-type]


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
            role="outro",  # type: ignore[arg-type]
            message="x",
            scene_numbers=[1],
            target_seconds=3.0,
        )


def test_cutlist_orders_must_be_contiguous() -> None:
    seg: dict[str, object] = dict(
        scene_number=1, start_frame=0, end_frame_exclusive=120, roi=None, zoom_start_s=None
    )
    with pytest.raises(ValidationError):
        Cutlist(
            segments=[CutSegment(order=0, **seg), CutSegment(order=2, **seg)]  # type: ignore[arg-type]
        )
    ok = Cutlist(
        segments=[CutSegment(order=0, **seg), CutSegment(order=1, **seg)]  # type: ignore[arg-type]
    )
    assert [s.order for s in ok.segments] == [0, 1]


def test_script_needs_lines() -> None:
    with pytest.raises(ValidationError):
        Script(language="de", lines=[])
    s = Script(language="de", lines=[ScriptLine(chapter=1, scene_number=1, text="Stopp!")])
    assert s.lines[0].text == "Stopp!"


def test_qa_report_verdict_literal() -> None:
    with pytest.raises(ValidationError):
        QaReport(verdict="maybe", findings=[])  # type: ignore[arg-type]
    assert QaReport(verdict="ship", findings=[]).verdict == "ship"


# Verbatim shape of a pre-windows board review file (workspace livetest schema rebuilt as a
# fixture — no `windows` list, no per-window roi): these MUST keep validating.
_OLD_REVIEW_JSON = """
{
  "scene_number": 13,
  "src_start_frame": 4980,
  "src_end_frame_exclusive": 7260,
  "description": "terminal with running agents",
  "whats_happening": "logs scroll while a job finishes",
  "hook_score": 7,
  "best_window": {"offset_s": 12.0, "duration_s": 6.0},
  "roi": {"x": 0.05, "y": 0.4, "w": 0.55, "h": 0.3},
  "legibility_notes": "small mono font",
  "degraded": false,
  "model": "OllamaDescribeBackend",
  "version": 2,
  "created_utc": "2026-07-14T18:22:31Z"
}
"""


def test_scene_review_windows_default_to_best_window() -> None:
    r = _review()
    assert r.windows == [r.best_window]
    again = SceneReview.model_validate_json(r.model_dump_json())
    assert again.windows == [again.best_window]


def test_old_review_json_without_windows_still_validates() -> None:
    r = SceneReview.model_validate_json(_OLD_REVIEW_JSON)
    assert r.windows == [BestWindow(offset_s=12.0, duration_s=6.0)]
    assert r.best_window.roi is None
    assert r.roi is not None and r.roi.w == 0.55


def test_scene_review_windows_first_must_equal_best_window() -> None:
    with pytest.raises(ValidationError):
        _review(windows=[BestWindow(offset_s=9.0, duration_s=1.0)])


def test_scene_review_windows_must_not_overlap() -> None:
    w0 = BestWindow(offset_s=1.0, duration_s=4.0)
    with pytest.raises(ValidationError):
        _review(best_window=w0, windows=[w0, BestWindow(offset_s=3.0, duration_s=2.0)])
    ok = _review(best_window=w0, windows=[w0, BestWindow(offset_s=5.0, duration_s=2.0)])
    assert len(ok.windows) == 2  # touching (end == next start) is fine, end-exclusive analog


def test_best_window_roi_optional_and_validated() -> None:
    w = BestWindow(offset_s=0.0, duration_s=2.0, roi=Roi(x=0.1, y=0.1, w=0.3, h=0.3))
    assert w.roi is not None
    with pytest.raises(ValidationError):
        BestWindow(offset_s=0.0, duration_s=2.0, roi=Roi(x=0.8, y=0.0, w=0.5, h=0.5))


def test_chapter_scene_numbers_accept_window_refs() -> None:
    c = Chapter(
        chapter=1,
        role="hook",
        message="m",
        scene_numbers=[1, {"scene": 1, "window": 1}],  # type: ignore[list-item]
        target_seconds=3.0,
    )
    assert c.scene_numbers[0] == 1
    assert c.scene_numbers[1] == SceneWindowRef(scene=1, window=1)
    assert as_scene_window(c.scene_numbers[0]) == (1, 0)
    assert as_scene_window(c.scene_numbers[1]) == (1, 1)


def test_storyline_rejects_duplicate_scene_window_pairs() -> None:
    def chapter(n: int, scenes: list[object]) -> Chapter:
        return Chapter(
            chapter=n,
            role="hook" if n == 1 else "payoff_cta",
            message="m",
            scene_numbers=scenes,  # type: ignore[arg-type]
            target_seconds=3.0,
        )

    # Same (scene, window) pair twice inside one chapter — via both notations.
    with pytest.raises(ValidationError) as exc:
        Storyline(red_thread="rt", arc=[chapter(1, [1, {"scene": 1, "window": 0}])])
    assert "scene 1 window 0" in str(exc.value)

    # Same pair across chapters is just as forbidden.
    with pytest.raises(ValidationError):
        Storyline(red_thread="rt", arc=[chapter(1, [2]), chapter(2, [2])])

    # Same scene with a DIFFERENT window is the whole point — allowed.
    ok = Storyline(
        red_thread="rt", arc=[chapter(1, [1, {"scene": 1, "window": 1}]), chapter(2, [2])]
    )
    assert len(ok.arc) == 2


def test_board_meta_defaults() -> None:
    m = BoardMeta(
        session_id="s1",
        asset_id="a1",
        created_utc="2026-07-13T12:00:00Z",
        task="overview short",
        target_seconds=60.0,
    )
    assert m.format == "insta" and m.status == "active"


def test_v2_visual_plan_rejects_mixed_beat_and_rough_cut_representations() -> None:
    candidate = VisualSceneCandidate(
        candidate_id="candidate-0",
        rough_cut_order=0,
        scene_number=1,
        window_index=0,
        src_start_frame=0,
        src_end_frame_exclusive=120,
        thumb_frame=60,
        max_duration_s=5,
        description="clear interface",
        transcript_snippet="the narrated step",
        rationale="fits the rough-cut row",
        score=0.9,
    )
    choice = VisualSceneChoice(
        rough_cut_order=0,
        scene_number=1,
        description="clear interface",
        transcript="the narrated step",
        rationale="fits the rough-cut row",
        candidates=[candidate],
        recommended_candidate_id=candidate.candidate_id,
        recommended_included=True,
        recommended_duration_s=5,
    )

    with pytest.raises(ValidationError, match="exactly one representation"):
        VisualPlan(
            version=2,
            proposal_hash="a" * 64,
            request_hash="b" * 64,
            beats=[
                VisualBeatPlan(
                    beat_id="beat-0",
                    voice_segment_index=0,
                    narration_text="the narrated step",
                    duration_s=5.0,
                    candidates=[
                        VisualShotCandidate(
                            candidate_id="beat-candidate-0",
                            beat_id="beat-0",
                            voice_segment_index=0,
                            scene_number=1,
                            window_index=0,
                            src_start_frame=0,
                            src_end_frame_exclusive=120,
                            thumb_frame=60,
                            description="clear interface",
                            transcript_snippet="the narrated step",
                            rationale="fits the beat",
                            score=0.9,
                        )
                    ],
                    recommended_candidate_id="beat-candidate-0",
                )
            ],
            scene_choices=[choice],
            voice_total_frames=120,
            fps=24.0,
        )
