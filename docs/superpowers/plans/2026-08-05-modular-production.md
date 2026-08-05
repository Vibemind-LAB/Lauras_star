# Modulare Produktion (deterministische Post-Gate-Kette) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Nach der Gate-B-Freigabe läuft Voice→Cutlist→Contact-Sheet→Render als deterministische Tool-Kette plus eine begrenzte QA-Agent-Stufe — ohne das kreative Agenten-Team, das freigegebene Scripts umschreibt.

**Architecture:** Neues Modul `production_pipeline.py` trägt die Kette (`run_deterministic_tail`) und die QA-Stufe; ein reines Prädikat `deterministic_eligible` in `production_orchestrator.py` wählt sie vor dem Team-Aufbau; `_handle_approve_script` wird von einem Follow-up-Text zu einem puren Resume (`run_production_resume`, Job ohne `message`).

**Tech Stack:** Python 3.11, pytest, bestehende `ProductionDeps`/`ToolSpec`-Closures, AutoGen nur noch für die QA-Stufe.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-05-modular-production-design.md`. Entscheidungen dort sind bindend.
- **Kein Team-Fallback** bei Kettenfehlern; genau **1 Auto-Retry pro Schritt**, dann ehrliches Scheitern mit Schrittnamen.
- Der Tail besitzt **keinen Schreibpfad auf `script`/`storyline`** — er erhält ausschließlich die vier Ketten-Closures + QA; Exakt-Tupel-Tests pinnen das.
- Scope v1: Die Kette läuft NUR, wenn `script_gate` aktiv + Freigabe content-aktuell + `resume_point` ∈ {`voice`,`cutlist`,`contact_sheet`,`render_report`,`qa_report`} + `message is None`. Alles andere bleibt der Team-Pfad.
- `_SCRIPT_APPROVED_FOLLOW_UP_TEXT` entfällt ersatzlos.
- Event-Formen der Karte bleiben: `{"type": "tool_call", ...}` / `{"type": "tool_result", ...}` wie im Team-Pfad; keine neuen Event-Typen.
- Chat-Texte Deutsch, Identifier/Kommentare/Commits Englisch. Typing strikt (bare `uv run mypy` deckt src+tests), `ruff check src tests`, kein `print`.
- Gates je Task VORDERGRUND mit echter Summary-Zeile; pytest NIE mit zusätzlichem `-q` (addopts hat schon `-q`; doppelt = `-qq` unterdrückt die Summary).
- Explizites `git add <paths>` (nie `-A`). Commits enden mit `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task MP1: `production_pipeline.run_deterministic_tail` (Kette ohne QA)

**Files:**
- Create: `services/local-api/src/laura/short_creator/production_pipeline.py`
- Test: `services/local-api/tests/test_production_pipeline.py` (neu)

**Interfaces:**
- Consumes: `production_tools.build_production_tool_specs(db, board, *, asset_id, deps)` → `list[ToolSpec]` (`ToolSpec` = `{name, description, func}`); `Board.resume_point(expected_scenes) -> str`; `production_orchestrator._expected_scene_numbers(db, asset_id)` (bereits vorhanden, wird importiert).
- Produces: `TailOutcome` (dataclass: `ok: bool`, `failed_step: str | None`, `reason: str | None`, `summary: str`) und `run_deterministic_tail(db, board, *, asset_id, deps, event_sink) -> TailOutcome`. MP2 hängt QA an, MP3 ruft beides aus `run_production`.

- [ ] **Step 1: Failing Tests schreiben**

```python
"""Deterministic post-gate tail (spec 2026-08-05-modular-production-design.md).

The tail runs voice -> cutlist -> contact_sheet -> render as plain tool calls with
skip semantics (resume_point decides), one retry per step, and NO write path to
script/storyline. Tests drive it with a fake spec list — no autogen, no ffmpeg.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import pytest

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
    return run_deterministic_tail.__wrapped__(  # type: ignore[attr-defined]
        board, specs, expected_scenes=[1], event_sink=rec.sink
    ) if hasattr(run_deterministic_tail, "__wrapped__") else run_deterministic_tail(
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
```

