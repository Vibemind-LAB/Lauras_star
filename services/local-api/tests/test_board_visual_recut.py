"""Persisted optional visual-recut state and its user-review gates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from laura.short_creator.board import Board
from laura.short_creator.board_models import (
    BestWindow,
    BoardMeta,
    Chapter,
    ContactSheet,
    ContactSheetTile,
    Cutlist,
    CutSegment,
    QaReport,
    RenderReport,
    SceneCandidate,
    SceneReview,
    SceneSelection,
    Script,
    ScriptLine,
    Storyline,
    VisualBeatPlan,
    VisualPlan,
    VisualRecutRequest,
    VisualSceneCandidate,
    VisualSceneChoice,
    VisualSceneSelection,
    VisualShotCandidate,
    VoiceArtifact,
    content_hash,
    script_hash,
)

_HASH_A = "a" * 64
_HASH_B = "b" * 64
_HASH_C = "c" * 64


def _review(scene_number: int) -> SceneReview:
    return SceneReview(
        scene_number=scene_number,
        src_start_frame=(scene_number - 1) * 120,
        src_end_frame_exclusive=scene_number * 120,
        description=f"scene {scene_number}",
        whats_happening="a product workflow is shown",
        hook_score=7,
        best_window=BestWindow(offset_s=0.0, duration_s=4.0),
    )


def _selection() -> SceneSelection:
    candidates = [
        SceneCandidate(
            scene_number=number,
            src_start_frame=(number - 1) * 120,
            src_end_frame_exclusive=number * 120,
            thumb_frame=(number - 1) * 120 + 60,
            description=f"scene {number}",
            transcript_snippet="workflow narration",
            rationale="fits the approved story",
            recommended=True,
        )
        for number in [1, 2]
    ]
    return SceneSelection(
        candidates=candidates,
        selected_scene_numbers=[1, 2],
        confirmed_utc="2026-08-08T09:00:00+00:00",
    )


def _storyline() -> Storyline:
    return Storyline(
        red_thread="one workflow, clearly explained",
        arc=[
            Chapter(
                chapter=1,
                role="hook",
                message="show the first step",
                scene_numbers=[1],
                target_seconds=4.0,
            ),
            Chapter(
                chapter=2,
                role="feature",
                message="show the outcome",
                scene_numbers=[2],
                target_seconds=4.0,
            ),
        ],
    )


def _script() -> Script:
    return Script(
        language="English",
        lines=[
            ScriptLine(chapter=1, scene_number=1, text="Start with the important workflow."),
            ScriptLine(chapter=2, scene_number=2, text="Then see the result clearly."),
        ],
    )


def _voice(script: Script) -> VoiceArtifact:
    return VoiceArtifact(
        script_hash=script_hash(script.lines),
        mp3_path="voice.mp3",
        voice_s=8.0,
    )


@pytest.fixture
def board(tmp_path: Path) -> Board:
    result = Board.create(
        tmp_path / "board",
        BoardMeta(
            session_id="s1",
            asset_id="a1",
            created_utc="2026-08-08T08:00:00+00:00",
            task="test visual recut",
            target_seconds=8.0,
            scene_gate=True,
            script_gate=True,
        ),
    )
    for scene_number in [1, 2]:
        result.save_scene_review(_review(scene_number))
    result.save("scene_selection", _selection())
    result.save("storyline", _storyline())
    script = _script()
    result.save("script", script)
    result.save("voice", _voice(script))
    return result


@pytest.fixture
def board_with_finished_film(board: Board) -> Board:
    board.save(
        "cutlist",
        Cutlist(
            segments=[
                CutSegment(order=0, scene_number=1, start_frame=0, end_frame_exclusive=120),
                CutSegment(order=1, scene_number=2, start_frame=120, end_frame_exclusive=240),
            ]
        ),
    )
    board.save("contact_sheet", sheet_fixture())
    board.save(
        "render_report",
        RenderReport(export_id="e1", video_s=8.0, width=1080, height=1920),
    )
    board.save("qa_report", QaReport(verdict="ship"))
    return board


def visual_request(script: Script, voice: VoiceArtifact) -> VisualRecutRequest:
    return VisualRecutRequest(
        user_request="Choose stronger visual moments for the approved narration.",
        script_version=script.version,
        script_hash=content_hash(script),
        voice_version=voice.version,
        voice_hash=content_hash(voice),
    )


def request_fixture() -> VisualRecutRequest:
    return VisualRecutRequest(
        user_request="Re-cut the visuals without changing narration.",
        script_version=1,
        script_hash=_HASH_A,
        voice_version=1,
        voice_hash=_HASH_B,
    )


def _candidate(beat_id: str = "beat-1", candidate_id: str = "candidate-1") -> VisualShotCandidate:
    return VisualShotCandidate(
        candidate_id=candidate_id,
        beat_id=beat_id,
        voice_segment_index=0,
        scene_number=1,
        window_index=0,
        src_start_frame=0,
        src_end_frame_exclusive=120,
        thumb_frame=60,
        description="clear workflow view",
        transcript_snippet="start with the important workflow",
        rationale="matches the narration",
        score=0.9,
    )


def _scene_candidate(
    rough_cut_order: int, candidate_id: str | None = None, **overrides: object
) -> VisualSceneCandidate:
    base: dict[str, object] = {
        "candidate_id": candidate_id or f"scene-candidate-{rough_cut_order}",
        "rough_cut_order": rough_cut_order,
        "scene_number": rough_cut_order + 1,
        "window_index": 0,
        "src_start_frame": rough_cut_order * 120,
        "src_end_frame_exclusive": (rough_cut_order + 1) * 120,
        "thumb_frame": rough_cut_order * 120 + 60,
        "max_duration_s": 5,
        "description": f"clear rough-cut scene {rough_cut_order}",
        "transcript_snippet": "the approved narration",
        "rationale": "matches this rough-cut row",
        "score": 0.9,
    }
    base.update(overrides)
    return VisualSceneCandidate(**base)  # type: ignore[arg-type]


def v2_plan(
    *,
    scene_orders: list[int],
    confirmed_utc: str | None = None,
    selected: bool = False,
) -> VisualPlan:
    return VisualPlan(
        version=2,
        proposal_hash=_HASH_C,
        request_hash=_HASH_A,
        scene_choices=[
            VisualSceneChoice(
                rough_cut_order=order,
                scene_number=order + 1,
                description=f"rough-cut scene {order}",
                transcript="the approved narration",
                rationale="covers this rough-cut row",
                candidates=[_scene_candidate(order)],
                recommended_candidate_id=f"scene-candidate-{order}",
                recommended_included=True,
                recommended_duration_s=5,
                selected_candidate_id=f"scene-candidate-{order}" if selected else None,
                included=True if selected else None,
                requested_duration_s=5 if selected else None,
            )
            for order in scene_orders
        ],
        voice_total_frames=960,
        fps=24.0,
        confirmed_utc=confirmed_utc,
    )


def legacy_visual_plan_payload() -> dict[str, object]:
    return {
        "version": 1,
        "proposal_hash": _HASH_C,
        "request_hash": _HASH_A,
        "beats": [
            {
                "beat_id": "beat-1",
                "voice_segment_index": 0,
                "narration_text": "Start with the important workflow.",
                "duration_s": 4.0,
                "candidates": [_candidate().model_dump()],
                "recommended_candidate_id": "candidate-1",
            },
            {
                "beat_id": "beat-2",
                "voice_segment_index": 1,
                "narration_text": "Then see the result clearly.",
                "duration_s": 4.0,
                "candidates": [_candidate("beat-2", "candidate-2").model_dump()],
                "recommended_candidate_id": "candidate-2",
            },
        ],
    }


def plan_fixture(*, confirmed_utc: str | None) -> VisualPlan:
    candidate = _candidate()
    return VisualPlan(
        proposal_hash=_HASH_C,
        request_hash=_HASH_A,
        beats=[
            VisualBeatPlan(
                beat_id="beat-1",
                voice_segment_index=0,
                narration_text="Start with the important workflow.",
                duration_s=4.0,
                candidates=[candidate],
                recommended_candidate_id=candidate.candidate_id,
                selected_candidate_id=candidate.candidate_id if confirmed_utc is not None else None,
            )
        ],
        confirmed_utc=confirmed_utc,
    )


def cutlist_fixture() -> Cutlist:
    return Cutlist(
        segments=[CutSegment(order=0, scene_number=1, start_frame=0, end_frame_exclusive=120)]
    )


def sheet_fixture() -> ContactSheet:
    return ContactSheet(
        png_path="sheet.png",
        cols=1,
        rows=1,
        tiles=[
            ContactSheetTile(
                order=0,
                scene_number=1,
                frame=60,
                label="0 S1",
                src_start_frame=0,
                src_end_frame_exclusive=120,
                narration_excerpt="Start with the important workflow.",
                rationale="matches the narration",
            )
        ],
    )


def test_visual_request_invalidates_only_visual_downstream(board_with_finished_film: Board) -> None:
    script = board_with_finished_film.load("script")
    voice = board_with_finished_film.load("voice")
    assert isinstance(script, Script)
    assert isinstance(voice, VoiceArtifact)
    script_version, voice_version = script.version, voice.version

    board_with_finished_film.save("visual_recut_request", visual_request(script, voice))

    assert board_with_finished_film.load("storyline") is not None
    preserved_script = board_with_finished_film.load("script")
    preserved_voice = board_with_finished_film.load("voice")
    assert isinstance(preserved_script, Script)
    assert isinstance(preserved_voice, VoiceArtifact)
    assert preserved_script.version == script_version
    assert preserved_voice.version == voice_version
    assert board_with_finished_film.load("cutlist") is None
    assert board_with_finished_film.load("contact_sheet") is None
    assert board_with_finished_film.load("render_report") is None
    assert board_with_finished_film.load("qa_report") is None


def test_pending_visual_plan_and_sheet_approval_are_resume_points(board: Board) -> None:
    board.save("visual_recut_request", request_fixture())
    board.save("visual_plan", plan_fixture(confirmed_utc=None))
    assert board.resume_point([1, 2]) == "visual_selection"

    board.save("visual_plan", plan_fixture(confirmed_utc="2026-08-08T10:00:00+00:00"))
    board.save("cutlist", cutlist_fixture())
    sheet = sheet_fixture()
    board.save("contact_sheet", sheet)
    board.clear_contact_sheet_approval(enable_gate=True)
    assert board.resume_point([1, 2]) == "contact_sheet_approval"

    board.set_contact_sheet_approved("2026-08-08T10:01:00+00:00", _HASH_C)
    assert board.resume_point([1, 2]) == "contact_sheet_approval"

    board.set_contact_sheet_approved("2026-08-08T10:02:00+00:00", content_hash(sheet))
    assert board.resume_point([1, 2]) == "render_report"


def test_visual_plan_rejects_duplicate_candidate_ids() -> None:
    candidate = _candidate()
    with pytest.raises(ValidationError, match="duplicate candidate_ids"):
        VisualPlan(
            proposal_hash=_HASH_A,
            request_hash=_HASH_B,
            beats=[
                VisualBeatPlan(
                    beat_id="beat-1",
                    voice_segment_index=0,
                    narration_text="Narration.",
                    duration_s=1.0,
                    candidates=[candidate, candidate],
                    recommended_candidate_id=candidate.candidate_id,
                )
            ],
        )


def test_v2_plan_has_one_choice_per_rough_cut_order() -> None:
    plan = v2_plan(scene_orders=[0, 1, 2, 3])
    assert [choice.rough_cut_order for choice in plan.scene_choices] == [0, 1, 2, 3]

    with pytest.raises(ValidationError, match="duplicate rough_cut_order"):
        v2_plan(scene_orders=[0, 1, 1])


def test_v1_beat_plan_still_loads_without_scene_choices() -> None:
    payload = legacy_visual_plan_payload()
    plan = VisualPlan.model_validate(payload)

    assert plan.version == 1
    assert len(plan.beats) == 2
    assert plan.scene_choices == []


def test_v2_candidate_ranges_and_duration_choices_are_bounded() -> None:
    with pytest.raises(ValidationError, match="src_end_frame_exclusive"):
        _scene_candidate(0, src_end_frame_exclusive=0)
    with pytest.raises(ValidationError, match="thumb_frame"):
        _scene_candidate(0, thumb_frame=120)
    with pytest.raises(ValidationError, match="less than or equal to 10"):
        _scene_candidate(0, max_duration_s=11)
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        VisualSceneSelection(
            rough_cut_order=0,
            candidate_id="scene-candidate-0",
            included=True,
            requested_duration_s=0,
        )


def test_v2_plan_requires_rough_cut_choices_and_voice_timing() -> None:
    with pytest.raises(ValidationError, match="v2 plans require non-empty scene_choices"):
        VisualPlan(
            version=2,
            proposal_hash=_HASH_C,
            request_hash=_HASH_A,
            voice_total_frames=960,
            fps=24.0,
        )
    with pytest.raises(ValidationError, match="v2 plans require voice_total_frames and fps"):
        VisualPlan(
            version=2,
            proposal_hash=_HASH_C,
            request_hash=_HASH_A,
            scene_choices=[
                VisualSceneChoice(
                    rough_cut_order=0,
                    scene_number=1,
                    description="rough-cut scene 0",
                    transcript="the approved narration",
                    rationale="covers this rough-cut row",
                    candidates=[_scene_candidate(0)],
                    recommended_candidate_id="scene-candidate-0",
                    recommended_included=True,
                    recommended_duration_s=5,
                )
            ],
        )
    with pytest.raises(ValidationError, match="v1 plans require non-empty beats"):
        VisualPlan(version=1, proposal_hash=_HASH_C, request_hash=_HASH_A)


def test_confirmed_v2_plan_requires_a_decision_for_each_rough_cut_row() -> None:
    with pytest.raises(ValidationError, match="confirmed v2 plan requires a selection"):
        v2_plan(scene_orders=[0, 1], confirmed_utc="2026-08-09T10:00:00+00:00")

    confirmed = v2_plan(
        scene_orders=[0, 1], confirmed_utc="2026-08-09T10:00:00+00:00", selected=True
    )
    assert [choice.selected_candidate_id for choice in confirmed.scene_choices] == [
        "scene-candidate-0",
        "scene-candidate-1",
    ]


def test_visual_beat_requires_its_recommended_candidate() -> None:
    with pytest.raises(ValidationError, match="recommended_candidate_id"):
        VisualBeatPlan(
            beat_id="beat-1",
            voice_segment_index=0,
            narration_text="Narration.",
            duration_s=1.0,
            candidates=[_candidate()],
            recommended_candidate_id="missing",
        )


def test_visual_beat_rejects_a_selection_from_another_beat() -> None:
    with pytest.raises(ValidationError, match="selected_candidate_id"):
        VisualBeatPlan(
            beat_id="beat-1",
            voice_segment_index=0,
            narration_text="Narration.",
            duration_s=1.0,
            candidates=[_candidate()],
            recommended_candidate_id="candidate-1",
            selected_candidate_id="candidate-2",
        )


def test_old_meta_json_without_visual_recut_fields_loads(tmp_path: Path) -> None:
    root = tmp_path / "board"
    (root / "scene_reviews").mkdir(parents=True)
    (root / "versions").mkdir(parents=True)
    old_meta = {
        "session_id": "s1",
        "asset_id": "a1",
        "created_utc": "2026-08-01T00:00:00+00:00",
        "task": "older board",
        "target_seconds": 8.0,
    }
    (root / "meta.json").write_text(json.dumps(old_meta), encoding="utf-8")

    meta = Board.open(root).meta()

    assert meta.contact_sheet_gate is False
    assert meta.contact_sheet_approved_utc is None
    assert meta.contact_sheet_approved_hash is None


def test_board_without_visual_request_keeps_the_existing_resume_chain(board: Board) -> None:
    assert board.load("visual_recut_request") is None
    assert board.resume_point([1, 2]) == "cutlist"


def test_status_exposes_visual_selection_and_content_aware_contact_sheet_gate(board: Board) -> None:
    board.save("visual_recut_request", request_fixture())
    board.save("visual_plan", plan_fixture(confirmed_utc="2026-08-08T10:00:00+00:00"))
    board.save("cutlist", cutlist_fixture())
    sheet = sheet_fixture()
    board.save("contact_sheet", sheet)
    board.clear_contact_sheet_approval(enable_gate=True)

    pending = board.status()

    assert pending["visual_selection_gate"] == {
        "enabled": True,
        "approved": True,
        "pending": False,
        "proposal_id": _HASH_C,
        "beats": [
            {
                "beat_id": "beat-1",
                "voice_segment_index": 0,
                "narration_text": "Start with the important workflow.",
                "duration_s": 4.0,
                "recommended_candidate_id": "candidate-1",
                "selected_candidate_id": "candidate-1",
                "candidates": [_candidate().model_dump()],
            }
        ],
        "scene_choices": [],
        "voice_total_frames": None,
        "fps": None,
    }
    assert pending["contact_sheet_gate"]["enabled"] is True
    assert pending["contact_sheet_gate"]["approved"] is False
    assert pending["contact_sheet_gate"]["pending"] is True
    assert pending["contact_sheet_gate"]["current_sheet_hash"] == content_hash(sheet)
    assert pending["contact_sheet_gate"]["tiles"] == [sheet.tiles[0].model_dump()]

    board.set_contact_sheet_approved("2026-08-08T10:01:00+00:00", content_hash(sheet))

    approved = board.status()["contact_sheet_gate"]
    assert approved["approved"] is True
    assert approved["pending"] is False


def test_status_exposes_v2_rough_cut_visual_choices(board: Board) -> None:
    board.save("visual_recut_request", request_fixture())
    plan = v2_plan(scene_orders=[0, 1])
    board.save("visual_plan", plan)

    gate = board.status()["visual_selection_gate"]

    assert gate["beats"] == []
    assert gate["scene_choices"] == [choice.model_dump() for choice in plan.scene_choices]
    assert gate["voice_total_frames"] == 960
    assert gate["fps"] == 24.0
