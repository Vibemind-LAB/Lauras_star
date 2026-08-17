"""Domain rules and wire-ready views for resumable visual-selection drafts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from .board_models import VisualPlan, VisualSceneSelection


class VisualDraftValidationError(ValueError):
    """A draft is not a complete, structurally safe Rough-Cut decision set."""


class VisualSelectionDraftView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    proposal_hash: str
    selections: list[VisualSceneSelection]
    revision: int | None
    updated_utc: str | None
    stale: bool = False
    stale_reason: str | None = None


def default_visual_selections(plan: VisualPlan) -> list[VisualSceneSelection]:
    """Return one explicit recommendation-backed decision per v2 Rough-Cut row."""
    if not plan.scene_choices:
        raise VisualDraftValidationError("visual proposal has no Rough-Cut scene choices")
    return [
        VisualSceneSelection(
            rough_cut_order=choice.rough_cut_order,
            candidate_id=choice.recommended_candidate_id,
            included=choice.recommended_included,
            requested_duration_s=choice.recommended_duration_s,
        )
        for choice in plan.scene_choices
    ]


def validate_draft_selections(
    plan: VisualPlan, selections: list[VisualSceneSelection]
) -> list[VisualSceneSelection]:
    """Validate draft structure without enforcing final coverage or minimum includes."""
    choices = plan.scene_choices
    if len(selections) != len(choices):
        raise VisualDraftValidationError("provide one decision per Rough-Cut row")
    orders = [selection.rough_cut_order for selection in selections]
    if orders != list(range(len(choices))):
        raise VisualDraftValidationError("decisions must follow exact Rough-Cut order")
    for choice, selection in zip(choices, selections, strict=True):
        if choice.rough_cut_order != selection.rough_cut_order:
            raise VisualDraftValidationError("decisions must follow exact Rough-Cut order")
        candidate = next(
            (
                item
                for item in choice.candidates
                if item.candidate_id == selection.candidate_id
            ),
            None,
        )
        if candidate is None:
            raise VisualDraftValidationError("candidate does not belong to Rough-Cut row")
        if selection.requested_duration_s > candidate.max_duration_s:
            raise VisualDraftValidationError("requested duration exceeds candidate capacity")
    return list(selections)


def draft_view_from_row(row: dict[str, Any]) -> VisualSelectionDraftView:
    """Parse one repository row through the same strict domain model used by the API."""
    return VisualSelectionDraftView(
        session_id=str(row["session_id"]),
        proposal_hash=str(row["proposal_hash"]),
        selections=[
            VisualSceneSelection.model_validate(selection)
            for selection in row["selections"]
        ],
        revision=int(row["revision"]),
        updated_utc=str(row["updated_utc"]),
    )