Hinweis an den Implementer: `_run` oben ruft `run_deterministic_tail` mit der **inneren** Signatur (`board, specs, expected_scenes, event_sink`) auf. Implementiere das Modul GENAU so, dass die öffentliche Funktion diese testbare Kernsignatur hat (siehe Step 3) — dann entfällt der `__wrapped__`-Zweig und `_run` reduziert sich auf den direkten Aufruf. Passe `_run` beim Schreiben entsprechend an (kein `hasattr`-Zweig nötig); er steht hier nur, damit die Testdatei vor der Implementierung syntaktisch vollständig ist.

- [ ] **Step 2: Tests laufen lassen — sie müssen scheitern**

Run: `uv run pytest tests/test_production_pipeline.py` (aus `services/local-api`)
Expected: FAIL / ImportError „No module named 'laura.short_creator.production_pipeline'"

- [ ] **Step 3: Modul implementieren**

```python
"""Deterministic post-gate production tail (spec 2026-08-05-modular-production-design.md).

After the user approves the script (Gate B), nothing creative remains: voice, cutlist,
contact sheet and render are plain tool calls. This module runs them WITHOUT the agent
team, so an approved script can never be rewritten by a resumed run. Skip semantics come
from ``Board.resume_point``: the chain starts at the first missing artifact and each
successful step must advance it (a step that reports ok but does not advance ends the run
as a failure — never a spin). Each step gets exactly one retry (spec decision 3), then the
run fails honestly with the step name. QA is a separate, bounded agent stage (MP2) — this
module never holds a write-capable creative tool.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from .toolset import ToolSpec

logger = logging.getLogger(__name__)

# resume_point -> tool that produces that artifact. Order IS the chain order; the exact
# tuple is pinned by test_tail_tool_menu_is_pinned_exactly (structural no-rewrite
# guarantee: nothing outside these four names is ever callable from the tail).
_STEP_BY_RESUME_POINT: dict[str, str] = {
    "voice": "synthesize_script_voice",
    "cutlist": "build_cutlist",
    "contact_sheet": "save_contact_sheet",
    "render_report": "render_production",
}


@dataclass
class TailOutcome:
    """How the deterministic chain ended (QA not included — MP2 reports separately)."""

    ok: bool
    failed_step: str | None
    reason: str | None
    summary: str


def _emit(sink: Callable[[dict[str, Any]], None] | None, event: dict[str, Any]) -> None:
    if sink is None:
        return
    try:
        sink(event)
    except Exception:  # noqa: BLE001 — observability must never break the run
        logger.warning("deterministic tail event sink failed", exc_info=True)


def run_deterministic_tail(
    board: Any,
    specs: list[ToolSpec],
    *,
    expected_scenes: list[int],
    event_sink: Callable[[dict[str, Any]], None] | None = None,
) -> TailOutcome:
    """Run the post-approval chain from the board's current resume point to the render.

    ``specs`` is the FULL production ToolSpec list (the caller builds it once via
    ``build_production_tool_specs``); only the four chain tools are ever looked up. The
    board/spec split keeps this function pure enough to test with fakes.
    """
    funcs = {s.name: s.func for s in specs if s.name in _STEP_BY_RESUME_POINT.values()}
    done: list[str] = []

    while True:
        point = board.resume_point(expected_scenes)
        tool_name = _STEP_BY_RESUME_POINT.get(point)
        if tool_name is None:
            # Past the chain (qa_report/done) or before it (creative work missing —
            # the eligibility predicate should have prevented that; stop either way).
            break
        func = funcs.get(tool_name)
        if func is None:
            return TailOutcome(False, tool_name, "tool not available", _summary(done))

        result = _call_with_retry(func, tool_name, event_sink)
        if not result.get("ok", False):
            reason = str(result.get("reason", "tool failed"))[:300]
            return TailOutcome(False, tool_name, reason, _summary(done))

        if board.resume_point(expected_scenes) == point:
            return TailOutcome(
                False, tool_name, "no progress after ok result", _summary(done)
            )
        done.append(tool_name)

    return TailOutcome(True, None, None, _summary(done))


def _call_with_retry(
    func: Callable[..., dict[str, Any]],
    tool_name: str,
    event_sink: Callable[[dict[str, Any]], None] | None,
) -> dict[str, Any]:
    """One call plus exactly one retry (spec decision 3). Exceptions count as failures
    (the tools' own contract is to return ``{"ok": False}`` instead of raising, but the
    tail must survive a raising seam too)."""
    result: dict[str, Any] = {"ok": False, "reason": "not called"}
    for attempt in (1, 2):
        _emit(event_sink, {
            "type": "tool_call", "agent": "pipeline", "tool": tool_name,
            "args": {"attempt": attempt},
        })
        try:
            result = func()
        except Exception as exc:  # noqa: BLE001 — a raising tool is a failed attempt
            result = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"[:300]}
        _emit(event_sink, {
            "type": "tool_result", "tool": tool_name,
            "ok": bool(result.get("ok", False)),
            "summary": str(result)[:300],
        })
        if result.get("ok", False):
            return result
    return result


def _summary(done: list[str]) -> str:
    return "deterministic tail: " + (", ".join(done) if done else "nothing to do")
```

