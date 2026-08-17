"""Pure, deterministic candidate generation for a visual-only recut."""

from __future__ import annotations

from math import ceil

import pytest

from laura.short_creator.board_models import (
    BestWindow,
    SceneReview,
    ScriptLine,
    VisualPlan,
    VisualRecutRequest,
    VisualSceneSelection,
    VisualShotCandidate,
    VoiceArtifact,
    VoiceSegment,
)
from laura.short_creator.visual_candidates import (
    InsufficientVisualCandidates,
    SceneMaterial,
    TranscriptSpan,
    _recommend,
    build_rough_cut_visual_plan,
    build_visual_plan,
    coverage_windows,
)
from laura.short_creator.visual_timeline import (
    apply_scene_selections,
    resolve_selected_shots,
)
from laura.short_creator.voice_concat import INTER_SCENE_GAP_S

_HASH_A = "a" * 64
_HASH_B = "b" * 64
_FPS = 30.0


def degraded_scene(*, start: int, end_exclusive: int, fps: float) -> SceneMaterial:
    del fps
    review = SceneReview(
        scene_number=1,
        src_start_frame=start,
        src_end_frame_exclusive=end_exclusive,
        description="unavailable visual review",
        whats_happening="transcript-only fallback",
        hook_score=5,
        best_window=BestWindow(offset_s=0.0, duration_s=4.0),
        degraded=True,
    )
    return SceneMaterial(
        scene_number=1,
        src_start_frame=start,
        src_end_frame_exclusive=end_exclusive,
        description="organizing files on a desktop",
        transcript="organizing files and drafting emails",
        transcript_spans=(),
        review=review,
    )


def scene_with_span(text: str, *, start_frame: int, end_frame_exclusive: int) -> SceneMaterial:
    return SceneMaterial(
        scene_number=1,
        src_start_frame=0,
        src_end_frame_exclusive=3600,
        description="desktop workflow",
        transcript=text,
        transcript_spans=(
            TranscriptSpan(
                start_frame=start_frame,
                end_frame_exclusive=end_frame_exclusive,
                text=text,
            ),
        ),
        review=None,
    )


def request() -> VisualRecutRequest:
    return VisualRecutRequest(
        user_request="Choose distributed visual moments without changing narration.",
        script_version=1,
        script_hash=_HASH_A,
        voice_version=1,
        voice_hash=_HASH_B,
    )


def lines() -> list[ScriptLine]:
    return [
        ScriptLine(chapter=1, scene_number=1, text="Organizing files makes the desktop calm."),
        ScriptLine(chapter=2, scene_number=2, text="Drafting emails keeps the work moving."),
        ScriptLine(chapter=3, scene_number=3, text="Organizing files and emails saves time."),
    ]


def voice() -> VoiceArtifact:
    script_lines = lines()
    durations = [3.0, 3.2, 2.8]
    return VoiceArtifact(
        script_hash=_HASH_A,
        mp3_path="voice.mp3",
        voice_s=sum(durations),
        segments=[
            VoiceSegment(
                scene_number=line.scene_number,
                chapter=line.chapter,
                line_hash=f"line-{index}",
                mp3_path=f"voice-{index}.mp3",
                duration_s=duration,
                offset_s=float(index) * 3.5,
            )
            for index, (line, duration) in enumerate(zip(script_lines, durations, strict=True))
        ],
    )


def scenes() -> list[SceneMaterial]:
    return [
        SceneMaterial(
            scene_number=1,
            src_start_frame=0,
            src_end_frame_exclusive=900,
            description="organizing files and drafting emails on a desktop",
            transcript="organizing files and drafting emails",
            transcript_spans=(
                TranscriptSpan(0, 180, "organizing files"),
                TranscriptSpan(360, 540, "drafting emails"),
            ),
            review=SceneReview(
                scene_number=1,
                src_start_frame=0,
                src_end_frame_exclusive=900,
                description="desktop organizer",
                whats_happening="files and email workflow",
                hook_score=9,
                best_window=BestWindow(offset_s=0.0, duration_s=5.0),
            ),
        ),
        SceneMaterial(
            scene_number=2,
            src_start_frame=900,
            src_end_frame_exclusive=1800,
            description="organizing files and drafting emails on a desktop",
            transcript="organizing files and drafting emails",
            transcript_spans=(
                TranscriptSpan(900, 1080, "organizing files"),
                TranscriptSpan(1260, 1440, "drafting emails"),
            ),
            review=SceneReview(
                scene_number=2,
                src_start_frame=900,
                src_end_frame_exclusive=1800,
                description="email workflow",
                whats_happening="files and email workflow",
                hook_score=8,
                best_window=BestWindow(offset_s=0.0, duration_s=5.0),
            ),
        ),
    ]


