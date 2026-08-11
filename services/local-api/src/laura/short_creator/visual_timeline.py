"""Pure frame resolution for confirmed Rough-Cut visual selections."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256

from laura.short_creator.board_models import (
    VisualPlan,
    VisualSceneCandidate,
    VisualSceneSelection,
    VoiceArtifact,
)
from laura.short_creator.voice_concat import (
    INTER_SCENE_GAP_S,
    LAST_SEGMENT_CUSHION_S,
)


class VisualSelectionError(ValueError):
    """Raised when a visual selection cannot produce a valid Voice-length timeline."""


@dataclass(frozen=True)
class ResolvedVisualShot:
    rough_cut_order: int
    scene_number: int
    candidate_id: str
    src_start_frame: int
    src_end_frame_exclusive: int
    requested_frames: int
    final_frames: int


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _frames(duration_s: int, fps: float) -> int:
    return max(1, round(duration_s * fps))


def voice_total_frames(voice: VoiceArtifact, fps: float) -> int:
    """Project a segmented Voice duration to sequence frames exactly once."""
    if fps <= 0:
        raise VisualSelectionError("fps must be positive")
    if voice.segments is None or not voice.segments:
        raise VisualSelectionError("segmented Voice required")
    return sum(
        max(
            1,
            round(
                (
                    segment.duration_s
                    + (
                        LAST_SEGMENT_CUSHION_S
                        if index == len(voice.segments) - 1
                        else INTER_SCENE_GAP_S
                    )
                )
                * fps
            ),
        )
        for index, segment in enumerate(voice.segments)
    )


def _candidate_for_selection(
    *,
    plan: VisualPlan,
    selection: VisualSceneSelection,
) -> VisualSceneCandidate:
    choice = plan.scene_choices[selection.rough_cut_order]
    candidate = next(
        (
            item
            for item in choice.candidates
            if item.candidate_id == selection.candidate_id
        ),
        None,
    )
    if candidate is None:
        raise VisualSelectionError(
            f"candidate {selection.candidate_id} does not belong to Rough-Cut row "
            f"{selection.rough_cut_order}"
        )
    return candidate


def apply_scene_selections(
    plan: VisualPlan,
    selections: list[VisualSceneSelection],
    confirmed_utc: str,
) -> VisualPlan:
    """Validate and persist one explicit decision for every Rough-Cut row."""
    if plan.rough_cut_scene_count is None or plan.voice_total_frames is None or plan.fps is None:
        raise VisualSelectionError("v2 visual plan required")
    expected_orders = list(range(plan.rough_cut_scene_count))
    selected_orders = [selection.rough_cut_order for selection in selections]
    if selected_orders != expected_orders:
        raise VisualSelectionError(
            "every Rough-Cut row must be selected exactly once and in Rough-Cut order"
        )

    by_order = {selection.rough_cut_order: selection for selection in selections}
    included_frames: list[int] = []
    updated_choices = []
    stable_decisions: list[dict[str, object]] = []
    for choice in plan.scene_choices:
        selection = by_order[choice.rough_cut_order]
        candidate = _candidate_for_selection(plan=plan, selection=selection)
        if selection.requested_duration_s > candidate.max_duration_s:
            raise VisualSelectionError(
                f"Rough-Cut row {choice.rough_cut_order} duration exceeds candidate capacity"
            )
        requested_frames = _frames(selection.requested_duration_s, plan.fps)
        if selection.included:
            included_frames.append(requested_frames)
        updated_choices.append(
            choice.model_copy(
                update={
                    "selected_candidate_id": selection.candidate_id,
                    "included": selection.included,
                    "requested_duration_s": selection.requested_duration_s,
                }
            )
        )
        stable_decisions.append(
            {
                "rough_cut_order": selection.rough_cut_order,
                "candidate_id": selection.candidate_id,
                "included": selection.included,
                "requested_duration_s": selection.requested_duration_s,
            }
        )

    if len(included_frames) < 3:
        raise VisualSelectionError("visual selection requires at least three included scenes")
    requested_total = sum(included_frames)
    if requested_total < plan.voice_total_frames:
        raise VisualSelectionError("visual selection does not cover the Voice")
    final_last_frames = included_frames[-1] - (requested_total - plan.voice_total_frames)
    if final_last_frames < max(1, round(plan.fps)):
        raise VisualSelectionError("last-shot trim would leave below one second")

    selection_hash = _canonical_hash(
        {
            "proposal_hash": plan.proposal_hash,
            "selections": stable_decisions,
        }
    )
    payload = plan.model_dump(mode="python")
    payload.update(
        {
            "scene_choices": updated_choices,
            "selection_hash": selection_hash,
            "confirmed_utc": confirmed_utc,
        }
    )
    return VisualPlan.model_validate(payload)


def resolve_selected_shots(plan: VisualPlan) -> tuple[ResolvedVisualShot, ...]:
    """Resolve included shots in Rough-Cut order and trim only the final shot."""
    if plan.confirmed_utc is None or plan.voice_total_frames is None or plan.fps is None:
        raise VisualSelectionError("confirmed v2 visual plan required")

    shots: list[ResolvedVisualShot] = []
    for choice in plan.scene_choices:
        if not choice.included:
            continue
        if choice.selected_candidate_id is None or choice.requested_duration_s is None:
            raise VisualSelectionError("confirmed v2 visual plan has an incomplete selection")
        candidate = next(
            (
                item
                for item in choice.candidates
                if item.candidate_id == choice.selected_candidate_id
            ),
            None,
        )
        if candidate is None:
            raise VisualSelectionError("selected candidate does not belong to its Rough-Cut row")
        requested_frames = _frames(choice.requested_duration_s, plan.fps)
        shots.append(
            ResolvedVisualShot(
                rough_cut_order=choice.rough_cut_order,
                scene_number=choice.scene_number,
                candidate_id=candidate.candidate_id,
                src_start_frame=candidate.src_start_frame,
                src_end_frame_exclusive=candidate.src_end_frame_exclusive,
                requested_frames=requested_frames,
                final_frames=requested_frames,
            )
        )

    if len(shots) < 3:
        raise VisualSelectionError("visual selection requires at least three included scenes")
    requested_total = sum(shot.requested_frames for shot in shots)
    if requested_total < plan.voice_total_frames:
        raise VisualSelectionError("visual selection does not cover the Voice")
    final_frames = shots[-1].requested_frames - (requested_total - plan.voice_total_frames)
    if final_frames < max(1, round(plan.fps)):
        raise VisualSelectionError("last-shot trim would leave below one second")
    shots[-1] = ResolvedVisualShot(
        rough_cut_order=shots[-1].rough_cut_order,
        scene_number=shots[-1].scene_number,
        candidate_id=shots[-1].candidate_id,
        src_start_frame=shots[-1].src_start_frame,
        src_end_frame_exclusive=shots[-1].src_end_frame_exclusive,
        requested_frames=shots[-1].requested_frames,
        final_frames=final_frames,
    )
    return tuple(shots)