- [ ] **Step 4: Tests grün laufen lassen**

Run: `uv run pytest tests/test_production_pipeline.py`
Expected: PASS (7 Tests). Danach `uv run mypy` (bare) und `uv run ruff check src tests` — beide sauber.

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/short_creator/production_pipeline.py services/local-api/tests/test_production_pipeline.py
git commit -m "feat(short-creator): deterministic post-gate tail (voice->cutlist->sheet->render)"
```

---

### Task MP2: Begrenzte QA-Stufe (Ein-Agent-Team, Lese-/QA-Whitelist)

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_agents.py` (`build_production_team` bekommt `agent_names`-Filter)
- Modify: `services/local-api/src/laura/short_creator/production_orchestrator.py` (`_make_default_execute` bekommt `agent_names`- und `task_override`-Durchreiche NICHT — stattdessen exportiert MP2 eine eigene kleine Fabrik, s.u.)
- Modify: `services/local-api/src/laura/short_creator/production_pipeline.py` (QA-Stufe + Gesamteinstieg `run_tail_with_qa`)
- Test: `services/local-api/tests/test_production_agents.py` (Filter), `services/local-api/tests/test_production_pipeline.py` (QA-Aufruf-Vertrag)

**Interfaces:**
- Consumes: `build_production_team(db, board, config, *, asset_id, stage, deps)` (MP2 erweitert um `agent_names: tuple[str, ...] | None = None`); `orchestrator._safe_execute(execute, db, config, stage, kind, task) -> StageOutcome`; `production_orchestrator._make_default_execute(board, asset_id, deps, event_sink, *, require_tool_call)` als Vorlage für die QA-Execute-Fabrik.
- Produces: `run_tail_with_qa(db, board, config, *, asset_id, deps, event_sink, expected_scenes, qa_execute=None) -> tuple[TailOutcome, StageOutcome | None]` — MP3 ruft genau diese Funktion. `qa_execute` ist der Test-Seam (injizierte `ExecuteFn`), Default baut das echte Ein-Agent-Team.

- [ ] **Step 1: Failing Tests schreiben**

In `tests/test_production_agents.py` ergänzen:

```python
def test_agent_names_filter_builds_a_qa_only_team(monkeypatch, tmp_path) -> None:
    """agent_names=('qa_reviewer',) yields exactly one participant whose tools are the
    QA whitelist — the structural guarantee that the post-gate QA stage cannot write."""
    db, board, asset_id = _seeded_board(tmp_path)  # bestehende Fixture-Helper der Datei
    team = build_production_team(
        db, board, _config(), asset_id=asset_id, agent_names=("qa_reviewer",)
    )
    [agent] = team._participants  # noqa: SLF001 — same access the existing tests use
    assert agent.name == "qa_reviewer"
    assert tuple(sorted(t.name for t in agent._tools)) == (  # noqa: SLF001
        "board_status", "get_script", "get_storyline", "review_export", "save_qa_report",
    )


def test_agent_names_filter_unknown_name_raises() -> None:
    with pytest.raises(ValueError, match="unknown agent"):
        _ = [s for s in production_agent_specs("German") if False]  # placeholder-free:
    # Der eigentliche Assert liegt im Team-Builder:
    # build_production_team(..., agent_names=("nope",)) muss ValueError("unknown agent ...") heben.
```

(Der Implementer ersetzt den zweiten Test durch den direkten `build_production_team`-Aufruf mit der bestehenden Fixture — entscheidend sind die zwei Verträge: Exakt-Tupel-Whitelist und ValueError bei unbekanntem Namen.)