def rough_cut_scenes(count: int) -> list[SceneMaterial]:
    return [
        SceneMaterial(
            scene_number=index + 1,
            src_start_frame=index * 600,
            src_end_frame_exclusive=(index + 1) * 600,
            description=f"rough-cut scene {index + 1}",
            transcript=f"workflow step {index + 1}",
            transcript_spans=(),
            review=None,
        )
        for index in range(count)
    ]


def scored_rough_cut_scenes(
    hook_scores: list[int],
    *,
    duration_frames: int = 300,
) -> list[SceneMaterial]:
    duration_s = duration_frames / _FPS
    return [
        SceneMaterial(
            scene_number=index + 1,
            src_start_frame=index * duration_frames,
            src_end_frame_exclusive=(index + 1) * duration_frames,
            description="unrelated visual",
            transcript="",
            transcript_spans=(),
            review=SceneReview(
                scene_number=index + 1,
                src_start_frame=index * duration_frames,
                src_end_frame_exclusive=(index + 1) * duration_frames,
                description="unrelated visual",
                whats_happening="unrelated visual",
                hook_score=hook_score,
                best_window=BestWindow(offset_s=0.0, duration_s=duration_s),
            ),
        )
        for index, hook_score in enumerate(hook_scores)
    ]


def clamped_review_scenes(count: int) -> list[SceneMaterial]:
    return [
        SceneMaterial(
            scene_number=index + 1,
            src_start_frame=index * 300,
            src_end_frame_exclusive=(index + 1) * 300,
            description="clamped review window",
            transcript="",
            transcript_spans=(),
            review=SceneReview(
                scene_number=index + 1,
                src_start_frame=index * 300,
                src_end_frame_exclusive=(index + 1) * 300,
                description="clamped review window",
                whats_happening="same frame range at both frame rates",
                hook_score=5,
                best_window=BestWindow(offset_s=0.0, duration_s=20.0),
            ),
        )
        for index in range(count)
    ]


def recommended_selections(plan: VisualPlan) -> list[VisualSceneSelection]:
    return [
        VisualSceneSelection(
            rough_cut_order=choice.rough_cut_order,
            candidate_id=choice.recommended_candidate_id,
            included=choice.recommended_included,
            requested_duration_s=choice.recommended_duration_s,
        )
        for choice in plan.scene_choices
    ]


def test_every_rough_cut_scene_appears_once_in_order() -> None:
    plan = build_rough_cut_visual_plan(
        request=request(),
        scenes=rough_cut_scenes(8),
        narration_text="organize files and draft mail",
        voice_total_frames=1350,
        fps=30.0,
    )

    assert [choice.rough_cut_order for choice in plan.scene_choices] == list(range(8))
    assert [choice.scene_number for choice in plan.scene_choices] == list(range(1, 9))


def test_proposal_hash_binds_voice_frames_and_canonical_fps() -> None:
    scenes = rough_cut_scenes(8)
    base = build_rough_cut_visual_plan(
        request=request(),
        scenes=scenes,
        narration_text="organize files and draft mail",
        voice_total_frames=1200,
        fps=30.0,
    )
    changed_frames = build_rough_cut_visual_plan(
        request=request(),
        scenes=scenes,
        narration_text="organize files and draft mail",
        voice_total_frames=1199,
        fps=30.0,
    )
    canonical_equivalent_fps = build_rough_cut_visual_plan(
        request=request(),
        scenes=scenes,
        narration_text="organize files and draft mail",
        voice_total_frames=1200,
        fps=30,
    )
    fps_scenes = clamped_review_scenes(3)
    fps_30 = build_rough_cut_visual_plan(
        request=request(),
        scenes=fps_scenes,
        narration_text="same narration",
        voice_total_frames=90,
        fps=30.0,
    )
    fps_2997 = build_rough_cut_visual_plan(
        request=request(),
        scenes=fps_scenes,
        narration_text="same narration",
        voice_total_frames=90,
        fps=29.97,
    )

    assert base.proposal_hash != changed_frames.proposal_hash
    assert base.proposal_hash == canonical_equivalent_fps.proposal_hash
    assert fps_30.proposal_hash != fps_2997.proposal_hash

    confirmed_base = apply_scene_selections(
        base,
        recommended_selections(base),
        "2026-08-09T12:00:00Z",
    )
    confirmed_changed_frames = apply_scene_selections(
        changed_frames,
        recommended_selections(changed_frames),
        "2026-08-09T12:00:00Z",
    )
    assert confirmed_base.selection_hash != confirmed_changed_frames.selection_hash
    assert resolve_selected_shots(confirmed_base)[-1].final_frames == 300
    assert resolve_selected_shots(confirmed_changed_frames)[-1].final_frames == 299


