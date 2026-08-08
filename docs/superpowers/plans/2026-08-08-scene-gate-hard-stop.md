# Gate-S Orchestrator Hard-Stop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the active MagenticOne production run immediately after a new unconfirmed scene-selection proposal, so no wait/delegate loop or false confirmation can occur before the next external user turn.

**Architecture:** `build_production_team` snapshots the board's current scene-selection version and passes a board-backed `FunctionalTermination` to `MagenticOneGroupChat`. The production orchestrator continues to treat the Board as the sole state authority, reports a parked gate as `awaiting_user_input`, and treats genuine turn-budget exhaustion as a hard failure. Confirmation remains exclusively server-side through the existing `select_scenes` → `confirm_scene_selection` path.

**Tech Stack:** Python 3.11+, AutoGen AgentChat (`MagenticOneGroupChat`, `FunctionalTermination`), Pydantic v2 board artifacts, pytest, mypy strict, Ruff.

## Global Constraints

- The Board remains the only workflow state authority; LLM text never creates a transition.
- `story_architect` must not receive a scene-confirmation tool.
- A newly saved unconfirmed proposal terminates the current team run before another agent or orchestrator turn.
- A plain resume with an already-open proposal continues to park before team construction.
- A proposal-revision follow-up may run the team once; a new proposal version terminates that run again.
- Gate B, deterministic tail, voice, cutlist, rendering, and desktop payloads remain unchanged.
- AutoGen stays an optional `autoshort` extra; importing Laura without that extra must still work.
- Frame ranges remain integer and end-exclusive; this fix must not touch editorial time state.
- Every task follows RED → GREEN → focused regression → Conventional Commit.
- Preserve all unrelated untracked files in the primary worktree.

---

## File Map

- `services/local-api/src/laura/short_creator/production_agents.py`
  - Owns the pure board predicate and native AutoGen termination wiring.
- `services/local-api/src/laura/short_creator/production_orchestrator.py`
  - Owns `TaskResult` classification, Stage A/B behavior, and public production result status.
- `services/local-api/tests/test_production_agents.py`
  - Proves the board predicate and `FunctionalTermination` construction without importing real AutoGen.
- `services/local-api/tests/test_production_orchestrator.py`
  - Proves turn exhaustion is a hard failure and normal termination remains successful.
- `services/local-api/tests/test_production_orchestrator_scene_gate.py`
  - Proves pending/confirmed/revision run behavior and the `awaiting_user_input` contract.
- `services/local-api/tests/test_production_event_log.py`
  - Proves the real stream adapter preserves and classifies `TaskResult.stop_reason`.
- `tasks/todo.md`
  - Records the verified Gate-S hard-stop correction and its actual gate evidence.

---

### Task 1: Native board-backed Gate-S termination

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_agents.py:21-35,283-350`
- Modify: `services/local-api/tests/test_production_agents.py:120-145,299-420`

**Interfaces:**
- Consumes: `Board.meta().scene_gate`, `Board.load("scene_selection")`, and `SceneSelection.version` / `confirmed_utc`.
- Produces: `_scene_selection_version(board: Board) -> int | None`.
- Produces: `_new_pending_scene_selection(board: Board, initial_version: int | None) -> bool`.
- Produces: a `FunctionalTermination` passed as `termination_condition=` to every production `MagenticOneGroupChat`; it is inert unless Gate S is active and a new unconfirmed proposal version appears.

- [ ] **Step 1: Extend the hermetic AutoGen fake and write failing wiring assertions**

In `_install_fake_autogen`, add a fake `autogen_agentchat.conditions` module and capture the condition passed to the team:

```python
class FakeFunctionalTermination:
    def __init__(self, func: object) -> None:
        created["termination_predicate"] = func

class FakeMagenticOneGroupChat:
    def __init__(
        self,
        *,
        participants: tuple[object, ...],
        model_client: object,
        termination_condition: object | None = None,
        max_turns: int = 0,
    ) -> None:
        self._participants = list(participants)
        self._model_client = model_client
        self._termination_condition = termination_condition
        self._max_turns = max_turns
        created["team"] = self