In `tests/test_production_pipeline.py` ergänzen:

```python
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
```

- [ ] **Step 2: Tests laufen lassen — FAIL** (`agent_names` unbekannt, `run_tail_with_qa` fehlt)

Run: `uv run pytest tests/test_production_pipeline.py tests/test_production_agents.py`

- [ ] **Step 3: Implementieren**

`production_agents.build_production_team`: neuer Keyword-Parameter `agent_names: tuple[str, ...] | None = None`. Nach `production_agent_specs(...)`:

```python
    specs_all = production_agent_specs(board.meta().language)
    if agent_names is not None:
        known = {s.name for s in specs_all}
        unknown = [n for n in agent_names if n not in known]
        if unknown:
            raise ValueError(f"unknown agent name(s): {unknown}")
        specs_all = [s for s in specs_all if s.name in agent_names]
```

(die bestehende List-Comprehension iteriert dann über `specs_all`).

`production_pipeline.py` ergänzen:

```python
_QA_TASK = (
    "QA only. The creative chain is complete and the script is user-approved: judge the "
    "RENDERED export on the board (board_status -> get_storyline/get_script -> "
    "review_export) and save your verdict via save_qa_report. Report findings; never "
    "request script or storyline rewrites — the deterministic pipeline owns the chain."
)


def run_tail_with_qa(
    db: Any,
    board: Any,
    config: Any,
    *,
    asset_id: str,
    deps: Any,
    event_sink: Callable[[dict[str, Any]], None] | None,
    expected_scenes: list[int],
    specs: list[ToolSpec] | None = None,
    qa_execute: Any = None,
) -> tuple[TailOutcome, Any]:
    """Chain first, bounded QA second. QA runs only after a fully successful chain and
    gets exactly one retry via the same _call convention (spec decision 3 applies to the
    stage as a whole: one _safe_execute call is already exception-safe, a hard_fail QA is
    reported, not retried into a loop)."""
    if specs is None:
        from .production_tools import build_production_tool_specs

        specs = build_production_tool_specs(db, board, asset_id=asset_id, deps=deps)

    tail = run_deterministic_tail(
        board, specs, expected_scenes=expected_scenes, event_sink=event_sink
    )
    if not tail.ok:
        return tail, None

    from .orchestrator import _safe_execute

    execute = qa_execute if qa_execute is not None else _make_qa_execute(
        board, asset_id, deps, event_sink
    )
    qa_outcome = _safe_execute(execute, db, config, "A", "magentic", _QA_TASK)
    return tail, qa_outcome


def _make_qa_execute(
    board: Any,
    asset_id: str,
    deps: Any,
    event_sink: Callable[[dict[str, Any]], None] | None,
) -> Any:
    """The real QA ExecuteFn: a one-agent team (qa_reviewer only). Mirrors
    production_orchestrator._make_default_execute, narrowed via agent_names."""
    from .production_orchestrator import _make_default_execute

    return _make_default_execute(
        board, asset_id, deps, event_sink, agent_names=("qa_reviewer",)
    )
```

`production_orchestrator._make_default_execute`: neuer Keyword-Parameter `agent_names: tuple[str, ...] | None = None`, unverändert an `build_production_team(..., agent_names=agent_names)` durchgereicht (eine Zeile in der inneren Closure).

- [ ] **Step 4: Tests grün + mypy + ruff**

Run: `uv run pytest tests/test_production_pipeline.py tests/test_production_agents.py`, dann bare `uv run mypy`, `uv run ruff check src tests`.

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/short_creator/production_pipeline.py services/local-api/src/laura/short_creator/production_agents.py services/local-api/src/laura/short_creator/production_orchestrator.py services/local-api/tests/test_production_pipeline.py services/local-api/tests/test_production_agents.py
git commit -m "feat(short-creator): bounded qa stage for the deterministic tail"
```

---

### Task MP3: `deterministic_eligible` + Zweig in `run_production`

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_orchestrator.py` (Prädikat + Zweig zwischen Short-Circuit und `build_production_task`)
- Test: `services/local-api/tests/test_production_orchestrator.py` (ergänzen)