def test_subset_recommendation_uses_score_then_rough_cut_order() -> None:
    plan = build_rough_cut_visual_plan(
        request=request(),
        scenes=scored_rough_cut_scenes([1, 10, 3, 10, 10]),
        narration_text="relevant narration",
        voice_total_frames=90,
        fps=_FPS,
    )

    assert [
        choice.rough_cut_order
        for choice in plan.scene_choices
        if choice.recommended_included
    ] == [1, 3, 4]
    assert [choice.rough_cut_order for choice in plan.scene_choices] == [0, 1, 2, 3, 4]


@pytest.mark.parametrize(
    ("voice_total_frames", "expected_included"),
    [(90, 3), (96, 3), (120, 4)],
)
def test_recommended_one_second_scene_baseline_is_frame_exact_and_confirmable(
    voice_total_frames: int,
    expected_included: int,
) -> None:
    plan = build_rough_cut_visual_plan(
        request=request(),
        scenes=scored_rough_cut_scenes([5] * 4),
        narration_text="relevant narration",
        voice_total_frames=voice_total_frames,
        fps=_FPS,
    )

    assert sum(choice.recommended_included for choice in plan.scene_choices) == expected_included
    confirmed = apply_scene_selections(
        plan,
        recommended_selections(plan),
        "2026-08-09T12:00:00Z",
    )
    assert (
        sum(shot.final_frames for shot in resolve_selected_shots(confirmed))
        == voice_total_frames
    )


@pytest.mark.parametrize(
    ("scene_count", "voice_total_frames"),
    [(2, 60), (4, 89)],
)
def test_plan_rejects_inputs_that_cannot_keep_three_one_second_scenes(
    scene_count: int,
    voice_total_frames: int,
) -> None:
    with pytest.raises(InsufficientVisualCandidates):
        build_rough_cut_visual_plan(
            request=request(),
            scenes=scored_rough_cut_scenes([5] * scene_count),
            narration_text="relevant narration",
            voice_total_frames=voice_total_frames,
            fps=_FPS,
        )


def test_one_second_subset_expansion_is_directly_confirmable() -> None:
    plan = build_rough_cut_visual_plan(
        request=request(),
        scenes=scored_rough_cut_scenes([5] * 8, duration_frames=30),
        narration_text="relevant narration",
        voice_total_frames=210,
        fps=_FPS,
    )

    assert sum(choice.recommended_included for choice in plan.scene_choices) == 7
    confirmed = apply_scene_selections(
        plan,
        recommended_selections(plan),
        "2026-08-09T12:00:00Z",
    )
    assert sum(shot.final_frames for shot in resolve_selected_shots(confirmed)) == 210


def test_long_degraded_scene_offers_distributed_windows() -> None:
    plan = build_rough_cut_visual_plan(
        request=request(),
        scenes=[
            degraded_scene(start=0, end_exclusive=3533, fps=_FPS),
            *rough_cut_scenes(3)[1:],
        ],
        narration_text="Rowboat UI",
        voice_total_frames=300,
        fps=30.0,
    )

    starts = [candidate.src_start_frame for candidate in plan.scene_choices[0].candidates]
    assert len(starts) == 4
    assert starts[0] == 0
    assert any(start >= 1766 for start in starts)
    assert all(candidate.description for candidate in plan.scene_choices[0].candidates)
    assert all(candidate.rationale for candidate in plan.scene_choices[0].candidates)
    assert all(candidate.transcript_snippet for candidate in plan.scene_choices[0].candidates)
    assert all(
        candidate.max_duration_s
        == min(
            10,
            (candidate.src_end_frame_exclusive - candidate.src_start_frame) // 30,
        )
        for candidate in plan.scene_choices[0].candidates
    )


def test_scene_candidates_expose_grounding_and_frame_derived_capacity() -> None:
    scene = SceneMaterial(
        scene_number=4,
        src_start_frame=900,
        src_end_frame_exclusive=1200,
        description="",
        transcript="typing a concise project update",
        transcript_spans=(),
        review=SceneReview(
            scene_number=4,
            src_start_frame=900,
            src_end_frame_exclusive=1200,
            description="project update editor",
            whats_happening="a status update is being typed",
            hook_score=8,
            best_window=BestWindow(offset_s=0.0, duration_s=2.9),
        ),
    )

    plan = build_rough_cut_visual_plan(
        request=request(),
        scenes=[scene, *rough_cut_scenes(2)],
        narration_text="draft the project update",
        voice_total_frames=90,
        fps=30.0,
    )

    candidate = plan.scene_choices[0].candidates[0]
    assert candidate.description == "project update editor"
    assert candidate.rationale
    assert candidate.transcript_snippet == "typing a concise project update"
    assert candidate.max_duration_s == 2


