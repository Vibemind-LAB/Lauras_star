"""Deterministic post-gate tail (spec 2026-08-05-modular-production-design.md).

The tail runs voice -> cutlist -> contact_sheet -> render as plain tool calls with
skip semantics (resume_point decides), one retry per step, and NO write path to
script/storyline. Tests drive it with a fake spec list — no autogen, no ffmpeg.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from laura.short_creator.production_pipeline import (
    _STEP_BY_RESUME_POINT,
    TailOutcome,
    run_deterministic_tail,
)


@dataclass
class _FakeBoard:
    """resume_point is scripted: each successful step consumes the next entry."""

    points: list[str]

    def resume_point(self, expected: list[int]) -> str:
        return self.points[0]

    def advance(self) -> None:
        self.points.pop(0)


@dataclass
class _Recorder:
    calls: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    def sink(self, event: dict[str, Any]) -> None:
        self.events.append(event)


def _specs(board: _FakeBoard, rec: _Recorder,
           fail: dict[str, int] | None = None) -> list[Any]:
    """Fake ToolSpecs: every chain tool succeeds (advancing the board) unless
    `fail[name]` > 0 (counts down — lets one test model fail-once-then-succeed)."""
    from laura.short_creator.toolset import ToolSpec

    remaining = dict(fail or {})

    def make(name: str) -> Callable[..., dict[str, Any]]:
        def tool(**kwargs: Any) -> dict[str, Any]:
            rec.calls.append(name)
            if remaining.get(name, 0) > 0:
                remaining[name] -= 1
                return {"ok": False, "reason": f"{name} transient"}
            board.advance()
            return {"ok": True}
        tool.__name__ = name
        return tool

    return [
        ToolSpec(name=n, description=n, func=make(n))
        for n in ("synthesize_script_voice", "build_cutlist",
                  "save_contact_sheet", "render_production")
    ]


def _run(board: _FakeBoard, rec: _Recorder, specs: list[Any]) -> TailOutcome:
    return run_deterministic_tail(
        board, specs, expected_scenes=[1], event_sink=rec.sink
    )


def test_happy_path_runs_all_four_steps_in_chain_order() -> None:
    board = _FakeBoard(["voice", "cutlist", "contact_sheet", "render_report", "qa_report"])
    rec = _Recorder()
    outcome = _run(board, rec, _specs(board, rec))
    assert outcome.ok and outcome.failed_step is None
    assert rec.calls == ["synthesize_script_voice", "build_cutlist",
                         "save_contact_sheet", "render_production"]


def test_skip_semantics_resumes_mid_chain() -> None:
    board = _FakeBoard(["cutlist", "contact_sheet", "render_report", "qa_report"])
    rec = _Recorder()
    outcome = _run(board, rec, _specs(board, rec))
    assert outcome.ok
    assert rec.calls[0] == "build_cutlist" and "synthesize_script_voice" not in rec.calls


def test_one_retry_recovers_a_transient_failure() -> None:
    board = _FakeBoard(["voice", "cutlist", "contact_sheet", "render_report", "qa_report"])
    rec = _Recorder()
    outcome = _run(board, rec, _specs(board, rec, fail={"build_cutlist": 1}))
    assert outcome.ok
    assert rec.calls.count("build_cutlist") == 2


def test_double_failure_names_the_step_and_keeps_reason() -> None:
    board = _FakeBoard(["voice", "cutlist", "contact_sheet", "render_report", "qa_report"])
    rec = _Recorder()
    outcome = _run(board, rec, _specs(board, rec, fail={"render_production": 2}))
    assert not outcome.ok
    assert outcome.failed_step == "render_production"
    assert "transient" in (outcome.reason or "")


def test_no_progress_after_ok_result_fails_instead_of_looping() -> None:
    """A tool that reports ok:True but leaves resume_point unchanged must end the
    run as a failure — never spin."""
    board = _FakeBoard(["voice", "voice", "voice", "voice", "voice"])
    rec = _Recorder()

    from laura.short_creator.toolset import ToolSpec

    def liar(**kwargs: Any) -> dict[str, Any]:
        rec.calls.append("synthesize_script_voice")
        return {"ok": True}

    specs = [ToolSpec(name="synthesize_script_voice", description="", func=liar)]
    outcome = _run(board, rec, specs)
    assert not outcome.ok and outcome.failed_step == "synthesize_script_voice"
    assert "no progress" in (outcome.reason or "")


def test_events_mirror_the_team_shapes() -> None:
    board = _FakeBoard(["voice", "cutlist", "contact_sheet", "render_report", "qa_report"])
    rec = _Recorder()
    _run(board, rec, _specs(board, rec))
    kinds = [e["type"] for e in rec.events]
    assert kinds[:2] == ["tool_call", "tool_result"]
    assert all(e.get("agent") == "pipeline" for e in rec.events if e["type"] == "tool_call")


def test_tail_tool_menu_is_pinned_exactly() -> None:
    """The tail may only ever see the four chain tools — no save_script_chapter,
    no save_storyline, nothing else (structural no-rewrite guarantee)."""
    assert tuple(_STEP_BY_RESUME_POINT.values()) == (
        "synthesize_script_voice", "build_cutlist",
        "save_contact_sheet", "render_production",
    )
    assert tuple(_STEP_BY_RESUME_POINT.keys()) == (
        "voice", "cutlist", "contact_sheet", "render_report",
    )