```

Register the fake:

```python
ac_conditions = types.ModuleType("autogen_agentchat.conditions")
ac_conditions.FunctionalTermination = FakeFunctionalTermination  # type: ignore[attr-defined]
```

Add to `test_build_team_constructs`:

```python
assert team._termination_condition is not None
```

- [ ] **Step 2: Write failing pure predicate tests**

Allow the local `_board` fixture to set `scene_gate`, import `SceneCandidate` and
`SceneSelection`, then add:

```python
def _selection(*, confirmed: bool = False) -> SceneSelection:
    return SceneSelection(
        candidates=[
            SceneCandidate(
                scene_number=1,
                src_start_frame=0,
                src_end_frame_exclusive=SCENE_FRAMES,
                thumb_frame=SCENE_FRAMES // 2,
                description="dashboard",
                transcript_snippet="hallo welt",
                rationale="hook",
                recommended=True,
            )
        ],
        selected_scene_numbers=[1] if confirmed else [],
        confirmed_utc="2026-08-08T00:00:00+00:00" if confirmed else None,
    )


def test_scene_gate_termination_requires_a_new_unconfirmed_version(tmp_path: Path) -> None:
    _db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id, scene_gate=True)

    assert production_agents._scene_selection_version(board) is None
    assert production_agents._new_pending_scene_selection(board, None) is False

    board.save("scene_selection", _selection())
    assert production_agents._new_pending_scene_selection(board, None) is True
    current = production_agents._scene_selection_version(board)
    assert production_agents._new_pending_scene_selection(board, current) is False


def test_scene_gate_termination_ignores_gate_off_and_confirmed_boards(tmp_path: Path) -> None:
    _db, asset_id = _seed_scene(tmp_path)
    gate_off = _board(tmp_path / "off", asset_id, scene_gate=False)
    gate_off.save("scene_selection", _selection())
    assert production_agents._new_pending_scene_selection(gate_off, None) is False

    confirmed = _board(tmp_path / "confirmed", asset_id, scene_gate=True)
    confirmed.save("scene_selection", _selection(confirmed=True))
    assert production_agents._new_pending_scene_selection(confirmed, None) is False
```

- [ ] **Step 3: Run the new tests and verify RED**

Run:

```powershell
cd services/local-api
uv run pytest tests/test_production_agents.py -k "termination or build_team_constructs" -vv
```

Expected: FAIL because the two helper functions do not exist and the fake team receives no `termination_condition`.

- [ ] **Step 4: Implement the pure predicate**

In `production_agents.py`, import `SceneSelection` beside the existing board types and add near `MAX_TURNS`:

```python
def _scene_selection_version(board: Board) -> int | None:
    selection = board.load("scene_selection")
    return selection.version if isinstance(selection, SceneSelection) else None


def _new_pending_scene_selection(board: Board, initial_version: int | None) -> bool:
    if not board.meta().scene_gate:
        return False
    selection = board.load("scene_selection")
    return (
        isinstance(selection, SceneSelection)
        and selection.confirmed_utc is None
        and selection.version != initial_version
    )
```

- [ ] **Step 5: Wire AutoGen's native `FunctionalTermination` lazily**

Inside `build_production_team`'s existing optional-import block, add:

```python
from autogen_agentchat.conditions import FunctionalTermination
```

Capture the version immediately before team construction and pass the condition:

```python
initial_scene_selection_version = _scene_selection_version(board)
scene_gate_termination = FunctionalTermination(
    lambda _messages: _new_pending_scene_selection(
        board, initial_scene_selection_version
    )
)
return MagenticOneGroupChat(
    participants=agents,
    model_client=orchestrator,
    termination_condition=scene_gate_termination,
    max_turns=MAX_TURNS,
)
```

Do not import AutoGen at module scope. Do not add a text-mention termination.

- [ ] **Step 6: Prove the captured condition changes only after a new proposal**

Extend `test_build_team_constructs` after team creation:

```python
predicate = cast(Callable[[list[object]], bool], created["termination_predicate"])
assert predicate([]) is False
board.save("scene_selection", _selection())
assert predicate([]) is True
```

For the revision case, create the board with version 1 before `build_production_team`, save a changed unconfirmed proposal as version 2 afterward, and assert the captured condition changes from `False` to `True`.
Capture `_install_fake_autogen`'s return value as `created`, and add `Callable` from
`collections.abc` plus `cast` from `typing` to the test imports.

- [ ] **Step 7: Run focused and neighboring tests**

Run:

```powershell
uv run pytest tests/test_production_agents.py tests/test_production_tools_scene_gate.py -vv
uv run mypy src/laura/short_creator/production_agents.py tests/test_production_agents.py
uv run ruff check src/laura/short_creator/production_agents.py tests/test_production_agents.py
```

Expected: all tests PASS; Mypy and Ruff report no issues.

- [ ] **Step 8: Commit Task 1**

```powershell
git add services/local-api/src/laura/short_creator/production_agents.py services/local-api/tests/test_production_agents.py
git commit -m "fix(short-creator): stop team after scene proposal"
```

---

### Task 2: Preserve and fail closed on AutoGen stop reasons

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_orchestrator.py:391-437,477-514`
- Modify: `services/local-api/tests/test_production_orchestrator.py:1231-1345`
- Modify: `services/local-api/tests/test_production_event_log.py:45-145`

