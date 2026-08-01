"""run_overview_scout: one agent PICKS AND ORDERS pre-built candidate windows by index and
never invents frames (spec 2026-07-31-auto-overview-design.md §4).

Injected ``runner`` fakes stand in for the real LLM — no autogen, no network, no DB.
"""

from __future__ import annotations

import json

from laura.short_creator.overview_scout import run_overview_scout
from laura.short_creator.overview_windows import Candidate
from laura.short_creator.providers import AgentConfig, resolve_from_env

FPS = {"a": (30, 1), "b": (30, 1)}


def _candidates() -> list[Candidate]:
    return [
        Candidate("a", "A", 1, 0, 300, "alpha"),
        Candidate("b", "B", 2, 0, 300, "beta"),
        Candidate("a", "A", 3, 600, 900, "gamma"),
    ]


def _reply(clips: list[int], rationale: str = "covers both angles") -> str:
    return "here you go\n" + json.dumps({"clips": clips, "rationale": rationale})


def _config() -> AgentConfig:
    """The real env-resolved config: the scout only passes it on to the runner, which every
    test replaces — so no provider is ever contacted."""
    return resolve_from_env()


def test_adopts_a_valid_selection_in_the_agents_order() -> None:
    # NOTE: the brief's original fixture used runner=lambda _task: _reply([2, 0]) with an
    # expected ["gamma", "alpha"]. Candidates 0 and 2 are BOTH asset "a" (only candidate 1 is
    # asset "b"), so [2, 0] is a single-source selection while asset "b" is available — the
    # same index pair {0, 2} is exercised by test_single_source_selection_is_rejected_when_
    # two_are_available as its REJECTED first attempt. The two expectations are mutually
    # exclusive under the "cover >= 2 assets when >1 is available" rule (design spec
    # 2026-07-31-auto-overview-design.md §4: "mindestens zwei verschiedene Assets, sofern die
    # Kandidaten mehr als eines abdecken"), which the implementation and three other tests in
    # this file agree on. Fixed here to a genuinely multi-asset selection ([2, 1] -> gamma,
    # beta), keeping the test's intent: a subset, in the agent's own (non-candidate) order.
    out = run_overview_scout(
        _config(),
        topic="mission",
        candidates=_candidates(),
        target_seconds=180,
        fps_by_asset=FPS,
        runner=lambda _task: _reply([2, 1]),
    )
    assert [c.snippet for c in out["clips"]] == ["gamma", "beta"]
    assert out["fallback"] is False
    assert out["rationale"] == "covers both angles"


def test_out_of_range_index_is_retried_once_then_adopted() -> None:
    calls: list[str] = []

    def runner(task: str) -> str:
        calls.append(task)
        return _reply([99]) if len(calls) == 1 else _reply([1, 0])

    out = run_overview_scout(
        _config(), topic="mission", candidates=_candidates(), target_seconds=180,
        fps_by_asset=FPS, runner=runner,
    )
    assert len(calls) == 2
    assert "not a candidate index" in calls[1]
    assert [c.snippet for c in out["clips"]] == ["beta", "alpha"]
    assert out["fallback"] is False


def test_duplicate_indices_are_rejected() -> None:
    replies = [_reply([1, 1]), _reply([1, 0])]

    def runner(_task: str) -> str:
        return replies.pop(0)

    out = run_overview_scout(
        _config(), topic="mission", candidates=_candidates(), target_seconds=180,
        fps_by_asset=FPS, runner=runner,
    )
    assert [c.snippet for c in out["clips"]] == ["beta", "alpha"]


def test_single_source_selection_is_rejected_when_two_are_available() -> None:
    replies = [_reply([0, 2]), _reply([0, 1])]

    def runner(_task: str) -> str:
        return replies.pop(0)

    out = run_overview_scout(
        _config(), topic="mission", candidates=_candidates(), target_seconds=180,
        fps_by_asset=FPS, runner=runner,
    )
    assert {c.asset_id for c in out["clips"]} == {"a", "b"}


def test_single_source_selection_is_fine_when_only_one_asset_has_material() -> None:
    only_a = [Candidate("a", "A", 1, 0, 300, "alpha"), Candidate("a", "A", 3, 600, 900, "gamma")]
    out = run_overview_scout(
        _config(), topic="mission", candidates=only_a, target_seconds=180,
        fps_by_asset=FPS, runner=lambda _task: _reply([0, 1]),
    )
    assert out["fallback"] is False
    assert len(out["clips"]) == 2


def test_a_raising_runner_falls_back_without_an_exception() -> None:
    def runner(_task: str) -> str:
        raise RuntimeError("model exploded")

    out = run_overview_scout(
        _config(), topic="mission", candidates=_candidates(), target_seconds=180,
        fps_by_asset=FPS, runner=runner,
    )
    assert out["fallback"] is True
    assert out["rationale"] == "automatic fallback: top search scores"
    assert [c.snippet for c in out["clips"]] == ["alpha", "beta", "gamma"]


def test_garbage_replies_twice_fall_back() -> None:
    out = run_overview_scout(
        _config(), topic="mission", candidates=_candidates(), target_seconds=180,
        fps_by_asset=FPS, runner=lambda _task: "I would rather not",
    )
    assert out["fallback"] is True


def test_selection_is_trimmed_to_the_target() -> None:
    """3 x 10s against a 10s target (+20% tolerance) keeps only the first clip."""
    out = run_overview_scout(
        _config(), topic="mission", candidates=_candidates(), target_seconds=10,
        fps_by_asset=FPS, runner=lambda _task: _reply([1, 0, 2]),
    )
    assert [c.snippet for c in out["clips"]] == ["beta"]


def test_the_task_text_lists_numbered_candidates() -> None:
    seen: list[str] = []

    def runner(task: str) -> str:
        seen.append(task)
        return _reply([0, 1])

    run_overview_scout(
        _config(), topic="mission", candidates=_candidates(), target_seconds=180,
        fps_by_asset=FPS, runner=runner,
    )
    assert "[0]" in seen[0] and "[2]" in seen[0]
    assert "alpha" in seen[0] and "gamma" in seen[0]
    assert "mission" in seen[0]