**Interfaces:**
- Consumes: `production_pipeline.run_tail_with_qa` (MP2, exakte Signatur oben); `board.meta().script_gate/script_approved_utc/script_approved_script_hash`; `board_models.content_hash`; `Board.load("script")`; `board.resume_point`.
- Produces: `deterministic_eligible(board, message, expected_scenes) -> bool` (rein, von MP4-Tests mitbenutzt). `run_production` liefert im deterministischen Fall dieselbe `_completed_result`-Form (Karten/Status unverändert).

- [ ] **Step 1: Failing Tests schreiben**

In `tests/test_production_orchestrator.py` ergänzen (bestehende Board-Fixtures der Datei nutzen — es gibt dort Helper, die ein Board mit storyline+script seeden; der Implementer verwendet exakt diese statt neue zu erfinden):

```python
def _approve_current(board) -> None:
    from laura.short_creator.board_models import content_hash
    script = board.load("script")
    board.set_script_approved("2026-08-05T12:00:00Z", content_hash(script))


class TestDeterministicEligible:
    def test_true_only_for_gated_current_post_script_no_message(self, seeded_board) -> None:
        board = seeded_board(script_gate=True)   # storyline+script vorhanden, resume=voice
        _approve_current(board)
        assert deterministic_eligible(board, None, board_expected_scenes) is True

    def test_false_without_gate(self, seeded_board) -> None:
        board = seeded_board(script_gate=False)
        _approve_current(board)
        assert deterministic_eligible(board, None, board_expected_scenes) is False

    def test_false_when_approval_stale(self, seeded_board) -> None:
        board = seeded_board(script_gate=True)
        board.set_script_approved("2026-08-05T12:00:00Z", "not-the-current-hash")
        assert deterministic_eligible(board, None, board_expected_scenes) is False

    def test_false_when_never_approved(self, seeded_board) -> None:
        board = seeded_board(script_gate=True)
        assert deterministic_eligible(board, None, board_expected_scenes) is False

    def test_false_with_follow_up_message(self, seeded_board) -> None:
        board = seeded_board(script_gate=True)
        _approve_current(board)
        assert deterministic_eligible(board, "mach den Hook punchiger",
                                      board_expected_scenes) is False

    def test_false_before_script_exists(self, empty_board) -> None:
        board = empty_board(script_gate=True)     # resume=storyline
        assert deterministic_eligible(board, None, board_expected_scenes) is False


def test_run_production_uses_tail_not_team_when_eligible(monkeypatch, seeded_env) -> None:
    """seeded_env: db+asset+session+board mit storyline/script, gate an, approved current.
    Der Team-Execute darf NICHT laufen; run_tail_with_qa wird genau einmal gerufen."""
    called = {"tail": 0, "team": 0}

    def fake_tail(db, board, config, **kwargs):
        called["tail"] += 1
        from laura.short_creator.production_pipeline import TailOutcome
        from laura.short_creator.orchestrator import StageOutcome
        return (TailOutcome(True, None, None, "deterministic tail: all"),
                StageOutcome(status="ok", weak=False, summary="ship",
                             team="magentic", stage="A"))

    monkeypatch.setattr(
        "laura.short_creator.production_orchestrator.run_tail_with_qa", fake_tail
    )

    def team_execute(*a, **k):
        called["team"] += 1
        raise AssertionError("team must not run on the deterministic path")

    result = run_production(
        seeded_env.db, _config(), asset_id=seeded_env.asset_id,
        session_id=seeded_env.session_id, task="t", execute=team_execute,
    )
    assert called == {"tail": 1, "team": 0}
    assert result["ok"] is True and result["summary"].startswith("deterministic tail")


def test_run_production_tail_failure_sets_failed_status(monkeypatch, seeded_env) -> None:
    def fake_tail(db, board, config, **kwargs):
        from laura.short_creator.production_pipeline import TailOutcome
        return TailOutcome(False, "render_production", "boom", "deterministic tail: voice"), None

    monkeypatch.setattr(
        "laura.short_creator.production_orchestrator.run_tail_with_qa", fake_tail
    )
    result = run_production(
        seeded_env.db, _config(), asset_id=seeded_env.asset_id,
        session_id=seeded_env.session_id, task="t",
        execute=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no team")),
    )
    assert result["ok"] is False
    assert "render_production" in result["summary"]
    assert seeded_env.board().meta_status() == "failed"  # per board.status()/meta
```