**Interfaces:**
- Consumes: duck-typed AutoGen `TaskResult.stop_reason: str | None`.
- Produces: `_turn_budget_exhausted(stop_reason: str | None) -> bool`.
- Preserves: `StageOutcome.status` remains `Literal["ok", "hard_fail"]`; Task 3 introduces a production-result-only awaiting status without broadening the shared escalation type.

- [ ] **Step 1: Write failing `_parse_outcome` stop-reason tests**

Add to `test_production_orchestrator.py`:

```python
def test_parse_outcome_max_turns_hard_fails(tmp_path: Path) -> None:
    from laura.short_creator.production_orchestrator import _parse_outcome

    board = _make_board(tmp_path)
    result = SimpleNamespace(
        messages=[_SummaryMsg("still waiting")],
        stop_reason="Maximum number of turns 30 reached.",
    )

    outcome = _parse_outcome(board, result, stage="A", tool_calls=3)

    assert outcome.status == "hard_fail"
    assert "Maximum number of turns 30 reached" in outcome.summary


def test_parse_outcome_functional_termination_stays_ok(tmp_path: Path) -> None:
    from laura.short_creator.production_orchestrator import _parse_outcome

    board = _make_board(tmp_path)
    result = SimpleNamespace(
        messages=[_SummaryMsg("proposal saved")],
        stop_reason="FunctionalTermination triggered",
    )

    outcome = _parse_outcome(board, result, stage="A", tool_calls=1)

    assert outcome.status == "ok"
    assert outcome.summary == "proposal saved"
```

- [ ] **Step 2: Write the failing stream-adapter regression**

Change the local `TaskResult` fake in `test_production_event_log.py` to accept a stop reason:

```python
class TaskResult:
    def __init__(self, messages: list[Any], stop_reason: str = "done") -> None:
        self.messages = messages
        self.stop_reason = stop_reason
```

Add:

```python
def test_stream_turn_exhaustion_is_not_reported_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[Any] = [
        _Msg("story_architect", "Paused."),
        TaskResult(
            [_Msg("story_architect", "Paused.")],
            stop_reason="Maximum number of turns 30 reached.",
        ),
    ]
    monkeypatch.setattr(po, "build_production_team", lambda *a, **k: _FakeTeam(events))
    execute = po._make_default_execute(_board(tmp_path), "a1", None, None)

    outcome = execute(None, _config(), "A", "magentic", "the task")  # type: ignore[arg-type]

    assert outcome.status == "hard_fail"
    assert "Maximum number of turns" in outcome.summary
```

- [ ] **Step 3: Run the new tests and verify RED**

Run:

```powershell
uv run pytest tests/test_production_orchestrator.py -k "max_turns or functional_termination" -vv
uv run pytest tests/test_production_event_log.py::test_stream_turn_exhaustion_is_not_reported_ok -vv
```

Expected: FAIL because `_parse_outcome` currently ignores `stop_reason` and returns `status="ok"`.

- [ ] **Step 4: Implement the narrow stop-reason classifier**

Add above `_parse_outcome`:

```python
def _turn_budget_exhausted(stop_reason: str | None) -> bool:
    return "maximum number of turns" in (stop_reason or "").lower()
```

At the start of `_parse_outcome`, capture `stop_reason`. After deriving the last non-empty message but before the zero-tool-call guard, return a hard failure for budget exhaustion:

