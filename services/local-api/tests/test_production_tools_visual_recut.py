"""Production tools for a visual-only, full-frame recut of preserved narration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
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
    VisualPlan,
    VisualRecutRequest,
    VoiceArtifact,
    VoiceSegment,
    content_hash,
    lines_in_storyline_order,
    script_hash,
)
from laura.short_creator.production_tools import ProductionDeps, build_production_tool_specs

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


def _seed_two_scenes(tmp_path: Path) -> tuple[Database, str]:
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
        src_out_frame_exclusive=SCENE_FRAMES * 2,
        seq_in_frame=0,
        seq_out_frame_exclusive=SCENE_FRAMES * 2,
        lane=0,
        role="base",
    )
    repos.replace_scenes(
        db,
        project["id"],
        timeline["id"],
        [(0, SCENE_FRAMES), (SCENE_FRAMES, SCENE_FRAMES * 2)],
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


def versions(board: Board, *names: str) -> tuple[int, ...]:
    return tuple(board.load(name).version for name in names)


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


def confirm_visual_plan(board: Board) -> None:
    plan = board.load("visual_plan")
    assert isinstance(plan, VisualPlan)
    board.save(
        "visual_plan",
        plan.model_copy(
            update={
                "beats": [
                    beat.model_copy(
                        update={"selected_candidate_id": beat.recommended_candidate_id}
                    )
                    for beat in plan.beats
                ],
                "confirmed_utc": "2026-08-08T10:00:00+00:00",
            }
        ),
    )


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
    result = tool(harness, "start_visual_recut")(
        user_request="better pictures, keep voice", framing_mode="full_frame_blur"
    )
    assert result["ok"] is True
    confirm_visual_plan(harness.board)


def test_start_visual_recut_preserves_script_and_voice_versions(
    finished_harness: Harness,
) -> None:
    before = versions(finished_harness.board, "storyline", "script", "voice")

    result = tool(finished_harness, "start_visual_recut")(
        user_request="better pictures, keep voice", framing_mode="full_frame_blur"
    )

    assert result["ok"] is True
    assert result["status"] == "awaiting_user_input"
    assert versions(finished_harness.board, "storyline", "script", "voice") == before
    assert isinstance(finished_harness.board.load("visual_plan"), VisualPlan)
    assert finished_harness.board.meta().contact_sheet_gate is True


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
    harness: Harness,
) -> None:
    start = tool(harness, "start_visual_recut")
    first = start(user_request="better pictures", framing_mode="full_frame_blur")
    before = versions(harness.board, "visual_recut_request", "visual_plan")

    second = start(user_request="better pictures", framing_mode="full_frame_blur")

    assert first["ok"] is True
    assert second == first
    assert versions(harness.board, "visual_recut_request", "visual_plan") == before


def test_visual_cutlist_is_full_frame_and_uses_voice_segment_durations(
    harness: Harness,
) -> None:
    _start_and_confirm(harness)

    result = tool(harness, "build_cutlist")()

    cutlist = harness.board.load("cutlist")
    plan = harness.board.load("visual_plan")
    assert result["ok"] is True
    assert isinstance(cutlist, Cutlist)
    assert isinstance(plan, VisualPlan)
    assert all(
        segment.roi is None and segment.zoom_start_s is None
        for segment in cutlist.segments
    )
    assert [
        segment.end_frame_exclusive - segment.start_frame for segment in cutlist.segments
    ] == [40, 54]
    assert cutlist.parents == {
        "script": content_hash(harness.board.load("script")),
        "voice": content_hash(harness.board.load("voice")),
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

    monkeypatch.setattr(production_tools.context, "_proxy_path", lambda *_args: "proxy.mp4")
    monkeypatch.setattr(production_tools.context, "_frame_rate", lambda *_args: (30, 1))
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
