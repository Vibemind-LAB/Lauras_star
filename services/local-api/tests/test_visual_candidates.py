"""Pure, deterministic candidate generation for a visual-only recut."""

from __future__ import annotations

from math import ceil

import pytest

from laura.short_creator.board_models import (
    BestWindow,
    SceneReview,
    ScriptLine,
    VisualRecutRequest,
    VoiceArtifact,
    VoiceSegment,
)
from laura.short_creator.visual_candidates import (
    InsufficientVisualCandidates,
    SceneMaterial,
    TranscriptSpan,
    build_visual_plan,
    coverage_windows,
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

    assert first.proposal_hash == second.proposal_hash
    assert [candidate.candidate_id for beat in first.beats for candidate in beat.candidates] == [
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
