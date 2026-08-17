"""Domain contracts for incomplete but structurally valid visual drafts."""

from __future__ import annotations

import pytest

from laura.short_creator.board_models import (
    VisualPlan,
    VisualSceneCandidate,
    VisualSceneChoice,
)
from laura.short_creator.visual_selection_drafts import (
    VisualDraftValidationError,
    default_visual_selections,
    validate_draft_selections,
)


def _candidate(order: int, choice: int, *, max_duration_s: int = 10) -> VisualSceneCandidate:
    start = order * 900 + choice * 300
    return VisualSceneCandidate(
        candidate_id=f"scene-{order}-candidate-{choice}",
        rough_cut_order=order,
        scene_number=order + 1,
        window_index=choice,
        src_start_frame=start,
        src_end_frame_exclusive=start + max_duration_s * 30,
        thumb_frame=start + 30,
        max_duration_s=max_duration_s,
        description=f"Scene {order + 1}",
        transcript_snippet="workflow",
        rationale="current rough-cut row",
        score=1.0 - choice / 10,
    )


def _plan() -> VisualPlan:
    parents = {
        "rough_cut": "1" * 64,
        "script": "2" * 64,
        "source_media": "3" * 64,
        "source_media_quick": "4" * 64,
        "visual_recut_request": "5" * 64,
        "voice": "6" * 64,
    }
    return VisualPlan(
        version=2,
        proposal_hash="a" * 64,
        request_hash="b" * 64,
        scene_choices=[
            VisualSceneChoice(
                rough_cut_order=order,
                scene_number=order + 1,
                description=f"Scene {order + 1}",
                transcript="workflow",
                rationale="current rough-cut row",
                candidates=[_candidate(order, 0), _candidate(order, 1, max_duration_s=6)],
                recommended_candidate_id=f"scene-{order}-candidate-0",
                recommended_included=order < 3,
                recommended_duration_s=5,
            )
            for order in range(4)
        ],
        rough_cut_scene_count=4,
        voice_total_frames=600,
        fps=30.0,
        parents=parents,
    )


def test_default_visual_selections_follow_every_recommendation_in_order() -> None:
    """Catches a new draft omitting, reordering, or changing a Rough-Cut row."""
    selections = default_visual_selections(_plan())

    assert [selection.model_dump() for selection in selections] == [
        {
            "rough_cut_order": 0,
            "candidate_id": "scene-0-candidate-0",
            "included": True,
            "requested_duration_s": 5,
        },
        {
            "rough_cut_order": 1,
            "candidate_id": "scene-1-candidate-0",
            "included": True,
            "requested_duration_s": 5,
        },
        {
            "rough_cut_order": 2,
            "candidate_id": "scene-2-candidate-0",
            "included": True,
            "requested_duration_s": 5,
        },
        {
            "rough_cut_order": 3,
            "candidate_id": "scene-3-candidate-0",
            "included": False,
            "requested_duration_s": 5,
        },
    ]


def test_draft_validation_allows_incomplete_final_coverage() -> None:
    """Catches autosave incorrectly applying final coverage rules to intermediate work."""
    selections = default_visual_selections(_plan())
    changed = [
        selection.model_copy(update={"included": False}) for selection in selections
    ]

    assert validate_draft_selections(_plan(), changed) == changed


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda rows: rows[:-1], "one decision per Rough-Cut row"),
        (lambda rows: [rows[1], rows[0], *rows[2:]], "Rough-Cut order"),
        (
            lambda rows: [
                rows[0].model_copy(update={"candidate_id": "scene-0-unknown"}),
                *rows[1:],
            ],
            "candidate does not belong",
        ),
        (
            lambda rows: [
                rows[0].model_copy(
                    update={
                        "candidate_id": "scene-0-candidate-1",
                        "requested_duration_s": 7,
                    }
                ),
                *rows[1:],
            ],
            "candidate capacity",
        ),
    ],
)
def test_draft_validation_rejects_structurally_unsafe_rows(
    mutate: object, reason: str
) -> None:
    """Catches malformed drafts becoming durable and impossible to confirm later."""
    rows = default_visual_selections(_plan())
    assert callable(mutate)
    changed = mutate(rows)

    with pytest.raises(VisualDraftValidationError, match=reason):
        validate_draft_selections(_plan(), changed)