```python
stop_reason_raw = getattr(result, "stop_reason", None)
stop_reason = str(stop_reason_raw) if stop_reason_raw is not None else None
if _turn_budget_exhausted(stop_reason):
    return StageOutcome(
        status="hard_fail",
        weak=_qa_weak(board),
        summary=(stop_reason or summary)[:2000],
        team="magentic",
        stage=stage,
    )
```

Do not classify `FunctionalTermination` as failure; Task 3 maps the board's pending state to the public awaiting result.

- [ ] **Step 5: Run focused and neighboring tests**

```powershell
uv run pytest tests/test_production_orchestrator.py -k "parse_outcome" -vv
uv run pytest tests/test_production_event_log.py -vv
uv run mypy src/laura/short_creator/production_orchestrator.py tests/test_production_orchestrator.py tests/test_production_event_log.py
uv run ruff check src/laura/short_creator/production_orchestrator.py tests/test_production_orchestrator.py tests/test_production_event_log.py
```

Expected: all tests PASS; Mypy and Ruff report no issues.

- [ ] **Step 6: Commit Task 2**

```powershell
git add services/local-api/src/laura/short_creator/production_orchestrator.py services/local-api/tests/test_production_orchestrator.py services/local-api/tests/test_production_event_log.py
git commit -m "fix(short-creator): fail closed on team turn exhaustion"
```

---

### Task 3: Expose a truthful `awaiting_user_input` production result

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_orchestrator.py:24-48,519-559,720-763,843-884`
- Modify: `services/local-api/tests/test_production_orchestrator_scene_gate.py:141-268`
- Modify: `services/local-api/tests/test_production_event_log.py:64-145`

**Interfaces:**
- Consumes: existing `StageOutcome.status: Literal["ok", "hard_fail"]` and authoritative `Board.resume_point(expected_scenes)`.
- Produces: `ProductionRunStatus = Literal["ok", "hard_fail", "awaiting_user_input"]` local to `production_orchestrator.py`.
- Produces: `_awaiting_scene_selection(board: Board, expected_scenes: list[int]) -> bool`.
- Produces: `_completed_result(..., status: ProductionRunStatus, ...)`, where `ok` is true for both `ok` and `awaiting_user_input`, while `complete` remains true only at `resume_point == "done"`.

- [ ] **Step 1: Tighten the existing plain-resume test to require the explicit status**

In `test_run_awaiting_selection_never_spawns_team`, add:

```python
assert result["status"] == "awaiting_user_input"
assert result["ok"] is True
assert result["complete"] is False
```

The existing assertions that `calls == []` and `board.meta().status == "active"` remain.

- [ ] **Step 2: Replace the pending-message test with a proposal-revision regression**

Rename `test_run_awaiting_selection_with_message_still_runs_the_team` to
`test_pending_selection_revision_runs_once_then_parks_again`. Extend the existing local
`_proposal` helper with a keyword-only `rationale: str = "starker hook"` argument and pass it
into the candidate's `rationale` field. Inside the injected `execute`, save a changed
unconfirmed proposal before returning:

```python
def execute(
    db: Database,
    config: providers.AgentConfig,
    stage: str,
    kind: str,
    task: str,
) -> orchestrator.StageOutcome:
    calls.append((stage, kind))
    board.save("scene_selection", _proposal(rationale="revised hook"))
    return orchestrator.StageOutcome(
        status="ok",
        weak=False,
        summary="proposal revised",
        team=cast(orchestrator.TeamKind, kind),
        stage=cast(providers.Stage, stage),
    )