def test_scene_candidate_uses_a_deterministic_non_empty_fallback_label() -> None:
    scene = SceneMaterial(
        scene_number=7,
        src_start_frame=0,
        src_end_frame_exclusive=300,
        description="",
        transcript="",
        transcript_spans=(),
        review=None,
    )

    plan = build_rough_cut_visual_plan(
        request=request(),
        scenes=[scene, *rough_cut_scenes(2)],
        narration_text="show the workflow",
        voice_total_frames=90,
        fps=30.0,
    )

    assert plan.scene_choices[0].description == "Rough-Cut scene 7"
    assert all(
        candidate.description == "Rough-Cut scene 7"
        for candidate in plan.scene_choices[0].candidates
    )


def test_default_includes_all_scenes_when_one_second_each_fits() -> None:
    plan = build_rough_cut_visual_plan(
        request=request(),
        scenes=rough_cut_scenes(8),
        narration_text="organize files and draft mail",
        voice_total_frames=1350,
        fps=30.0,
    )

    assert all(choice.recommended_included for choice in plan.scene_choices)
    assert sum(choice.recommended_duration_s for choice in plan.scene_choices) >= 45


def test_recommendation_uses_a_candidate_that_can_hold_its_duration() -> None:
    short_best_window = BestWindow(offset_s=0.0, duration_s=2.0)
    scene = SceneMaterial(
        scene_number=3,
        src_start_frame=0,
        src_end_frame_exclusive=600,
        description="workflow",
        transcript="relevant workflow",
        transcript_spans=(TranscriptSpan(0, 60, "relevant workflow"),),
        review=SceneReview(
            scene_number=3,
            src_start_frame=0,
            src_end_frame_exclusive=600,
            description="workflow",
            whats_happening="the workflow progresses",
            hook_score=9,
            best_window=short_best_window,
            windows=[short_best_window, BestWindow(offset_s=3.0, duration_s=10.0)],
        ),
    )

    plan = build_rough_cut_visual_plan(
        request=request(),
        scenes=[*rough_cut_scenes(2), scene],
        narration_text="relevant workflow",
        voice_total_frames=300,
        fps=30.0,
    )

    choice = plan.scene_choices[-1]
    recommended = next(
        candidate
        for candidate in choice.candidates
        if candidate.candidate_id == choice.recommended_candidate_id
    )
    assert choice.recommended_duration_s == 8
    assert recommended.max_duration_s >= choice.recommended_duration_s


def test_degraded_117_second_scene_covers_late_material() -> None:
    scene = degraded_scene(start=0, end_exclusive=3533, fps=_FPS)

    windows = coverage_windows(scene, beat_text="organizing files", beat_duration_s=3.2, fps=_FPS)

    assert len(windows) == 4
    assert windows[0].start_frame == 0
    assert any(window.start_frame >= 1766 for window in windows)
    assert all(
        window.start_frame < window.end_frame_exclusive <= 3533
        for window in windows
    )


def test_relevant_transcript_anchor_beats_uniform_fallback() -> None:
    scene = scene_with_span("drafting emails", start_frame=2100, end_frame_exclusive=2220)

    windows = coverage_windows(scene, beat_text="drafting emails", beat_duration_s=3.0, fps=_FPS)

    assert windows[0].start_frame <= 2100 < windows[0].end_frame_exclusive


def test_relevant_transcript_span_outranks_an_earlier_irrelevant_span() -> None:
    scene = SceneMaterial(
        scene_number=1,
        src_start_frame=0,
        src_end_frame_exclusive=1200,
        description="desktop workflow",
        transcript="garden plants then drafting invoices",
        transcript_spans=(
            TranscriptSpan(0, 120, "garden plants"),
            TranscriptSpan(600, 720, "drafting invoices"),
        ),
        review=None,
    )

    windows = coverage_windows(
        scene,
        beat_text="drafting invoices",
        beat_duration_s=3.0,
        fps=_FPS,
    )

    assert windows[0].transcript_snippet == "drafting invoices"
    assert windows[0].start_frame <= 600 < windows[0].end_frame_exclusive


