"""Frame-exact confirmation and resolution for Rough-Cut visual selections."""

from __future__ import annotations

import pytest

from laura.short_creator.board_models import (
    VisualPlan,
    VisualSceneCandidate,
    VisualSceneChoice,
    VisualSceneSelection,
    VoiceArtifact,
    VoiceSegment,
)
from laura.short_creator.visual_timeline import (
    VisualSelectionError,
    apply_scene_selections,
    resolve_selected_shots,
    voice_total_frames,
)

_FPS = 30.0
_HASH_A = "a" * 64
_HASH_B = "b" * 64


def candidate(order: int, *, max_duration_s: int = 10) -> VisualSceneCandidate:
    start = order * 600
    return VisualSceneCandidate(
        candidate_id=f"candidate-{order}",
        rough_cut_order=order,
        scene_number=order + 1,
        window_index=0,
        src_start_frame=start,
        src_end_frame_exclusive=start + max_duration_s * 30,
        thumb_frame=start + max_duration_s * 15,
        max_duration_s=max_duration_s,
        description=f"Rough-Cut scene {order + 1}",
        transcript_snippet=f"workflow step {order + 1}",
        rationale="covers the Rough-Cut row",
        score=0.8,
    )


def pending_plan(
    *,
    scene_count: int,
    voice_frames: int,
    max_duration_s: int = 10,
) -> VisualPlan:
    return VisualPlan(
        version=2,
        proposal_hash=_HASH_A,
        request_hash=_HASH_B,
        scene_choices=[
            VisualSceneChoice(
                rough_cut_order=order,
                scene_number=order + 1,
                description=f"Rough-Cut scene {order + 1}",
                transcript=f"workflow step {order + 1}",
                rationale="keeps the Rough-Cut row available",
                candidates=[candidate(order, max_duration_s=max_duration_s)],
                recommended_candidate_id=f"candidate-{order}",
                recommended_included=True,
                recommended_duration_s=max_duration_s,
            )
            for order in range(scene_count)
        ],
        rough_cut_scene_count=scene_count,
        voice_total_frames=voice_frames,
        fps=_FPS,
    )


def selections(
    durations: list[int],
    *,
    included: list[bool] | None = None,
) -> list[VisualSceneSelection]:
    include_flags = included if included is not None else [True] * len(durations)
    return [
        VisualSceneSelection(
            rough_cut_order=order,
            candidate_id=f"candidate-{order}",
            included=include_flags[order],
            requested_duration_s=duration,
        )
        for order, duration in enumerate(durations)
    ]


def confirmed_plan(*, durations: list[int], voice_frames: int) -> VisualPlan:
    return apply_scene_selections(
        pending_plan(scene_count=len(durations), voice_frames=voice_frames),
        selections(durations),
        "2026-08-09T10:00:00Z",
    )


def segmented_voice() -> VoiceArtifact:
    durations = [1.0, 2.0, 3.0]
    return VoiceArtifact(
        script_hash=_HASH_A,
        mp3_path="voice.mp3",
        segments=[
            VoiceSegment(
                scene_number=index + 1,
                chapter=index + 1,
                line_hash=f"line-{index}",
                mp3_path=f"voice-{index}.mp3",
                duration_s=duration,
                offset_s=float(index),
            )
            for index, duration in enumerate(durations)
        ],
    )


def test_voice_total_frames_uses_inter_scene_gaps_and_final_cushion() -> None:
    assert voice_total_frames(segmented_voice(), fps=20.0) == 140


def test_voice_total_frames_requires_segmented_voice() -> None:
    voice = VoiceArtifact(script_hash=_HASH_A, mp3_path="legacy.mp3")

    with pytest.raises(VisualSelectionError, match="segmented Voice required"):
        voice_total_frames(voice, fps=_FPS)


def test_apply_scene_selections_persists_every_decision_and_a_stable_hash() -> None:
    plan = pending_plan(scene_count=4, voice_frames=1200)
    chosen = selections([10, 10, 10, 10])

    first = apply_scene_selections(plan, chosen, "2026-08-09T10:00:00Z")
    second = apply_scene_selections(plan, chosen, "2026-08-09T11:00:00Z")

    assert first.confirmed_utc == "2026-08-09T10:00:00Z"
    assert first.selection_hash == second.selection_hash
    assert first.selection_hash is not None and len(first.selection_hash) == 64
    assert [choice.selected_candidate_id for choice in first.scene_choices] == [
        "candidate-0",
        "candidate-1",
        "candidate-2",
        "candidate-3",
    ]
    assert [choice.requested_duration_s for choice in first.scene_choices] == [10] * 4


def test_overcoverage_trims_only_final_included_shot() -> None:
    plan = confirmed_plan(durations=[10, 10, 10, 10, 10], voice_frames=1350)

    shots = resolve_selected_shots(plan)

    assert [shot.final_frames for shot in shots] == [300, 300, 300, 300, 150]
    assert [shot.rough_cut_order for shot in shots] == [0, 1, 2, 3, 4]


def test_undercoverage_is_rejected() -> None:
    plan = pending_plan(scene_count=4, voice_frames=1350)

    with pytest.raises(VisualSelectionError, match="does not cover the Voice"):
        apply_scene_selections(
            plan,
            selections([10, 10, 10, 10]),
            "2026-08-09T10:00:00Z",
        )


def test_fewer_than_three_included_scenes_is_rejected() -> None:
    plan = pending_plan(scene_count=4, voice_frames=600)

    with pytest.raises(VisualSelectionError, match="at least three"):
        apply_scene_selections(
            plan,
            selections([10, 10, 10, 10], included=[True, True, False, False]),
            "2026-08-09T10:00:00Z",
        )


@pytest.mark.parametrize(
    "chosen",
    [
        [
            VisualSceneSelection(
                rough_cut_order=0,
                candidate_id="candidate-0",
                included=True,
                requested_duration_s=10,
            )
        ]
        * 4,
        selections([10, 10, 10]),
    ],
)
def test_every_rough_cut_row_must_be_selected_exactly_once(
    chosen: list[VisualSceneSelection],
) -> None:
    plan = pending_plan(scene_count=4, voice_frames=900)

    with pytest.raises(VisualSelectionError, match="exactly once"):
        apply_scene_selections(plan, chosen, "2026-08-09T10:00:00Z")


def test_candidate_must_belong_to_its_rough_cut_row() -> None:
    plan = pending_plan(scene_count=3, voice_frames=900)
    chosen = selections([10, 10, 10])
    chosen[0] = chosen[0].model_copy(update={"candidate_id": "candidate-1"})

    with pytest.raises(VisualSelectionError, match="does not belong"):
        apply_scene_selections(plan, chosen, "2026-08-09T10:00:00Z")


def test_requested_duration_cannot_exceed_candidate_capacity() -> None:
    plan = pending_plan(scene_count=3, voice_frames=450, max_duration_s=5)

    with pytest.raises(VisualSelectionError, match="exceeds candidate capacity"):
        apply_scene_selections(
            plan,
            selections([6, 5, 5]),
            "2026-08-09T10:00:00Z",
        )


def test_last_shot_trim_must_leave_at_least_one_second() -> None:
    plan = pending_plan(scene_count=5, voice_frames=1201)

    with pytest.raises(VisualSelectionError, match="below one second"):
        apply_scene_selections(
            plan,
            selections([10, 10, 10, 10, 1]),
            "2026-08-09T10:00:00Z",
        )