(Fixture-Namen `seeded_board`/`empty_board`/`seeded_env` an die real vorhandenen Helper der Testdatei anpassen — die Datei hat bereits Builder für Boards mit/ohne Artefakte; NICHTS doppelt bauen.)

- [ ] **Step 2: FAIL laufen lassen** — `deterministic_eligible` existiert nicht.

- [ ] **Step 3: Implementieren**

In `production_orchestrator.py` (Import oben: `from .production_pipeline import run_tail_with_qa`):

```python
_TAIL_RESUME_POINTS = frozenset({
    "voice", "cutlist", "contact_sheet", "render_report", "qa_report",
})


def deterministic_eligible(
    board: Board, message: str | None, expected_scenes: list[int]
) -> bool:
    """Spec 2026-08-05 (modular production): the post-approval resume of a gated session
    runs the deterministic tail instead of the agent team. True IFF the gate is on, the
    approval is content-current (same compare pair as the voice gate), the creative work
    is done (resume point past 'script'), and this is not a text follow-up."""
    if message is not None:
        return False
    meta = board.meta()
    if not meta.script_gate or meta.script_approved_utc is None:
        return False
    script = board.load("script")
    if not isinstance(script, Script):
        return False
    if meta.script_approved_script_hash != content_hash(script):
        return False
    return board.resume_point(expected_scenes) in _TAIL_RESUME_POINTS
```

Zweig in `run_production` — direkt NACH dem bestehenden „board already coherent"-Short-Circuit und VOR `build_production_task` (Zeile ~600):

```python
    if deterministic_eligible(board, message, expected_scenes):
        run_deps = _deps_for_run(deps, board, message)
        tail, qa_outcome = run_tail_with_qa(
            db, board, config, asset_id=asset_id, deps=run_deps,
            event_sink=event_sink, expected_scenes=expected_scenes,
        )
        resume_point = board.resume_point(expected_scenes)
        if not tail.ok:
            board.set_status("failed")
            summary = f"deterministic tail failed at {tail.failed_step}: {tail.reason}"
        else:
            if resume_point == "done":
                board.set_status("complete")
            summary = tail.summary + (
                f"; qa: {qa_outcome.summary}" if qa_outcome is not None else ""
            )
        return _completed_result(
            board,
            session_id=session_id,
            restored=restored,
            status="ok" if tail.ok else "hard_fail",
            stage="A",
            team="magentic",  # cosmetic result field; cards read summary/status.
            weak=_qa_weak(board),
            escalated=False,
            summary=summary,
            export_id=_export_id_of(board),
            resume_point=resume_point,
        )
```

(`Script`/`content_hash` sind in der Datei zu importieren, falls noch nicht vorhanden.)

- [ ] **Step 4: Tests grün + mypy + ruff** — `uv run pytest tests/test_production_orchestrator.py tests/test_production_pipeline.py`, bare `uv run mypy`, `uv run ruff check src tests`.

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/short_creator/production_orchestrator.py services/local-api/tests/test_production_orchestrator.py
git commit -m "feat(short-creator): eligible post-approval runs take the deterministic tail"
```

---

### Task MP4: `approve_script` als purer Resume

**Files:**
- Modify: `services/local-api/src/laura/api/short_creator.py` (extract `_enqueue_production_run(db, session_id, message)` + neue `run_production_resume(db, session_id)`)
- Modify: `services/local-api/src/laura/chat/executor.py` (`_handle_approve_script`: Resume statt Follow-up-Text; Doppel-Freigabe auf unfertigem Board stößt Resume an; `_SCRIPT_APPROVED_FOLLOW_UP_TEXT` löschen)
- Test: `services/local-api/tests/test_script_gate.py`, `services/local-api/tests/test_chat_executor.py` (anpassen/ergänzen)

**Interfaces:**
- Consumes: MP3s Verhalten (Resume ohne message + gated/approved-current ⇒ Tail). `run_production_follow_up(db, session_id, text)` bleibt für echte Text-Follow-ups unverändert bestehen.
- Produces: `run_production_resume(db, session_id) -> dict[str, Any]` — identisches Rückgabeformat wie `run_production_follow_up` (`{session_id, job_id, warnings}`), Payload OHNE `message`-Key.

- [ ] **Step 1: Failing Tests schreiben**

`tests/test_script_gate.py` — bestehende approve-Tests anpassen + neu:

```python
def test_approve_enqueues_a_pure_resume_without_message(seeded_gate_session) -> None:
    """The approve job payload must not carry a message key — a message run is a team
    run (MP3's predicate), the whole point of the modular arc."""
    result = _approve_via_chat(seeded_gate_session)          # bestehender Helper-Stil
    job = _job_row(seeded_gate_session.db, result_job_id(result))
    payload = json.loads(job["payload_json"])
    assert "message" not in payload or payload["message"] in (None, "")


