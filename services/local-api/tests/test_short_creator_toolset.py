"""In-process tool bridge for the short-creator agents (Iteration 3/9).

Decision: the agents run in the same process as the db + Laura's tool_* funcs,
so we wrap those functions as in-process AutoGen tools instead of spawning the
stdio MCP server. ``build_tool_specs`` (db captured) is pure and tested against
a real in-memory db; ``build_function_tools`` is the only autogen-touching
function (lazy import), tested with a fake FunctionTool.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from laura.db import repos
from laura.db.database import Database
from laura.mcp import tools as mcp_tools
from laura.short_creator import toolset

EXPECTED_TOOLS = {
    "next_action",
    "search_visual_moments",
    "extract_shorts",
    "list_short_candidates",
    "explain_candidate",
    "score_visual_hook",
    "get_similar_segments",
    "build_roughcut",
    "render_timeline",
    "job_status",
    "describe_moment",
    "transcript_window",
    "transcript_overview",
    "render_short",
    "check_voice_alignment",
    "pick_best_candidate",
    "pick_best_candidates",
    "scene_transcripts",
    "rank_scenes_by_topic",
    "render_scenes",
    "synthesize_voiceover",
}


def test_render_scenes_fans_out_one_export_per_format(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_segments(_db: Database, _aid: str, numbers: list[int]) -> list[tuple[int, int]]:
        assert numbers == [1, 2]
        return [(0, 100), (300, 400)]

    def fake_render(_db: Database, asset_id: str, segments: Any, **kw: Any) -> dict[str, Any]:
        calls.append({"asset_id": asset_id, "segments": segments, **kw})
        return {"ok": True, "export_id": f"e{len(calls)}", "job_id": f"j{len(calls)}"}

    monkeypatch.setattr(toolset, "_scene_segments", fake_segments)
    monkeypatch.setattr(mcp_tools, "tool_render_segments", fake_render)
    specs = {s.name: s for s in toolset.build_tool_specs(db)}
    out = specs["render_scenes"].func("a1", [1, 2], ["insta", "x", "linkedin"], None, "blur")
    assert out["ok"] is True
    assert [r["format"] for r in out["renders"]] == ["insta", "x", "linkedin"]
    assert [c["vertical"] for c in calls] == [True, False, True]
    assert [c["out_size"] for c in calls] == [(1080, 1920), (1920, 1080), (1080, 1080)]
    assert all(c["fit"] == "blur" for c in calls)


def test_synthesize_voiceover_graceful_without_backend(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(toolset, "resolve_voice_backend", lambda: None)
    specs = {s.name: s for s in toolset.build_tool_specs(db)}

    out = specs["synthesize_voiceover"].func("any-asset", "Skript")

    assert out["ok"] is False
    assert "LAURA_ELEVENLABS_API_KEY" in out["reason"]


def test_synthesize_voiceover_writes_into_project_workspace(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = repos.create_project(
        db,
        name="p",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(tmp_path / "proj"),
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="a",
        source_path=str(tmp_path / "a.mp4"),
    )
    received: dict[str, Any] = {}

    class FakeBackend:
        def synthesize(self, text: str, out_path: Path) -> dict[str, Any]:
            received["text"] = text
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"MP3")
            return {"ok": True, "path": str(out_path), "bytes": 3}

    monkeypatch.setattr(toolset, "resolve_voice_backend", lambda: FakeBackend())
    specs = {s.name: s for s in toolset.build_tool_specs(db)}

    out = specs["synthesize_voiceover"].func(asset["id"], "  Volle Energie!  ")

    assert out["ok"] is True
    path = Path(out["voiceover_path"])
    assert path.exists() and path.suffix == ".mp3"
    assert path.parent == tmp_path / "proj" / "voiceovers"
    assert received["text"] == "Volle Energie!"  # stripped before synthesis
    assert out["chars"] == len("Volle Energie!")


def test_synthesize_voiceover_rejects_empty_script(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeBackend:
        def synthesize(self, text: str, out_path: Path) -> dict[str, Any]:
            raise AssertionError("must not be called for an empty script")

    monkeypatch.setattr(toolset, "resolve_voice_backend", lambda: FakeBackend())
    specs = {s.name: s for s in toolset.build_tool_specs(db)}

    out = specs["synthesize_voiceover"].func("any-asset", "   ")

    assert out == {"ok": False, "reason": "empty script"}


def test_build_tool_specs_exposes_expected_tools(db: Database) -> None:
    specs = toolset.build_tool_specs(db)
    names = {s.name for s in specs}
    assert names >= EXPECTED_TOOLS
    # Every spec carries a non-empty LLM-facing description and a callable.
    for s in specs:
        assert s.description.strip()
        assert callable(s.func)


def test_next_action_wrapper_calls_through_to_real_tool(db: Database) -> None:
    # Smoke: the wrapper injects db and calls the real tool_next_action; an unknown
    # asset must resolve to found=False (a pure DB read — no models, no autogen).
    specs = {s.name: s for s in toolset.build_tool_specs(db)}
    result = specs["next_action"].func("no-such-asset")
    assert result.get("found") is False


def test_job_status_wrapper_calls_through_to_real_tool(db: Database) -> None:
    specs = {s.name: s for s in toolset.build_tool_specs(db)}
    result = specs["job_status"].func("no-such-job")
    assert result.get("found") is False


def test_extract_shorts_waits_for_the_job_then_reports_count(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Async extraction: the wrapper must poll the job to terminal and re-list, so the scout
    # never sees an empty candidate list right after enqueueing (live-run finding).
    statuses = iter(["queued", "running", "succeeded"])
    monkeypatch.setattr(toolset, "_sleep", lambda _s: None)
    monkeypatch.setattr(
        mcp_tools,
        "tool_extract_shorts",
        lambda _db, _aid, **_kw: {"ok": True, "job_id": "j1", "asset_id": _aid},
    )
    monkeypatch.setattr(
        mcp_tools, "tool_job_status", lambda _db, _jid: {"found": True, "status": next(statuses)}
    )
    monkeypatch.setattr(
        mcp_tools, "tool_list_short_candidates", lambda _db, _aid: {"count": 42, "candidates": []}
    )
    specs = {s.name: s for s in toolset.build_tool_specs(db)}
    out = specs["extract_shorts"].func("a1")
    assert out["job_final_status"] == "succeeded"
    assert out["count"] == 42


def test_pick_best_candidate_prefers_score_near_target_length(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Deterministic choice: among non-rejected candidates, prefer high score but stay near the
    # target length (live-run finding: a 7B Director does not reliably emit CHOSEN, so the
    # editor needs a dependable default; the Director's explicit choice can still override).
    fps = 30
    candidates = [
        # 60s long, top score -> length penalty should beat it for a 20s target
        {
            "id": "long",
            "score": 5.0,
            "rejected": False,
            "start_frame": 0,
            "end_frame_exclusive": 60 * fps,
        },
        # 21s, good score -> best fit
        {
            "id": "fit",
            "score": 4.0,
            "rejected": False,
            "start_frame": 0,
            "end_frame_exclusive": 21 * fps,
        },
        # 20s, but rejected -> excluded
        {
            "id": "rej",
            "score": 9.0,
            "rejected": True,
            "start_frame": 0,
            "end_frame_exclusive": 20 * fps,
        },
    ]
    monkeypatch.setattr(
        mcp_tools,
        "tool_list_short_candidates",
        lambda _db, _aid: {"count": len(candidates), "candidates": candidates},
    )
    monkeypatch.setattr(toolset, "_asset_fps", lambda _db, _aid: float(fps))
    specs = {s.name: s for s in toolset.build_tool_specs(db)}
    out = specs["pick_best_candidate"].func("a1", 20)
    assert out["ok"] is True
    assert out["candidate_id"] == "fit"
    assert out["duration_s"] == 21.0


def test_pick_best_candidates_multi_scene_chronological(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Greedy by score, non-overlapping, stops near the target, returns STORY order.
    fps = 30
    rows = [
        {
            "id": "late",
            "score": 5.0,
            "rejected": False,
            "start_frame": 9000,
            "end_frame_exclusive": 9000 + 8 * fps,
        },  # 8s, best score
        {
            "id": "early",
            "score": 4.0,
            "rejected": False,
            "start_frame": 100,
            "end_frame_exclusive": 100 + 7 * fps,
        },  # 7s
        {
            "id": "overlap",
            "score": 4.5,
            "rejected": False,
            "start_frame": 9100,
            "end_frame_exclusive": 9100 + 6 * fps,
        },  # overlaps "late"
        {
            "id": "mid",
            "score": 3.0,
            "rejected": False,
            "start_frame": 5000,
            "end_frame_exclusive": 5000 + 6 * fps,
        },  # 6s
        {
            "id": "rej",
            "score": 9.0,
            "rejected": True,
            "start_frame": 200,
            "end_frame_exclusive": 200 + 5 * fps,
        },
    ]
    monkeypatch.setattr(
        mcp_tools,
        "tool_list_short_candidates",
        lambda _db, _aid: {"count": len(rows), "candidates": rows},
    )
    monkeypatch.setattr(toolset, "_asset_fps", lambda _db, _aid: float(fps))
    specs = {s.name: s for s in toolset.build_tool_specs(db)}
    out = specs["pick_best_candidates"].func("a1", 20)
    assert out["ok"] is True
    # late(8s) + early(7s) + mid(6s) = 21s >= 20; overlap + rejected excluded; story order:
    assert out["candidate_ids"] == ["early", "mid", "late"]
    assert out["total_seconds"] == 21.0


def test_pick_best_candidate_no_candidates_graceful(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        mcp_tools, "tool_list_short_candidates", lambda _db, _aid: {"count": 0, "candidates": []}
    )
    specs = {s.name: s for s in toolset.build_tool_specs(db)}
    out = specs["pick_best_candidate"].func("a1", 20)
    assert out["ok"] is False


def test_build_function_tools_missing_extra_raises(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "autogen_core", None)
    with pytest.raises(RuntimeError, match="autoshort"):
        toolset.build_function_tools(db)


def test_build_function_tools_wraps_every_spec(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeFunctionTool:
        def __init__(self, func: object, *, name: str = "", description: str = "") -> None:
            self.func = func
            self.name = name
            self.description = description

    core = types.ModuleType("autogen_core")
    core_tools = types.ModuleType("autogen_core.tools")
    core_tools.FunctionTool = FakeFunctionTool  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "autogen_core", core)
    monkeypatch.setitem(sys.modules, "autogen_core.tools", core_tools)

    tools = toolset.build_function_tools(db)
    built = {t.name for t in tools}
    assert built >= EXPECTED_TOOLS
    assert all(t.description.strip() for t in tools)