def _candidate(
    candidate_id: str,
    *,
    scene_number: int,
    start_frame: int,
    end_frame_exclusive: int,
    score: float,
) -> VisualShotCandidate:
    return VisualShotCandidate(
        candidate_id=candidate_id,
        beat_id="beat-2",
        voice_segment_index=1,
        scene_number=scene_number,
        window_index=0,
        src_start_frame=start_frame,
        src_end_frame_exclusive=end_frame_exclusive,
        thumb_frame=start_frame + (end_frame_exclusive - start_frame) // 2,
        description="workflow",
        transcript_snippet="drafting invoices",
        rationale="test candidate",
        score=score,
    )


def test_recommendation_skips_a_strongly_overlapping_window_from_the_same_scene() -> None:
    previous = _candidate(
        "previous",
        scene_number=1,
        start_frame=0,
        end_frame_exclusive=120,
        score=1.0,
    )
    near_duplicate = _candidate(
        "near-duplicate",
        scene_number=1,
        start_frame=1,
        end_frame_exclusive=121,
        score=1.0,
    )
    alternative = _candidate(
        "alternative",
        scene_number=2,
        start_frame=240,
        end_frame_exclusive=360,
        score=0.9,
    )

    recommended = _recommend([near_duplicate, alternative], previous)

    assert recommended == alternative.candidate_id


def test_recommendations_avoid_adjacent_duplicate_visuals() -> None:
    plan = build_visual_plan(
        request=request(), ordered_lines=lines(), voice=voice(), scenes=scenes(), fps=_FPS
    )

    chosen = [
        next(
            candidate
            for candidate in beat.candidates
            if candidate.candidate_id == beat.recommended_candidate_id
        )
        for beat in plan.beats
    ]
    assert all(
        (left.scene_number, left.src_start_frame, left.src_end_frame_exclusive)
        != (right.scene_number, right.src_start_frame, right.src_end_frame_exclusive)
        for left, right in zip(chosen, chosen[1:], strict=False)
    )


def test_plan_ids_are_deterministic_and_candidates_are_limited_to_four() -> None:
    first = build_visual_plan(
        request=request(), ordered_lines=lines(), voice=voice(), scenes=scenes(), fps=_FPS
    )
    second = build_visual_plan(
        request=request(), ordered_lines=lines(), voice=voice(), scenes=scenes(), fps=_FPS
    )

    candidate_ids = [
        candidate.candidate_id for beat in first.beats for candidate in beat.candidates
    ]
    assert candidate_ids == [
        "1910509b64a9daa1c33185d8211d42f72184b43aec5784bdac770ad58002c578",
        "1285e429e6f530a6b40c1e60c69d42e68bf185aca79cb2d6970aa774a1fabad7",
        "57134f2989a067c9fe83185885354a2eafdb5568233c3a14310895dc76a9c593",
        "abdf25a79fe48170894a37d3b01bcebc97b440afb80bf2a8d272544ae465a587",
        "f82ddacf71971be283b7e090c0d53c14aa933f449db91acab7ec985780ddcc5d",
        "ac3ee18ec3fb4f194f83fe26dee4243ab49506339a7db195559ac17f32a11465",
    ]
    assert first.proposal_hash == "8ed0adb5a0e9f754fcd1ab5a1b9749a6cb3ec3a374cc2e29edc9992e08283bcd"
    assert first.proposal_hash == second.proposal_hash
    assert candidate_ids == [
        candidate.candidate_id for beat in second.beats for candidate in beat.candidates
    ]
    assert all(len(beat.candidates) <= 4 for beat in first.beats)
    assert all(
        len(candidate.candidate_id) == 64
        for beat in first.beats
        for candidate in beat.candidates
    )


def test_candidates_have_capacity_for_the_voice_duration() -> None:
    plan = build_visual_plan(
        request=request(), ordered_lines=lines(), voice=voice(), scenes=scenes(), fps=_FPS
    )

    for beat in plan.beats:
        required_frames = ceil(max(4.0, beat.duration_s + INTER_SCENE_GAP_S) * _FPS)
        assert all(
            candidate.src_end_frame_exclusive - candidate.src_start_frame >= required_frames
            for candidate in beat.candidates
        )


def test_insufficient_candidates_name_the_uncovered_beat() -> None:
    too_short = SceneMaterial(
        scene_number=1,
        src_start_frame=0,
        src_end_frame_exclusive=90,
        description="brief view",
        transcript="organizing files",
        transcript_spans=(),
        review=None,
    )

    with pytest.raises(InsufficientVisualCandidates, match="beat-1"):
        build_visual_plan(
            request=request(),
            ordered_lines=lines(),
            voice=voice(),
            scenes=[too_short],
            fps=_FPS,
        )