```

Assert:

```python
assert calls == [("A", "magentic")]
assert result["status"] == "awaiting_user_input"
assert result["resume_point"] == "scene_selection"
assert result["ok"] is True
assert board.meta().status == "active"
```

- [ ] **Step 3: Add the live-loop defense test: pending Board beats hard-fail escalation**

Add:

```python
def test_new_pending_proposal_parks_without_stage_b_retry(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    config = providers.resolve_from_env({})
    board = _make_board(db, asset_id, "sess-live-loop", scene_gate=True)
    board.save_scene_review(_review(1))
    calls: list[str] = []

    def execute(
        db: Database,
        config: providers.AgentConfig,
        stage: str,
        kind: str,
        task: str,
    ) -> orchestrator.StageOutcome:
        calls.append(stage)
        board.save("scene_selection", _proposal())
        return orchestrator.StageOutcome(
            status="hard_fail",
            weak=True,
            summary="Maximum number of turns 30 reached.",
            team=cast(orchestrator.TeamKind, kind),
            stage=cast(providers.Stage, stage),
        )

    result = production_orchestrator.run_production(
        db,
        config,
        asset_id=asset_id,
        session_id="sess-live-loop",
        task="demo",
        target_seconds=60,
        execute=execute,
    )

    assert calls == ["A"]
    assert result["status"] == "awaiting_user_input"
    assert result["ok"] is True
    assert result["complete"] is False
    assert result["resume_point"] == "scene_selection"
    assert board.meta().status == "active"
```

This reproduces the observed board/result contradiction and pins the desired precedence: a valid persisted human gate parks instead of rerunning Stage B.

- [ ] **Step 4: Run the scene-gate tests and verify RED**

```powershell
uv run pytest tests/test_production_orchestrator_scene_gate.py -vv
```

Expected: FAIL because the plain park and post-team pending state still report `status="ok"`, and a hard-fail Stage A currently triggers Stage B.

- [ ] **Step 5: Add the production-result-only status and shared gate predicate**

In `production_orchestrator.py`, define:

```python
ProductionRunStatus = Literal["ok", "hard_fail", "awaiting_user_input"]


def _awaiting_scene_selection(board: Board, expected_scenes: list[int]) -> bool:
    return (
        board.meta().scene_gate
        and isinstance(board.load("scene_selection"), SceneSelection)
        and board.resume_point(expected_scenes) == "scene_selection"
    )
```

Change `_completed_result.status` to `ProductionRunStatus` and derive:

```python
"ok": status != "hard_fail",
```

Keep `"complete": resume_point == "done"` unchanged.

- [ ] **Step 6: Use `awaiting_user_input` in both park paths**

Replace the duplicated condition in the pre-team Gate-S guard with
`_awaiting_scene_selection(board, expected_scenes)` and pass:

```python
status="awaiting_user_input"
```

After Stage A returns, compute the Board state before deciding on Stage B:

```python
awaiting_scene_selection = _awaiting_scene_selection(board, expected_scenes)
escalated = False
if outcome.status == "hard_fail" and not awaiting_scene_selection:
    outcome = _safe_execute(run, db, config, "B", "magentic", task_text)
    escalated = True
    awaiting_scene_selection = _awaiting_scene_selection(board, expected_scenes)
```

Before `_completed_result`, select the public result status:

```python
result_status: ProductionRunStatus = (
    "awaiting_user_input" if awaiting_scene_selection else outcome.status
)
result_summary = (
    "awaiting user scene selection — pick scenes in chat to continue"
    if awaiting_scene_selection
    else outcome.summary
)
```

Only set `board.status="failed"` when `result_status == "hard_fail"`; keep it `active` while awaiting. Pass `result_status` into `_completed_result`.
Pass `result_summary` as its summary so a defensive park after turn exhaustion cannot expose
the old loop's failure text as the current user action.

- [ ] **Step 7: Prove confirmed and gate-off paths remain unchanged**

Retain and run these existing tests without weakening them:

```powershell
uv run pytest tests/test_production_orchestrator_scene_gate.py::test_run_confirmed_selection_runs_the_team tests/test_production_orchestrator_scene_gate.py::test_run_gate_off_board_unaffected tests/test_production_orchestrator_scene_gate.py::test_run_gate_on_with_no_proposal_yet_still_runs_the_team -vv
```

Expected: PASS; confirmed selection advances past `scene_selection`, gate-off behavior is unchanged, and a gate-on board with no proposal still starts Phase 1.

- [ ] **Step 8: Run focused type, lint, and regression gates**

```powershell
uv run pytest tests/test_production_orchestrator_scene_gate.py tests/test_production_event_log.py -vv
uv run mypy src/laura/short_creator/production_orchestrator.py tests/test_production_orchestrator_scene_gate.py tests/test_production_event_log.py
uv run ruff check src/laura/short_creator/production_orchestrator.py tests/test_production_orchestrator_scene_gate.py tests/test_production_event_log.py
```

Expected: all tests PASS; Mypy and Ruff report no issues.

- [ ] **Step 9: Commit Task 3**

```powershell
git add services/local-api/src/laura/short_creator/production_orchestrator.py services/local-api/tests/test_production_orchestrator_scene_gate.py services/local-api/tests/test_production_event_log.py
git commit -m "fix(short-creator): report scene gate as awaiting input"
```

---

### Task 4: Whole-fix verification and living-task documentation

**Files:**
- Modify: `tasks/todo.md` after the existing `Szenen-Auswahl (Gate S) + Voice pro Szene` section
- Verify only: all files modified in Tasks 1-3

**Interfaces:**
- Consumes: the final behavior and actual verification output from Tasks 1-3.
- Produces: a checked `[x]` task block documenting the hard-stop invariant, external confirmation boundary, truthful result contract, and exact observed gates.

- [ ] **Step 1: Run the complete focused Gate-S and conversation regression suite**

```powershell
cd services/local-api
uv run pytest tests/test_production_agents.py tests/test_production_tools_scene_gate.py tests/test_production_orchestrator_scene_gate.py tests/test_api_scene_selection.py tests/test_chat_executor.py tests/test_production_event_log.py -vv
```

Expected: PASS with no repeated-run regression, no failed test, and no unexpected deselection.

- [ ] **Step 2: Run the complete backend quality gates**

```powershell
uv run pytest -p no:cacheprovider
uv run mypy
uv run ruff check src tests
```

Expected: full pytest suite PASS (optional extras may skip only their existing guarded tests), Mypy reports `Success: no issues found`, Ruff reports `All checks passed!`.

- [ ] **Step 3: Inspect the final diff for scope and accidental state changes**

```powershell
git status --short --branch
git diff --check
git diff --stat e8b4cc1..HEAD
git diff e8b4cc1..HEAD -- services/local-api/src/laura/short_creator/production_agents.py services/local-api/src/laura/short_creator/production_orchestrator.py services/local-api/tests/test_production_agents.py services/local-api/tests/test_production_orchestrator.py services/local-api/tests/test_production_orchestrator_scene_gate.py services/local-api/tests/test_production_event_log.py tasks/todo.md
```

Expected: only the approved hard-stop code/tests/docs are present; unrelated untracked files remain untouched; no whitespace errors.

- [ ] **Step 4: Record the verified correction in `tasks/todo.md`**

Append a compact checked section that states all of these exact invariants:

```markdown
## Gate-S Orchestrator Hard-Stop  `[x]`  (Fix 2026-08-08)
- [x] Ein neu persistiertes, unbestätigtes `scene_selection` beendet den aktiven
      `MagenticOneGroupChat` über eine boardbasierte `FunctionalTermination`; kein Agent
      wird zum Warten erneut aufgerufen.
- [x] `select_scenes` / `confirm_scene_selection` bleibt der einzige Confirmation-Pfad;
      Agententext kann `confirmed_utc` und `selected_scene_numbers` nicht setzen.
- [x] Geparkte Runs melden `status=awaiting_user_input`, `ok=true`, `complete=false` und
      `resume_point=scene_selection`; echte Turn-Erschöpfung ohne Gate-Transition ist
      `hard_fail`.
- [x] Verifiziert mit fokussierten Gate-S-/Chat-/Event-Log-Tests sowie vollständigem
      Backend-Pytest, Mypy strict und Ruff; die exakten Laufzahlen stehen im Commit-Bericht.
```

- [ ] **Step 5: Commit the verification record**

```powershell
git add tasks/todo.md
git commit -m "docs(tasks): record Gate-S hard-stop verification"
```

- [ ] **Step 6: Final evidence snapshot**

```powershell
git status --short --branch
git log --oneline -5
git rev-parse HEAD
```

Expected: only the user's pre-existing untracked files remain; the feature branch contains the design commit plus the three fix commits and the verification-doc commit; report the exact HEAD and test summaries without claiming a live provider run.

---

## Plan Self-Review Mapping

- Immediate in-run hard stop: Task 1.
- No repeated wait/delegate conversation: Task 1 predicate and wiring tests; Task 4 focused suite.
- Proposal-revision run stops again: Task 1 revision snapshot test; Task 3 revision regression.
- External-only confirmation boundary: unchanged implementation, pinned by Task 4's existing API/chat tests.
- Explicit `awaiting_user_input`: Task 3.
- Turn-budget false-success closure: Task 2, with stream-level regression.
- Plain resume and confirmed-resume compatibility: Task 3 existing tests.
- Optional AutoGen import boundary: Task 1 hermetic fake plus existing missing-extra test.
- Full quality and scope evidence: Task 4.
