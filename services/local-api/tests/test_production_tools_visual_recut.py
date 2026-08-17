"""Production tools for a visual-only, full-frame recut of preserved narration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.short_creator import context as production_context
from laura.short_creator.board import Board
from laura.short_creator.board_models import (
    BestWindow,
    BoardMeta,
    Chapter,
    ContactSheet,
    ContactSheetTile,
    Cutlist,
    SceneReview,
    Script,
    ScriptLine,
    Storyline,
    VisualBeatPlan,
    VisualPlan,
    VisualRecutRequest,
    VisualSceneSelection,
    VisualShotCandidate,
    VoiceArtifact,
    VoiceSegment,
    content_hash,
    lines_in_storyline_order,
    script_hash,
)
from laura.short_creator.production_pipeline import run_deterministic_tail
from laura.short_creator.production_tools import ProductionDeps, build_production_tool_specs
from laura.short_creator.visual_timeline import apply_scene_selections

FPS = 30
SCENE_FRAMES = 300


@dataclass
class Counter:
    value: int = 0

    def raise_on_call(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.value += 1
        raise AssertionError("renderer must not be called")


@dataclass
class Harness:
    db: Database
    asset_id: str
    board: Board


class FakeRenderer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, db: Database, asset_id: str, segments: list[tuple[int, int]], **kwargs: Any
    ) -> dict[str, Any]:
        self.calls.append({"asset_id": asset_id, "segments": segments, **kwargs})
        asset = repos.get_asset(db, asset_id)
        assert asset is not None
        export = repos.create_export(
            db, project_id=str(asset["project_id"]), timeline_id=None, format="mp4"
        )
        repos.set_export_done(db, export["id"], path="rendered.mp4", size_bytes=1)
        return {"ok": True, "export_id": export["id"]}


def _seed_two_scenes(tmp_path: Path, *, scene_count: int = 2) -> tuple[Database, str]:
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db: Database = SqliteDatabase(settings.db_path)
    db.migrate()
    project = repos.create_project(
        db,
        name="p",
        rate_num=FPS,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(tmp_path / "ws" / "proj"),
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="a.mp4",
        source_path=str(tmp_path / "a.mp4"),
    )
    timeline = repos.create_timeline(
        db,
        project_id=project["id"],
        name="Rough Cut",
        kind="rough_cut",
        created_from=asset["id"],
    )
    repos.add_timeline_clip(
        db,
        timeline_id=timeline["id"],
        asset_id=asset["id"],
        src_in_frame=0,
        src_out_frame_exclusive=SCENE_FRAMES * scene_count,
        seq_in_frame=0,
        seq_out_frame_exclusive=SCENE_FRAMES * scene_count,
        lane=0,
        role="base",
    )
    repos.replace_scenes(
        db,
        project["id"],
        timeline["id"],
        [
            (index * SCENE_FRAMES, (index + 1) * SCENE_FRAMES)
            for index in range(scene_count)
        ],
    )
    return db, str(asset["id"])


def _storyline() -> Storyline:
    return Storyline(
        red_thread="keep the approved narration",
        arc=[
            Chapter(
                chapter=1,
                role="hook",
                message="show the workflow",
                scene_numbers=[1, 2],
                target_seconds=4.0,
            )
        ],
    )


def _script() -> Script:
    return Script(
        language="de",
        lines=[
            ScriptLine(chapter=1, scene_number=1, text="Zeige den ersten Schritt."),
            ScriptLine(chapter=1, scene_number=2, text="Dann folgt das Ergebnis."),
        ],
    )


def _voice(script: Script, storyline: Storyline) -> VoiceArtifact:
    ordered_lines = lines_in_storyline_order(script, storyline)
    return VoiceArtifact(
        script_hash=script_hash(ordered_lines),
        mp3_path="voice.mp3",
        timings_path="voice.timings.json",
        voice_s=2.8,
        segments=[
            VoiceSegment(
                scene_number=1,
                chapter=1,
                line_hash="a" * 64,
                mp3_path="voice-1.mp3",
                duration_s=1.0,
                offset_s=0.0,
            ),
            VoiceSegment(
                scene_number=2,
                chapter=1,
                line_hash="b" * 64,
                mp3_path="voice-2.mp3",
                duration_s=1.5,
                offset_s=1.3,
            ),
        ],
    )


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    db, asset_id = _seed_two_scenes(tmp_path)
    board = Board.create(
        tmp_path / "board",
        BoardMeta(
            session_id="s1",
            asset_id=asset_id,
            created_utc="2026-08-08T08:00:00+00:00",
            task="visual recut",
            target_seconds=4.0,
        ),
    )
    for scene_number in (1, 2):
        board.save_scene_review(
            SceneReview(
                scene_number=scene_number,
                src_start_frame=(scene_number - 1) * SCENE_FRAMES,
                src_end_frame_exclusive=scene_number * SCENE_FRAMES,
                description=f"scene {scene_number} shows the workflow",
                whats_happening="the product changes state",
                hook_score=8,
                best_window=BestWindow(offset_s=0.0, duration_s=10.0),
                windows=[BestWindow(offset_s=0.0, duration_s=10.0)],
            )
        )
    storyline = _storyline()
    script = _script()
    board.save("storyline", storyline)
    board.save("script", script)
    board.save("voice", _voice(script, storyline))
    return Harness(db=db, asset_id=asset_id, board=board)


@pytest.fixture
def finished_harness(harness: Harness) -> Harness:
    from laura.short_creator.board_models import CutSegment, QaReport, RenderReport

    harness.board.save(
        "cutlist",
        Cutlist(
            segments=[
                CutSegment(order=0, scene_number=1, start_frame=0, end_frame_exclusive=120)
            ]
        ),
    )
    harness.board.save("contact_sheet", _sheet(harness.board))
    harness.board.save(
        "render_report", RenderReport(export_id="e1", video_s=4.0, width=1080, height=1920)
    )
    harness.board.save("qa_report", QaReport(verdict="ship"))
    return harness


@pytest.fixture
def v2_harness(tmp_path: Path) -> Harness:
    scene_count = 5
    db, asset_id = _seed_two_scenes(tmp_path, scene_count=scene_count)
    board = Board.create(
        tmp_path / "v2-board",
        BoardMeta(
            session_id="v2",
            asset_id=asset_id,
            created_utc="2026-08-09T08:00:00+00:00",
            task="rough-cut visual recut",
            target_seconds=45.0,
        ),
    )
    for scene_number in range(1, scene_count + 1):
        board.save_scene_review(
            SceneReview(
                scene_number=scene_number,
                src_start_frame=(scene_number - 1) * SCENE_FRAMES,
                src_end_frame_exclusive=scene_number * SCENE_FRAMES,
                description=f"Rough-Cut scene {scene_number}",
                whats_happening=f"workflow step {scene_number}",
                hook_score=8,
                best_window=BestWindow(offset_s=0.0, duration_s=10.0),
                windows=[BestWindow(offset_s=0.0, duration_s=10.0)],
            )
        )
    storyline = Storyline(
        red_thread="keep every Rough-Cut scene available",
        arc=[
            Chapter(
                chapter=1,
                role="feature",
                message="show the full workflow",
                scene_numbers=list(range(1, scene_count + 1)),
                target_seconds=45.0,
            )
        ],
    )
    script = Script(
        language="de",
        lines=[
            ScriptLine(
                chapter=1,
                scene_number=scene_number,
                text=f"Schritt {scene_number} bleibt unverändert.",
            )
            for scene_number in range(1, scene_count + 1)
        ],
    )
    board.save("storyline", storyline)
    board.save("script", script)
    board.save(
        "voice",
        VoiceArtifact(
            script_hash=script_hash(lines_in_storyline_order(script, storyline)),
            mp3_path="voice-v2.mp3",
            voice_s=45.0,
            segments=[
                VoiceSegment(
                    scene_number=scene_number,
                    chapter=1,
                    line_hash=f"{scene_number}" * 64,
                    mp3_path=f"voice-{scene_number}.mp3",
                    duration_s=9.65 if scene_number < scene_count else 4.7,
                    offset_s=float((scene_number - 1) * 10),
                )
                for scene_number in range(1, scene_count + 1)
            ],
        ),
    )
    return Harness(db=db, asset_id=asset_id, board=board)


def versions(board: Board, *names: str) -> tuple[int, ...]:
    result: list[int] = []
    for name in names:
        artifact = board.load(name)
        assert artifact is not None
        version = getattr(artifact, "version", None)
        assert isinstance(version, int)
        result.append(version)
    return tuple(result)


def versions_and_hashes(board: Board, *names: str) -> tuple[tuple[int, str], ...]:
    result: list[tuple[int, str]] = []
    for name in names:
        artifact = board.load(name)
        assert artifact is not None
        version = getattr(artifact, "version", None)
        assert isinstance(version, int)
        result.append((version, content_hash(artifact)))
    return tuple(result)


def tool(
    harness: Harness,
    name: str,
    *,
    render_segments: Any | None = None,
    probe_duration: Any | None = None,
) -> Any:
    deps = ProductionDeps(render_segments=render_segments, probe_duration=probe_duration)
    specs = build_production_tool_specs(
        harness.db, harness.board, asset_id=harness.asset_id, deps=deps
    )
    return next(spec.func for spec in specs if spec.name == name)


def save_confirmed_legacy_visual_plan(board: Board) -> None:
    storyline = board.load("storyline")
    script = board.load("script")
    voice = board.load("voice")
    assert isinstance(storyline, Storyline)
    assert isinstance(script, Script)
    assert isinstance(voice, VoiceArtifact)
    assert voice.segments is not None
    request = VisualRecutRequest(
        user_request="legacy v1 visual recut",
        framing_mode="full_frame_blur",
        script_version=script.version,
        script_hash=content_hash(script),
        voice_version=voice.version,
        voice_hash=content_hash(voice),
        parents={"script": content_hash(script), "voice": content_hash(voice)},
    )
    board.save("visual_recut_request", request)
    beats: list[VisualBeatPlan] = []
    ordered_lines = lines_in_storyline_order(script, storyline)
    for index, segment in enumerate(voice.segments):
        beat_id = f"legacy-beat-{index}"
        candidate_id = f"legacy-candidate-{index}"
        candidate = VisualShotCandidate(
            candidate_id=candidate_id,
            beat_id=beat_id,
            voice_segment_index=index,
            scene_number=segment.scene_number,
            window_index=0,
            src_start_frame=index * SCENE_FRAMES,
            src_end_frame_exclusive=(index + 1) * SCENE_FRAMES,
            thumb_frame=index * SCENE_FRAMES + SCENE_FRAMES // 2,
            description=f"scene {segment.scene_number}",
            transcript_snippet=ordered_lines[index].text,
            rationale="legacy v1 compatibility fixture",
            score=1.0,
        )
        beats.append(
            VisualBeatPlan(
                beat_id=beat_id,
                voice_segment_index=index,
                narration_text=ordered_lines[index].text,
                duration_s=segment.duration_s,
                candidates=[candidate],
                recommended_candidate_id=candidate_id,
                selected_candidate_id=candidate_id,
            )
        )
    board.save(
        "visual_plan",
        VisualPlan(
            proposal_hash="a" * 64,
            request_hash="b" * 64,
            beats=beats,
            confirmed_utc="2026-08-08T10:00:00+00:00",
            parents={
                "visual_recut_request": content_hash(request),
                "script": content_hash(script),
                "voice": content_hash(voice),
            },
        ),
    )
    board.clear_contact_sheet_approval(enable_gate=True)


def confirm_v2_plan(board: Board, durations: list[int]) -> VisualPlan:
    plan = board.load("visual_plan")
    assert isinstance(plan, VisualPlan)
    confirmed = apply_scene_selections(
        plan,
        [
            VisualSceneSelection(
                rough_cut_order=choice.rough_cut_order,
                candidate_id=choice.recommended_candidate_id,
                included=True,
                requested_duration_s=durations[choice.rough_cut_order],
            )
            for choice in plan.scene_choices
        ],
        "2026-08-09T10:00:00+00:00",
    )
    board.save("visual_plan", confirmed)
    return confirmed


def _sheet(board: Board) -> ContactSheet:
    cutlist = board.load("cutlist")
    assert isinstance(cutlist, Cutlist)
    return ContactSheet(
        png_path="sheet.png",
        cols=len(cutlist.segments),
        rows=1,
        tiles=[
            ContactSheetTile(
                order=segment.order,
                scene_number=segment.scene_number,
                frame=segment.start_frame
                + (segment.end_frame_exclusive - segment.start_frame) // 2,
                label=f"{segment.order} S{segment.scene_number}",
            )
            for segment in cutlist.segments
        ],
        parents={"cutlist": content_hash(cutlist)},
    )


def _start_and_confirm(harness: Harness) -> None:
    save_confirmed_legacy_visual_plan(harness.board)


def test_start_visual_recut_proposes_every_rough_cut_scene_and_preserves_narration(
    v2_harness: Harness,
) -> None:
    before = versions_and_hashes(v2_harness.board, "storyline", "script", "voice")

    result = tool(v2_harness, "start_visual_recut")(
        user_request="better pictures, keep voice", framing_mode="full_frame_blur"
    )

    assert result["ok"] is True
    assert result["status"] == "awaiting_user_input"
    assert len(result["scene_choices"]) == 5
    assert versions_and_hashes(
        v2_harness.board, "storyline", "script", "voice"
    ) == before
    plan = v2_harness.board.load("visual_plan")
    assert isinstance(plan, VisualPlan)
    assert [choice.rough_cut_order for choice in plan.scene_choices] == [0, 1, 2, 3, 4]
    assert v2_harness.board.meta().contact_sheet_gate is True


def test_new_v2_selection_invalidates_finished_tail_and_rebuilds_contact_sheet(
    v2_harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tool(v2_harness, "start_visual_recut")(
        user_request="all Rough-Cut scenes",
        framing_mode="full_frame_blur",
    )
    assert first["ok"] is True
    confirm_v2_plan(v2_harness.board, [10, 10, 10, 10, 10])
    assert tool(v2_harness, "build_cutlist")()["ok"] is True
    first_cutlist = v2_harness.board.load("cutlist")
    assert isinstance(first_cutlist, Cutlist)
    first_sheet = _sheet(v2_harness.board)
    v2_harness.board.save("contact_sheet", first_sheet)
    v2_harness.board.clear_contact_sheet_approval(enable_gate=True)
    v2_harness.board.set_contact_sheet_approved(
        "2026-08-09T10:01:00+00:00", content_hash(first_sheet)
    )
    from laura.short_creator.board_models import QaReport, RenderReport

    v2_harness.board.save(
        "render_report",
        RenderReport(export_id="old-export", video_s=45.0, width=1080, height=1920),
    )
    v2_harness.board.save("qa_report", QaReport(verdict="ship"))
    v2_harness.board.set_status("complete")
    old_versions: dict[str, int] = {}
    for name in ("cutlist", "contact_sheet", "render_report", "qa_report"):
        artifact = v2_harness.board.load(name)
        assert artifact is not None
        version = artifact.model_dump()["version"]
        assert isinstance(version, int)
        old_versions[name] = version
    assert v2_harness.board.meta().status == "complete"

    second = tool(v2_harness, "start_visual_recut")(
        user_request="same scenes, different lengths",
        framing_mode="full_frame_blur",
    )
    assert second["ok"] is True
    confirm_v2_plan(v2_harness.board, [9, 10, 10, 10, 10])

    for name, old_version in old_versions.items():
        assert v2_harness.board.load(name) is None
        assert old_version in v2_harness.board.versions(name)
    assert v2_harness.board.resume_point([1, 2, 3, 4, 5]) == "cutlist"

    from laura.short_creator import production_tools

    monkeypatch.setattr(production_context, "_proxy_path", lambda *_args: "proxy.mp4")
    monkeypatch.setattr(production_context, "_frame_rate", lambda *_args: (30, 1))
    monkeypatch.setattr(production_tools, "_probe_video_dims", lambda _path: (1920, 1080))
    monkeypatch.setattr(production_tools, "_find_fontfile", lambda: None)
    monkeypatch.setattr(
        production_tools,
        "_extract_sheet_tiles",
        lambda *_args, **_kwargs: (True, False, None),
    )
    monkeypatch.setattr(production_tools, "_compose_sheet_grid", lambda *_args: True)
    specs = build_production_tool_specs(
        v2_harness.db,
        v2_harness.board,
        asset_id=v2_harness.asset_id,
    )

    tail = run_deterministic_tail(
        v2_harness.board,
        specs,
        expected_scenes=[1, 2, 3, 4, 5],
    )

    assert tail.ok is True
    rebuilt = v2_harness.board.load("contact_sheet")
    assert isinstance(rebuilt, ContactSheet)
    assert rebuilt.version > old_versions["contact_sheet"]
    assert v2_harness.board.resume_point([1, 2, 3, 4, 5]) == "contact_sheet_approval"
    assert v2_harness.board.load("render_report") is None
    assert v2_harness.board.load("qa_report") is None


def test_v2_cutlist_refuses_changed_rough_cut_without_mutation(
    v2_harness: Harness,
) -> None:
    result = tool(v2_harness, "start_visual_recut")(
        user_request="all five Rough-Cut scenes",
        framing_mode="full_frame_blur",
    )
    assert result["ok"] is True
    assert len(result["scene_choices"]) == 5
    asset = repos.get_asset(v2_harness.db, v2_harness.asset_id)
    assert asset is not None
    timeline = repos.get_or_create_asset_rough_cut(
        v2_harness.db,
        str(asset["project_id"]),
        v2_harness.asset_id,
    )
    repos.replace_scenes(
        v2_harness.db,
        str(asset["project_id"]),
        str(timeline["id"]),
        [
            (index * SCENE_FRAMES, (index + 1) * SCENE_FRAMES)
            for index in range(4)
        ],
    )
    confirm_v2_plan(v2_harness.board, [10, 10, 10, 10, 10])
    before = versions_and_hashes(
        v2_harness.board,
        "storyline",
        "script",
        "voice",
        "visual_recut_request",
        "visual_plan",
    )

    built = tool(v2_harness, "build_cutlist")()

    assert built["ok"] is False
    assert "Rough-Cut" in built["reason"]
    assert v2_harness.board.load("cutlist") is None
    assert versions_and_hashes(
        v2_harness.board,
        "storyline",
        "script",
        "voice",
        "visual_recut_request",
        "visual_plan",
    ) == before


def test_v2_cutlist_refuses_changed_project_fps_without_mutation(
    v2_harness: Harness,
) -> None:
    result = tool(v2_harness, "start_visual_recut")(
        user_request="all Rough-Cut scenes at current sequence rate",
        framing_mode="full_frame_blur",
    )
    assert result["ok"] is True
    plan = v2_harness.board.load("visual_plan")
    assert isinstance(plan, VisualPlan)
    assert plan.fps == 30.0
    assert plan.voice_total_frames == 1350
    confirm_v2_plan(v2_harness.board, [10, 10, 10, 10, 10])
    asset = repos.get_asset(v2_harness.db, v2_harness.asset_id)
    assert asset is not None
    with v2_harness.db.transaction() as conn:
        conn.execute(
            "UPDATE projects SET sequence_rate_num = ?, sequence_rate_den = ? WHERE id = ?",
            (24, 1, str(asset["project_id"])),
        )
    before = versions_and_hashes(
        v2_harness.board,
        "storyline",
        "script",
        "voice",
        "visual_recut_request",
        "visual_plan",
    )

    built = tool(v2_harness, "build_cutlist")()

    assert built["ok"] is False
    assert "frame rate" in built["reason"]
    assert v2_harness.board.load("cutlist") is None
    assert versions_and_hashes(
        v2_harness.board,
        "storyline",
        "script",
        "voice",
        "visual_recut_request",
        "visual_plan",
    ) == before


def test_v2_cutlist_rejects_candidate_narrower_than_resolved_duration(
    v2_harness: Harness,
) -> None:
    result = tool(v2_harness, "start_visual_recut")(
        user_request="all Rough-Cut scenes with bounded source ranges",
        framing_mode="full_frame_blur",
    )
    assert result["ok"] is True
    plan = v2_harness.board.load("visual_plan")
    assert isinstance(plan, VisualPlan)
    first_choice = plan.scene_choices[0]
    narrow_id = first_choice.recommended_candidate_id
    narrow_candidates = [
        candidate.model_copy(
            update={
                "src_start_frame": 1000,
                "src_end_frame_exclusive": 1030,
                "thumb_frame": 1015,
                "max_duration_s": 10,
            }
        )
        if candidate.candidate_id == narrow_id
        else candidate
        for candidate in first_choice.candidates
    ]
    v2_harness.board.save(
        "visual_plan",
        plan.model_copy(
            update={
                "scene_choices": [
                    first_choice.model_copy(update={"candidates": narrow_candidates}),
                    *plan.scene_choices[1:],
                ]
            }
        ),
    )
    confirm_v2_plan(v2_harness.board, [10, 10, 10, 10, 10])

    built = tool(v2_harness, "build_cutlist")()

    assert built["ok"] is False
    assert "too short" in built["reason"]
    assert v2_harness.board.load("cutlist") is None


def test_start_visual_recut_rejects_other_framing_without_mutation(harness: Harness) -> None:
    before = versions(harness.board, "storyline", "script", "voice")

    result = tool(harness, "start_visual_recut")(
        user_request="crop it", framing_mode="crop"
    )

    assert result == {"ok": False, "reason": 'framing_mode must be "full_frame_blur"'}
    assert versions(harness.board, "storyline", "script", "voice") == before
    assert harness.board.load("visual_recut_request") is None


def test_start_visual_recut_refuses_legacy_voice_without_mutation(harness: Harness) -> None:
    script = harness.board.load("script")
    storyline = harness.board.load("storyline")
    assert isinstance(script, Script)
    assert isinstance(storyline, Storyline)
    harness.board.save(
        "voice",
        VoiceArtifact(
            script_hash=script_hash(lines_in_storyline_order(script, storyline)),
            mp3_path="legacy.mp3",
            voice_s=2.5,
        ),
    )
    before = versions(harness.board, "storyline", "script", "voice")

    result = tool(harness, "start_visual_recut")(
        user_request="better pictures", framing_mode="full_frame_blur"
    )

    assert result["ok"] is False
    assert "segmented voice" in result["reason"]
    assert versions(harness.board, "storyline", "script", "voice") == before
    assert harness.board.load("visual_recut_request") is None


def test_start_visual_recut_is_idempotent_while_same_proposal_is_pending(
    v2_harness: Harness,
) -> None:
    start = tool(v2_harness, "start_visual_recut")
    first = start(user_request="better pictures", framing_mode="full_frame_blur")
    before = versions(v2_harness.board, "visual_recut_request", "visual_plan")

    second = start(user_request="better pictures", framing_mode="full_frame_blur")

    assert first["ok"] is True
    assert second == first
    assert versions(v2_harness.board, "visual_recut_request", "visual_plan") == before


def test_pending_v2_rebuilds_after_rough_cut_drift(v2_harness: Harness) -> None:
    voice = v2_harness.board.load("voice")
    assert isinstance(voice, VoiceArtifact)
    assert voice.segments is not None
    v2_harness.board.save(
        "voice",
        voice.model_copy(
            update={
                "voice_s": 36.7,
                "segments": [
                    segment.model_copy(
                        update={"duration_s": 7.0, "offset_s": index * 7.35}
                    )
                    for index, segment in enumerate(voice.segments)
                ],
            }
        ),
    )
    start = tool(v2_harness, "start_visual_recut")
    first = start(
        user_request="same request after Rough-Cut edit",
        framing_mode="full_frame_blur",
    )
    assert first["ok"] is True
    assert len(first["scene_choices"]) == 5
    first_plan = v2_harness.board.load("visual_plan")
    assert isinstance(first_plan, VisualPlan)
    narration_before = versions_and_hashes(
        v2_harness.board, "storyline", "script", "voice"
    )
    asset = repos.get_asset(v2_harness.db, v2_harness.asset_id)
    assert asset is not None
    timeline = repos.get_or_create_asset_rough_cut(
        v2_harness.db,
        str(asset["project_id"]),
        v2_harness.asset_id,
    )
    repos.replace_scenes(
        v2_harness.db,
        str(asset["project_id"]),
        str(timeline["id"]),
        [
            (index * 375, (index + 1) * 375)
            for index in range(4)
        ],
    )

    second = start(
        user_request="same request after Rough-Cut edit",
        framing_mode="full_frame_blur",
    )

    assert second["ok"] is True
    assert len(second["scene_choices"]) == 4
    second_plan = v2_harness.board.load("visual_plan")
    assert isinstance(second_plan, VisualPlan)
    assert second_plan.version > first_plan.version
    assert second_plan.proposal_hash != first_plan.proposal_hash
    assert second_plan.rough_cut_scene_count == 4
    assert v2_harness.board.load("cutlist") is None
    assert versions_and_hashes(
        v2_harness.board, "storyline", "script", "voice"
    ) == narration_before


def test_pending_v2_rebuilds_after_project_fps_drift(v2_harness: Harness) -> None:
    start = tool(v2_harness, "start_visual_recut")
    first = start(
        user_request="same request after frame-rate edit",
        framing_mode="full_frame_blur",
    )
    assert first["ok"] is True
    first_plan = v2_harness.board.load("visual_plan")
    assert isinstance(first_plan, VisualPlan)
    assert first_plan.fps == 30.0
    assert first_plan.voice_total_frames == 1350
    narration_before = versions_and_hashes(
        v2_harness.board, "storyline", "script", "voice"
    )
    asset = repos.get_asset(v2_harness.db, v2_harness.asset_id)
    assert asset is not None
    with v2_harness.db.transaction() as conn:
        conn.execute(
            "UPDATE projects SET sequence_rate_num = ?, sequence_rate_den = ? WHERE id = ?",
            (24, 1, str(asset["project_id"])),
        )

    second = start(
        user_request="same request after frame-rate edit",
        framing_mode="full_frame_blur",
    )

    assert second["ok"] is True
    second_plan = v2_harness.board.load("visual_plan")
    assert isinstance(second_plan, VisualPlan)
    assert second_plan.version > first_plan.version
    assert second_plan.proposal_hash != first_plan.proposal_hash
    assert second_plan.fps == 24.0
    assert second_plan.voice_total_frames == 1080
    assert v2_harness.board.load("cutlist") is None
    assert versions_and_hashes(
        v2_harness.board, "storyline", "script", "voice"
    ) == narration_before


def test_pending_v2_rebuilds_stale_voice_frame_projection(v2_harness: Harness) -> None:
    start = tool(v2_harness, "start_visual_recut")
    first = start(
        user_request="same request with current Voice projection",
        framing_mode="full_frame_blur",
    )
    assert first["ok"] is True
    plan = v2_harness.board.load("visual_plan")
    assert isinstance(plan, VisualPlan)
    assert plan.voice_total_frames == 1350
    v2_harness.board.save(
        "visual_plan",
        plan.model_copy(update={"voice_total_frames": 1349}),
    )
    stale = v2_harness.board.load("visual_plan")
    assert isinstance(stale, VisualPlan)
    narration_before = versions_and_hashes(
        v2_harness.board, "storyline", "script", "voice"
    )

    second = start(
        user_request="same request with current Voice projection",
        framing_mode="full_frame_blur",
    )

    assert second["ok"] is True
    refreshed = v2_harness.board.load("visual_plan")
    assert isinstance(refreshed, VisualPlan)
    assert refreshed.version > stale.version
    assert refreshed.voice_total_frames == 1350
    assert refreshed.fps == 30.0
    assert v2_harness.board.load("cutlist") is None
    assert versions_and_hashes(
        v2_harness.board, "storyline", "script", "voice"
    ) == narration_before


def test_visual_cutlist_is_full_frame_and_uses_voice_segment_durations(
    harness: Harness,
) -> None:
    _start_and_confirm(harness)

    result = tool(harness, "build_cutlist")()

    cutlist = harness.board.load("cutlist")
    plan = harness.board.load("visual_plan")
    script = harness.board.load("script")
    voice = harness.board.load("voice")
    assert result["ok"] is True
    assert isinstance(cutlist, Cutlist)
    assert isinstance(plan, VisualPlan)
    assert isinstance(script, Script)
    assert isinstance(voice, VoiceArtifact)
    assert all(
        segment.roi is None and segment.zoom_start_s is None
        for segment in cutlist.segments
    )
    assert [
        segment.end_frame_exclusive - segment.start_frame for segment in cutlist.segments
    ] == [40, 54]
    assert cutlist.parents == {
        "script": content_hash(script),
        "voice": content_hash(voice),
        "visual_plan": content_hash(plan),
    }


def test_visual_cutlist_refuses_stale_request_hashes(harness: Harness) -> None:
    _start_and_confirm(harness)
    plan = harness.board.load("visual_plan")
    request = harness.board.load("visual_recut_request")
    assert isinstance(plan, VisualPlan)
    assert isinstance(request, VisualRecutRequest)
    harness.board.save(
        "visual_recut_request", request.model_copy(update={"script_hash": "0" * 64})
    )
    harness.board.save("visual_plan", plan)

    result = tool(harness, "build_cutlist")()

    assert result["ok"] is False
    assert "current script and voice" in result["reason"]
    assert harness.board.load("cutlist") is None


def test_visual_cutlist_refuses_beats_out_of_voice_order(harness: Harness) -> None:
    _start_and_confirm(harness)
    plan = harness.board.load("visual_plan")
    assert isinstance(plan, VisualPlan)
    harness.board.save(
        "visual_plan",
        plan.model_copy(update={"beats": list(reversed(plan.beats))}),
    )

    result = tool(harness, "build_cutlist")()

    assert result == {
        "ok": False,
        "reason": "visual plan beats must follow voice segment order",
    }
    assert harness.board.load("cutlist") is None


def test_visual_cutlist_refuses_candidate_from_another_voice_beat(
    harness: Harness,
) -> None:
    _start_and_confirm(harness)
    plan = harness.board.load("visual_plan")
    assert isinstance(plan, VisualPlan)
    first = plan.beats[0]
    selected_id = first.selected_candidate_id
    assert selected_id is not None
    mismatched_candidates = [
        candidate.model_copy(update={"voice_segment_index": 1})
        if candidate.candidate_id == selected_id
        else candidate
        for candidate in first.candidates
    ]
    harness.board.save(
        "visual_plan",
        plan.model_copy(
            update={
                "beats": [
                    first.model_copy(update={"candidates": mismatched_candidates}),
                    *plan.beats[1:],
                ]
            }
        ),
    )

    result = tool(harness, "build_cutlist")()

    assert result == {
        "ok": False,
        "reason": "selected visual candidate does not match its beat",
    }
    assert harness.board.load("cutlist") is None


def test_render_refuses_before_current_sheet_approval(harness: Harness) -> None:
    _start_and_confirm(harness)
    assert tool(harness, "build_cutlist")()["ok"] is True
    calls = Counter()

    result = tool(harness, "render_production", render_segments=calls.raise_on_call)()

    assert result == {"ok": False, "reason": "contact sheet approval required"}
    assert calls.value == 0


def test_render_refuses_stale_sheet_approval_without_calling_renderer(
    harness: Harness,
) -> None:
    _start_and_confirm(harness)
    assert tool(harness, "build_cutlist")()["ok"] is True
    sheet = _sheet(harness.board)
    harness.board.save("contact_sheet", sheet)
    harness.board.clear_contact_sheet_approval(enable_gate=True)
    harness.board.set_contact_sheet_approved(
        "2026-08-08T10:01:00+00:00", "f" * 64
    )
    calls = Counter()

    result = tool(harness, "render_production", render_segments=calls.raise_on_call)()

    assert result == {"ok": False, "reason": "contact sheet approval required"}
    assert calls.value == 0


def test_render_approved_visual_recut_uses_blur_fit(harness: Harness) -> None:
    _start_and_confirm(harness)
    assert tool(harness, "build_cutlist")()["ok"] is True
    sheet = _sheet(harness.board)
    harness.board.save("contact_sheet", sheet)
    harness.board.clear_contact_sheet_approval(enable_gate=True)
    harness.board.set_contact_sheet_approved(
        "2026-08-08T10:01:00+00:00", content_hash(sheet)
    )
    renderer = FakeRenderer()

    result = tool(
        harness,
        "render_production",
        render_segments=renderer,
        probe_duration=lambda _path: 2.8,
    )()

    assert result["ok"] is True
    assert len(renderer.calls) == 1
    assert renderer.calls[0]["fit"] == "blur"
    assert renderer.calls[0]["zoom"] == [None, None]


def test_save_contact_sheet_emits_visual_plan_metadata(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    _start_and_confirm(harness)
    assert tool(harness, "build_cutlist")()["ok"] is True
    from laura.short_creator import production_tools

    monkeypatch.setattr(production_context, "_proxy_path", lambda *_args: "proxy.mp4")
    monkeypatch.setattr(production_context, "_frame_rate", lambda *_args: (30, 1))
    monkeypatch.setattr(production_tools, "_probe_video_dims", lambda _path: (1920, 1080))
    monkeypatch.setattr(production_tools, "_find_fontfile", lambda: None)
    monkeypatch.setattr(
        production_tools,
        "_extract_sheet_tiles",
        lambda *_args, **_kwargs: (True, False, None),
    )
    monkeypatch.setattr(production_tools, "_compose_sheet_grid", lambda *_args: True)

    result = tool(harness, "save_contact_sheet")()

    assert result["ok"] is True
    plan = harness.board.load("visual_plan")
    assert isinstance(plan, VisualPlan)
    expected = [
        {
            "src_start_frame": next(
                candidate.src_start_frame
                for candidate in beat.candidates
                if candidate.candidate_id == beat.selected_candidate_id
            ),
            "src_end_frame_exclusive": next(
                candidate.src_end_frame_exclusive
                for candidate in beat.candidates
                if candidate.candidate_id == beat.selected_candidate_id
            ),
            "narration_excerpt": beat.narration_text,
            "rationale": next(
                candidate.rationale
                for candidate in beat.candidates
                if candidate.candidate_id == beat.selected_candidate_id
            ),
        }
        for beat in plan.beats
    ]
    assert [
        {
            "src_start_frame": tile["src_start_frame"],
            "src_end_frame_exclusive": tile["src_end_frame_exclusive"],
            "narration_excerpt": tile["narration_excerpt"],
            "rationale": tile["rationale"],
        }
        for tile in result["tiles"]
    ] == expected