def test_double_approve_on_unfinished_board_resumes_again(seeded_gate_session) -> None:
    """Already-approved + hash current + board incomplete -> a SECOND 'Script freigeben'
    enqueues another resume (recovery path after a failed tail) instead of only replying."""
    _approve_via_chat(seeded_gate_session)
    jobs_before = _production_job_count(seeded_gate_session.db)
    messages = _approve_via_chat(seeded_gate_session)          # gleiche Konversation
    assert _production_job_count(seeded_gate_session.db) == jobs_before + 1
    assert any(m["kind"] == "action" for m in messages)


def test_double_approve_on_complete_board_stays_a_noop(seeded_gate_session) -> None:
    _approve_via_chat(seeded_gate_session)
    _complete_board(seeded_gate_session)                       # alle Artefakte seeden
    jobs_before = _production_job_count(seeded_gate_session.db)
    messages = _approve_via_chat(seeded_gate_session)
    assert _production_job_count(seeded_gate_session.db) == jobs_before
    assert "schon freigegeben" in _last_text(messages)
```

(Helper `_approve_via_chat`/`_job_row`/`_production_job_count`/`_complete_board`/`_last_text`: die Datei hat für alle bereits Muster — exakt deren Stil folgen, keine Parallel-Helfer bauen.)

- [ ] **Step 2: FAIL laufen lassen** — Payload trägt noch den Follow-up-Text; Doppel-Freigabe antwortet nur.

- [ ] **Step 3: Implementieren**

`api/short_creator.py`:

```python
def _enqueue_production_run(
    db: Database, session_id: str, message: str | None
) -> dict[str, Any]:
    """Shared enqueue for follow-up (message) and pure resume (None) runs."""
    session = repos.get_production_session(db, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    _require_autoshort()
    _require_usable_agent_config()
    asset_id = str(session["asset_id"])
    board = _open_board_or_404(db, asset_id, session_id)
    meta = board.meta()
    payload: dict[str, Any] = {
        "asset_id": asset_id,
        "session_id": session_id,
        "task": meta.task,
        "target_seconds": int(meta.target_seconds),
    }
    if message is not None:
        payload["message"] = message
    job_id = enqueue(
        db, queue=queue_for("production.run"), kind="production.run",
        payload=payload, max_attempts=1,
    )
    repos.set_production_session_job(db, session_id, job_id)
    from ..short_creator.providers import config_warnings, resolve_from_env

    return {
        "session_id": session_id,
        "job_id": job_id,
        "warnings": config_warnings(resolve_from_env()),
    }


def run_production_follow_up(db: Database, session_id: str, text: str) -> dict[str, Any]:
    """(Docstring wie bisher.)"""
    return _enqueue_production_run(db, session_id, text)


def run_production_resume(db: Database, session_id: str) -> dict[str, Any]:
    """Pure resume: same job, NO message — an eligible gated board takes the
    deterministic tail (production_orchestrator.deterministic_eligible)."""
    return _enqueue_production_run(db, session_id, None)
```

`chat/executor.py` in `_handle_approve_script`:
- Import `run_production_resume` statt (zusätzlich zu) `run_production_follow_up`; Konstante `_SCRIPT_APPROVED_FOLLOW_UP_TEXT` löschen.
- Den Doppel-Freigabe-Zweig ersetzen:

```python
    already_current = (
        meta.script_approved_utc is not None
        and meta.script_approved_script_hash == current_hash
    )
    if already_current and board.resume_point(
        _expected_scenes_for(db, asset_id)
    ) == "done":
        return [_append_text(db, conversation_id, _SCRIPT_ALREADY_APPROVED_TEXT, now_utc)]
    if not already_current:
        board.set_script_approved(now_utc, current_hash)

    try:
        result = run_production_resume(db, session_id)
    except HTTPException as exc:
        if not already_current:
            board.clear_script_approval()
        return [_append_text(db, conversation_id, _detail_reason(exc.detail), now_utc)]
    except Exception:
        if not already_current:
            board.clear_script_approval()
        raise
```

(`_expected_scenes_for` aus `api/short_creator.py` importieren — lokaler Import im Handler, wie die anderen board-nahen Imports dort. Der Rollback darf eine BEREITS BESTEHENDE aktuelle Freigabe nicht löschen — daher die `already_current`-Guards.)

- [ ] **Step 4: Tests grün** — `uv run pytest tests/test_script_gate.py tests/test_chat_executor.py tests/test_production_orchestrator.py`, bare `uv run mypy`, `uv run ruff check src tests`.

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/api/short_creator.py services/local-api/src/laura/chat/executor.py services/local-api/tests/test_script_gate.py services/local-api/tests/test_chat_executor.py
git commit -m "feat(chat): approve_script resumes deterministically (no follow-up text)"
```

---

### Task MP5: Volle Gates + Doku + manuelle Prüfliste

**Files:**
- Modify: `docs/00-overview.md` (2-3 Sätze im Transkript-Gates-Absatz: nach der Freigabe deterministische Kette), `tasks/todo.md` (Haken im bestehenden Stil)

- [ ] **Step 1:** Backend voll: `uv run pytest -p no:cacheprovider` (VORDERGRUND, echte Summary-Zeile zitieren), bare `uv run mypy`, `uv run ruff check src tests`.
- [ ] **Step 2:** Desktop: `pnpm test -- --run` + `pnpm typecheck` aus `apps/desktop` (es gibt KEINE Frontend-Änderung in diesem Arc — der Lauf beweist genau das).
- [ ] **Step 3:** Doku: In `docs/00-overview.md` den Gate-B-Satz ergänzen: nach „Script freigeben" läuft die Produktion als deterministische Werkzeugkette (Voice→Cutlist→Kontaktbogen→Render) plus begrenzter QA-Bewertung — das Team fasst freigegebene Scripts nicht mehr an. `tasks/todo.md`: neuen Block „Modulare Produktion (deterministischer Post-Gate-Pfad)" im Stil der Nachbarn, `[x]`.
- [ ] **Step 4:** Manuelle Prüfliste in den Task-Report (jede Zeile „manuell zu prüfen"): (a) Chat-Short bis Gate → „Script freigeben" → Karte erzählt `pipeline`-Tool-Events → „✓ Short fertig" → „▶ ansehen" spielt; (b) KEINE zweite Freigabe nötig (kein Script-Rewrite im Ledger: `runs/*.ndjson` enthält nach der Freigabe keinen `save_script_chapter`); (c) Text-Follow-up („mach den Hook punchiger") läuft weiterhin über das Team und bewaffnet das Gate neu; App-Neustart-Hinweis (detachtes Backend, [[livetest-app-env-traps]]).
- [ ] **Step 5: Commit**

```bash
git add docs/00-overview.md tasks/todo.md
git commit -m "docs: modular production overview + todo tick"
```

---

## Self-Review (Plan gegen Spec)

- **Spec-Abdeckung:** Modul+Kette+Retry+Skip (MP1) ✓; begrenzte QA (MP2) ✓; Prädikat+Zweig+`_completed_result` (MP3) ✓; approve=Resume+Doppel-Freigabe-Resume+Konstante weg (MP4) ✓; Doku/Gates (MP5) ✓; „Bewusst NICHT in v1" braucht keine Tasks ✓.
- **Platzhalter:** Testcode in MP3/MP4 referenziert Fixture-Helper per Rolle statt exaktem Namen (die Dateien HABEN solche Helper; Implementer folgt deren Stil) — bewusste Delegation, keine leeren Schritte.
- **Typkonsistenz:** `run_tail_with_qa(db, board, config, *, asset_id, deps, event_sink, expected_scenes, specs=None, qa_execute=None)` identisch in MP2 (Producer) und MP3 (Consumer); `TailOutcome`-Felder einheitlich; `run_production_resume` Rückgabeform = `run_production_follow_up`.
