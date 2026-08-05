"""Deterministic post-gate tail (spec 2026-08-05-modular-production-design.md).

The tail runs voice -> cutlist -> contact_sheet -> render as plain tool calls with
skip semantics (resume_point decides), one retry per step, and NO write path to
script/storyline. Tests drive it with a fake spec list — no autogen, no ffmpeg.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from laura.short_creator.production_pipeline import (
    _STEP_BY_RESUME_POINT,
    TailOutcome,
    run_deterministic_tail,
    run_tail_with_qa,
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


def test_oscillating_resume_point_ends_the_run_with_cycled_not_a_hang() -> None:
    """The no-progress guard only compares against the IMMEDIATELY prior resume
    point, so a two-step oscillation (voice -> cutlist -> voice -> ...) — possible
    under concurrent/downstream invalidation on a live board — slips past it: each
    single step looks like progress relative to its predecessor. The iteration cap
    must still end the run as a failure instead of looping forever.

    The fake board's own safety_cap (independent of the production cap) guards the
    TEST from hanging if `run_deterministic_tail`'s cap ever regresses: it turns a
    reintroduced infinite loop into a fast, clear assertion failure instead of a
    hang, without relying on the code under test to behave correctly.
    """
    import itertools
    from collections.abc import Iterator

    from laura.short_creator.toolset import ToolSpec

    @dataclass
    class _OscillatingBoard:
        cycle: Iterator[str]
        safety_cap: int = 1000
        calls: int = 0

        def resume_point(self, expected: list[int]) -> str:
            self.calls += 1
            if self.calls > self.safety_cap:
                raise AssertionError(
                    "iteration cap regression: run_deterministic_tail did not stop"
                )
            return next(self.cycle)

    board = _OscillatingBoard(cycle=itertools.cycle(["voice", "cutlist"]))
    rec = _Recorder()

    def make(name: str) -> Callable[..., dict[str, Any]]:
        def tool(**kwargs: Any) -> dict[str, Any]:
            rec.calls.append(name)
            return {"ok": True}
        return tool

    specs = [
        ToolSpec(name=n, description=n, func=make(n))
        for n in ("synthesize_script_voice", "build_cutlist",
                  "save_contact_sheet", "render_production")
    ]

    outcome = run_deterministic_tail(
        board, specs, expected_scenes=[1], event_sink=rec.sink
    )
    assert not outcome.ok
    assert "cycled" in (outcome.reason or "")
    # Small, well-defined bound: 2*len(chain) iterations, ~2 resume_point calls each,
    # plus one closing call — nowhere near the fake's 1000-call safety net.
    assert board.calls < 50


def test_event_sink_exception_does_not_break_the_run() -> None:
    """MINOR 1: the sink raising on every call must not stop the chain — `_emit`
    swallows sink errors, but that contract needs its own test, not just an
    incidental pass in the happy-path test (which uses a non-raising sink)."""
    board = _FakeBoard(["voice", "cutlist", "contact_sheet", "render_report", "qa_report"])
    rec = _Recorder()

    def raising_sink(event: dict[str, Any]) -> None:
        raise RuntimeError("sink boom")

    outcome = run_deterministic_tail(
        board, _specs(board, rec), expected_scenes=[1], event_sink=raising_sink
    )
    assert outcome.ok and outcome.failed_step is None
    assert rec.calls == ["synthesize_script_voice", "build_cutlist",
                         "save_contact_sheet", "render_production"]


def test_raising_tool_recovers_on_retry() -> None:
    """MINOR 2a: a tool that RAISES (instead of returning ok:False) on the first
    call and succeeds on the retry must let the run recover, same as a returned
    failure would."""
    board = _FakeBoard(["voice", "cutlist", "contact_sheet", "render_report", "qa_report"])
    rec = _Recorder()

    from laura.short_creator.toolset import ToolSpec

    attempts = {"n": 0}

    def flaky_voice(**kwargs: Any) -> dict[str, Any]:
        rec.calls.append("synthesize_script_voice")
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("transient boom")
        board.advance()
        return {"ok": True}

    specs = [
        ToolSpec(name="synthesize_script_voice", description="", func=flaky_voice)
        if s.name == "synthesize_script_voice" else s
        for s in _specs(board, rec)
    ]
    outcome = _run(board, rec, specs)
    assert outcome.ok and outcome.failed_step is None
    assert rec.calls == ["synthesize_script_voice", "synthesize_script_voice",
                         "build_cutlist", "save_contact_sheet", "render_production"]


def test_run_tail_with_qa_calls_qa_after_successful_chain() -> None:
    board = _FakeBoard(["voice", "cutlist", "contact_sheet", "render_report", "qa_report"])
    rec = _Recorder()
    qa_calls: list[str] = []

    def fake_qa(db: Any, config: Any, stage: str, kind: str, task: str) -> Any:
        qa_calls.append(task)
        from laura.short_creator.orchestrator import StageOutcome
        return StageOutcome(status="ok", weak=False, summary="ship", team="magentic", stage="A")

    tail, qa = run_tail_with_qa(
        None, board, None, asset_id="a", deps=None, event_sink=rec.sink,
        expected_scenes=[1], specs=_specs(board, rec), qa_execute=fake_qa,
    )
    assert tail.ok and qa is not None and qa.status == "ok"
    assert qa_calls and "save_qa_report" in qa_calls[0]


def test_run_tail_with_qa_skips_qa_when_chain_failed() -> None:
    board = _FakeBoard(["voice", "cutlist", "contact_sheet", "render_report", "qa_report"])
    rec = _Recorder()
    tail, qa = run_tail_with_qa(
        None, board, None, asset_id="a", deps=None, event_sink=rec.sink,
        expected_scenes=[1],
        specs=_specs(board, rec, fail={"synthesize_script_voice": 2}),
        qa_execute=lambda *a: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    assert not tail.ok and qa is None


def test_double_raising_tool_fails_with_exception_type_in_reason() -> None:
    """MINOR 2b: a tool that raises on both attempts must fail honestly, and the
    reason must carry the exception type name (not just its message) so a
    double-raise is distinguishable from a returned ok:False failure."""
    board = _FakeBoard(["voice", "cutlist", "contact_sheet", "render_report", "qa_report"])
    rec = _Recorder()

    from laura.short_creator.toolset import ToolSpec

    def always_raises(**kwargs: Any) -> dict[str, Any]:
        rec.calls.append("synthesize_script_voice")
        raise RuntimeError("boom")

    specs = [ToolSpec(name="synthesize_script_voice", description="", func=always_raises)]
    outcome = _run(board, rec, specs)
    assert not outcome.ok
    assert outcome.failed_step == "synthesize_script_voice"
    assert "RuntimeError" in (outcome.reason or "")


# --- C1/M1 (2026-08-05 final review): render_production's revision-cap refusal ------------
# render_production's revision-cap branch (production_tools.py) reports ok:False with
# `final`/`stale` and the real diagnosis under `note`, not `reason`. A bare `reason` fallback
# silently dropped that diagnosis, AND the retry loop burned its one retry on a call that was
# always going to say the same thing (the refusal is deterministic — a second identical call
# cannot come back differently).


def test_cap_refusal_shape_fails_immediately_without_retry() -> None:
    board = _FakeBoard(["voice", "cutlist", "contact_sheet", "render_report", "qa_report"])
    rec = _Recorder()

    from laura.short_creator.toolset import ToolSpec

    def capped_render(**kwargs: Any) -> dict[str, Any]:
        rec.calls.append("render_production")
        return {
            "ok": False, "final": True, "stale": True, "export_id": "e1", "checks": [],
            "note": "revision limit reached (2 renders); shipping this cut instead of "
                    "rendering again — WARNING: this render was made from an earlier script",
        }

    specs = [
        ToolSpec(name="render_production", description="", func=capped_render)
        if s.name == "render_production" else s
        for s in _specs(board, rec)
    ]
    outcome = _run(board, rec, specs)
    assert not outcome.ok
    assert outcome.failed_step == "render_production"
    assert rec.calls.count("render_production") == 1, "a deterministic cap refusal must not retry"
    assert "revision limit reached" in (outcome.reason or "")


def test_make_qa_execute_requires_a_tool_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """I1 (2026-08-05 final review): _make_qa_execute must ask _make_default_execute for
    require_tool_call=True — the same 2026-08-04 incident guard _parse_outcome already gives
    follow-up runs, applied to the QA stage: a QA turn that finishes without ever calling
    save_qa_report must become a hard_fail, not a silent ship."""
    import laura.short_creator.production_orchestrator as production_orchestrator
    from laura.short_creator.production_pipeline import _make_qa_execute

    captured: dict[str, Any] = {}

    def fake_make_default_execute(
        board: Any, asset_id: Any, deps: Any, event_sink: Any = None, *,
        require_tool_call: bool = False, agent_names: Any = None,
    ) -> Any:
        captured["require_tool_call"] = require_tool_call
        captured["agent_names"] = agent_names
        return lambda *a, **k: None

    monkeypatch.setattr(
        production_orchestrator, "_make_default_execute", fake_make_default_execute
    )

    _make_qa_execute(board=None, asset_id="a", deps=None, event_sink=None)

    assert captured["require_tool_call"] is True
    assert captured["agent_names"] == ("qa_reviewer",)
