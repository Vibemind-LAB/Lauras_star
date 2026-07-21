# Production Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three live-evidenced wounds of 2026-07-20: the summary that echoes the task text, the silent local-7B text-agent trap, and the invisible target-length shortfall.

**Architecture:** Three independent, small changes (spec `docs/superpowers/specs/2026-07-21-production-hardening-design.md`): (1) `_parse_outcome` takes the LAST non-empty message instead of concatenating all of them; (2) a `config_warnings` advisory parallel to `config_problems`, surfaced in the two production-enqueue responses and as one run-log event; (3) a `target_ratio` report field on `RenderReport` plus a note in the render tool's reply — reporting, never a gate.

**Tech Stack:** Python 3.11 + uv, pydantic v2, pytest, mypy strict, ruff (line length 100), FastAPI TestClient.

## Global Constraints

- Python via `uv` in `services/local-api/`; run tests as `uv run pytest …`. Gates are **bare `uv run mypy`** (must print "Success" — CI checks src AND tests; `mypy src` alone hid 11 errors once), `uv run pytest -q` (judge by exit code, `; echo EXIT=$?`), and `uv run ruff check src tests` ("All checks passed!").
- **Never run two `uv run` commands in parallel**; ALL commands FOREGROUND — never run_in_background, never arm monitors; for the full suite pass `timeout: 600000` to the Bash tool.
- mypy strict: annotate everything, no `Any` leaks in signatures; no `print` outside `scripts/`.
- Old boards must keep loading: every new model field needs a default; never add validation that rejects previously-written JSON. `content_hash` semantics stay untouched (only `version` excluded).
- Warnings are ADVISORY: `config_warnings` must never block an enqueue and never fail a run; a crashing event sink must never affect the run.
- `render_production`'s check list stays THREE checks — the target ratio is a report field + note, never a member of `checks` (a failing length check would provoke render thrashing).
- Commits: conventional commits, English, explicit `git add <paths>` (never `-A`), trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- All commands below run from `services/local-api/` unless a path says otherwise.

---

### Task 1: `_parse_outcome` reports the team's answer, not the task

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_orchestrator.py` (function `_parse_outcome`, ~lines 230–244)
- Test: `services/local-api/tests/test_production_orchestrator.py` (append)

**Interfaces:**
- Consumes: `StageOutcome`, `_qa_weak(board)`, `Board`/`BoardMeta` (all existing; the test builds a real `Board` on tmp_path exactly like the file's existing tests do — reuse the file's existing board-construction helper if one exists, otherwise `Board.create(tmp_path / "board", BoardMeta(session_id="s1", asset_id="a1", created_utc="2026-07-21T00:00:00+00:00", task="t", language="English", target_seconds=60.0))` with imports from `laura.short_creator.board` / `board_models`).
- Produces: unchanged signature `_parse_outcome(board, result, *, stage) -> StageOutcome`; later tasks do not depend on it.

- [ ] **Step 1: Write the failing test**

Append to `services/local-api/tests/test_production_orchestrator.py` (reuse the file's existing imports; add only what is missing):

```python
class _SummaryMsg:
    def __init__(self, content: str) -> None:
        self.content = content


def test_parse_outcome_summary_is_the_last_answer_not_the_task(tmp_path: Path) -> None:
    """Live finding 2026-07-20: every run's result.summary was the TASK text — _parse_outcome
    concatenated ALL messages and truncated to 2000 chars, so messages[0] (the task) always
    won. The summary must be the team's final answer."""
    from laura.short_creator.production_orchestrator import _parse_outcome

    board = _make_board(tmp_path)  # use the file's existing board helper; see Interfaces
    task_echo = "1) GOAL: build the film... " * 100  # long, like the real task text
    result = SimpleNamespace(
        messages=[
            _SummaryMsg(task_echo),
            _SummaryMsg("intermediate tool chatter"),
            _SummaryMsg("The film is built: 6 chapters, QA verdict ship."),
        ]
    )

    outcome = _parse_outcome(board, result, stage="A")

    assert outcome.summary == "The film is built: 6 chapters, QA verdict ship."


def test_parse_outcome_skips_trailing_empty_messages(tmp_path: Path) -> None:
    from laura.short_creator.production_orchestrator import _parse_outcome

    board = _make_board(tmp_path)
    result = SimpleNamespace(
        messages=[_SummaryMsg("the real answer"), _SummaryMsg("   "), _SummaryMsg("")]
    )

    outcome = _parse_outcome(board, result, stage="A")

    assert outcome.summary == "the real answer"


def test_parse_outcome_empty_result_gives_empty_summary(tmp_path: Path) -> None:
    from laura.short_creator.production_orchestrator import _parse_outcome

    board = _make_board(tmp_path)

    outcome = _parse_outcome(board, SimpleNamespace(messages=[]), stage="A")

    assert outcome.summary == ""
```

Notes for the implementer: `SimpleNamespace` comes from `types`. If the test file has no board helper, define `_make_board(tmp_path)` once at module level per the Interfaces block — do NOT duplicate board construction in each test.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_production_orchestrator.py -k parse_outcome -v`
Expected: FAIL — the first test's summary starts with `"1) GOAL: build the film..."` (the concatenation truncated at 2000 chars).

- [ ] **Step 3: Implement**

Replace the body of `_parse_outcome` (keep the docstring's first paragraph about `weak`, extend it):

```python
def _parse_outcome(board: Board, result: Any, *, stage: Stage) -> StageOutcome:
    """Read a finished production-team run into an outcome.

    ``weak`` comes from the board's QA verdict, not message scanning: v2's QA reviewer writes a
    structured ``QaReport`` (``verdict="ship"|"revise"``) to the board via ``save_qa_report``
    rather than saying the word "weak" in chat (v1's convention), so a missing report or a
    "revise" verdict is weak and only an explicit "ship" verdict is not.

    ``summary`` is the LAST non-empty message — the team's final answer. Concatenating all
    messages and truncating put ``messages[0]`` (the task text) into every summary
    (live finding 2026-07-20: three runs in a row "summarized" themselves with their own task).
    """
    summary = ""
    for msg in reversed(getattr(result, "messages", None) or []):
        to_text = getattr(msg, "to_model_text", None)
        text = (to_text() if callable(to_text) else str(getattr(msg, "content", ""))).strip()
        if text:
            summary = text[:2000]
            break
    return StageOutcome(
        status="ok", weak=_qa_weak(board), summary=summary, team="magentic", stage=stage
    )
```

- [ ] **Step 4: Run the orchestrator test file**

Run: `uv run pytest tests/test_production_orchestrator.py -q; echo EXIT=$?`
Expected: EXIT=0 (new tests pass; the existing entry/short-circuit tests do not assert on summary CONTENT beyond presence — if one does fail, read it: it is asserting the old task-echo behavior and must be updated to the new contract, stating so in the commit).

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/short_creator/production_orchestrator.py services/local-api/tests/test_production_orchestrator.py
git commit -m "fix(short-creator): the summary is the team's answer, not the task echo

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `config_warnings` — the local-7B trap gets a voice

**Files:**
- Modify: `services/local-api/src/laura/short_creator/providers.py` (new function after `config_problems`)
- Modify: `services/local-api/src/laura/api/short_creator.py` (endpoints `create_production` ~line 286 and the message endpoint ~line 334 — the two `production.run` enqueues)
- Modify: `services/local-api/src/laura/short_creator/production_orchestrator.py` (`run_production`: one event line)
- Test: `services/local-api/tests/test_agent_preflight.py` (unit), `services/local-api/tests/test_production_liveness.py` (endpoint), `services/local-api/tests/test_production_orchestrator.py` (event line)

**Interfaces:**
- Consumes: `AgentConfig` (fields `provider`, `agent_model`), `config_problems` (pattern neighbor), `resolve_from_env`, `run_production(db, config, ...)` with its `event_sink` plumbing (the `{"type": "restored", ...}` emission is the pattern to sit next to).
- Produces: `config_warnings(config: AgentConfig) -> list[str]` (importable from `laura.short_creator.providers`); response key `warnings: list[str]` on both 202 responses; event `{"type": "config_warning", "warnings": [...]}`.

- [ ] **Step 1: Write the failing unit tests**

Append to `services/local-api/tests/test_agent_preflight.py` (follow its existing AgentConfig-construction pattern — it already builds configs for `config_problems` tests):

```python
def test_config_warnings_flags_local_ollama_text_agents() -> None:
    """Live incident 2026-07-20: three production runs silently ran their text agents on
    qwen2.5:7b (provider default ollama) — tool calls came out as JSON prose, save_storyline
    got an invented schema, the orchestrator hallucinated "saved". One advisory line at
    enqueue time would have saved the hour."""
    config = resolve_from_env({})  # zero env -> ollama default

    warnings = config_warnings(config)

    assert len(warnings) == 1
    assert "ollama" in warnings[0]
    assert config.agent_model in warnings[0]
    assert "LAURA_AGENT_PROVIDER=openai-compat" in warnings[0]


def test_config_warnings_empty_for_hosted_providers() -> None:
    hosted = resolve_from_env(
        {"LAURA_AGENT_PROVIDER": "openai-compat", "LAURA_AGENT_API_KEY": "k"}
    )
    routed = resolve_from_env(
        {"LAURA_AGENT_PROVIDER": "9router", "LAURA_9ROUTER_API_KEY": "k"}
    )

    assert config_warnings(hosted) == []
    assert config_warnings(routed) == []
```

(Import `config_warnings` next to the file's existing `config_problems`/`resolve_from_env` imports.)

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_agent_preflight.py -k config_warnings -v`
Expected: FAIL — ImportError: cannot import name `config_warnings`.

- [ ] **Step 3: Implement `config_warnings`**

In `services/local-api/src/laura/short_creator/providers.py`, directly after `config_problems`:

```python
def config_warnings(config: AgentConfig) -> list[str]:
    """Advisory (non-fatal) findings about the resolved agent config.

    Parallel to :func:`config_problems`: problems block an enqueue (503), warnings are only
    surfaced — in the enqueue response and as one run-log line. Live incident 2026-07-20:
    three production runs silently ran their text agents on local qwen2.5:7b (the provider
    DEFAULT is ollama), which emits tool calls as prose and invents schemas; nothing said so
    anywhere. Local-first stays intact: warning, never a gate.
    """
    warnings: list[str] = []
    if config.provider == "ollama":
        warnings.append(
            f"text agents run on local ollama model {config.agent_model!r}: small local "
            "models are known to fail Magentic tool-calling (tool calls as prose, invented "
            "schemas); for production runs set LAURA_AGENT_PROVIDER=openai-compat and a "
            "hosted LAURA_AGENT_MODEL"
        )
    return warnings
```

- [ ] **Step 4: Verify unit tests pass**

Run: `uv run pytest tests/test_agent_preflight.py -q; echo EXIT=$?`
Expected: EXIT=0.

- [ ] **Step 5: Write the failing endpoint test**

Append to `services/local-api/tests/test_production_liveness.py` (reuse its `_app`/`_seed_asset`/`_start`-style helpers and the `_autoshort_available` monkeypatch; note `_start` posts the create endpoint):

```python
def test_production_enqueue_response_carries_config_warnings(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The 202 must say out loud when the text agents will run on a local ollama model."""
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    monkeypatch.delenv("LAURA_AGENT_PROVIDER", raising=False)  # -> ollama default
    client, db = _app(tmp_path)
    asset_id = _seed_asset(db)

    r = client.post(
        f"/assets/{asset_id}/production",
        json={"task": "a recap", "target_seconds": 30},
        headers=_H,
    )

    assert r.status_code == 202, r.text
    body = r.json()
    assert isinstance(body["warnings"], list) and len(body["warnings"]) == 1
    assert "ollama" in body["warnings"][0]


def test_production_enqueue_response_warnings_empty_for_hosted(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    monkeypatch.setenv("LAURA_AGENT_PROVIDER", "openai-compat")
    monkeypatch.setenv("LAURA_AGENT_API_KEY", "k")
    client, db = _app(tmp_path)
    asset_id = _seed_asset(db)

    r = client.post(
        f"/assets/{asset_id}/production",
        json={"task": "a recap", "target_seconds": 30},
        headers=_H,
    )

    assert r.status_code == 202, r.text
    assert r.json()["warnings"] == []
```

- [ ] **Step 6: Run to verify they fail**

Run: `uv run pytest tests/test_production_liveness.py -k warnings -v`
Expected: FAIL — KeyError: 'warnings'.

- [ ] **Step 7: Wire the endpoints**

In `services/local-api/src/laura/api/short_creator.py`: import `config_warnings` beside the existing providers imports (the module already imports from `..short_creator.providers` for `_require_usable_agent_config` — follow that import path). In `create_production`, change the return to:

```python
    return {
        "session_id": session_id,
        "job_id": job_id,
        "warnings": config_warnings(resolve_from_env()),
    }
```

In the message endpoint (`POST /production/{session_id}/message`), add the same `"warnings": config_warnings(resolve_from_env())` key to its 202 response dict (read the function first; keep every existing key).

- [ ] **Step 8: Verify endpoint tests pass**

Run: `uv run pytest tests/test_production_liveness.py -q; echo EXIT=$?`
Expected: EXIT=0 (the file's pre-existing tests keep passing — the new key is additive; `test_handle_production_run_asset_missing…` in test_production_run_handler.py is NOT affected because it tests the handler, not the endpoint).

- [ ] **Step 9: Write the failing event-line test**

Append to `services/local-api/tests/test_production_orchestrator.py` (reuse the file's existing run_production fixtures: injected execute, recording event sink — copy the construction from the existing restored-event test):

```python
def test_run_production_logs_a_config_warning_line_for_local_ollama(
    tmp_path: Path,
) -> None:
    """One {"type": "config_warning"} line in the run log when the text agents are local —
    the run log is where the 55-minute-invisibility class of incidents gets diagnosed."""
    # Build db/asset/config/execute exactly like the neighboring run_production tests,
    # but with config = resolve_from_env({}) (ollama default) and a recording sink.
    events: list[dict[str, Any]] = []
    # ... existing-arrangement here (see the restored-event test in this file) ...
    result = run_production(  # same call shape as the neighboring test
        db, resolve_from_env({}), asset_id=asset_id, session_id="s-warn",
        task="t", execute=execute, event_sink=events.append,
    )

    assert result["ok"] is True
    warn_lines = [e for e in events if e.get("type") == "config_warning"]
    assert len(warn_lines) == 1
    assert "ollama" in warn_lines[0]["warnings"][0]
```

NOTE for the implementer: the `# ... existing-arrangement here ...` line is deliberate — this file already has a complete arrangement for run_production-with-sink tests (the restored-event test). Copy THAT arrangement verbatim (db seeding, execute stub, call signature — the real signature may differ slightly from the sketch above; the assertions are the contract). Also assert the inverse in a second test: with a hosted config (`resolve_from_env({"LAURA_AGENT_PROVIDER": "openai-compat", "LAURA_AGENT_API_KEY": "k"})`) no `config_warning` event is emitted.

- [ ] **Step 10: Run to verify it fails, then wire run_production**

Run: `uv run pytest tests/test_production_orchestrator.py -k config_warning -v` → FAIL (no such event).

In `run_production` (production_orchestrator.py), directly BEFORE the existing restored-event emission (same sink pattern, same defensive wrapper style):

```python
    warnings = config_warnings(config)
    if warnings and event_sink is not None:
        try:
            event_sink({"type": "config_warning", "warnings": warnings})
        except Exception:  # noqa: BLE001 - observability must never break the run
            logger.warning("config_warning event sink failed", exc_info=True)
```

Import `config_warnings` from `.providers` beside the module's existing providers imports.

- [ ] **Step 11: Verify, then commit**

Run: `uv run pytest tests/test_production_orchestrator.py tests/test_agent_preflight.py tests/test_production_liveness.py -q; echo EXIT=$?`
Expected: EXIT=0.

```bash
git add services/local-api/src/laura/short_creator/providers.py services/local-api/src/laura/api/short_creator.py services/local-api/src/laura/short_creator/production_orchestrator.py services/local-api/tests/test_agent_preflight.py services/local-api/tests/test_production_liveness.py services/local-api/tests/test_production_orchestrator.py
git commit -m "feat(short-creator): warn out loud when text agents run on local ollama

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `target_ratio` — the length shortfall becomes visible

**Files:**
- Modify: `services/local-api/src/laura/short_creator/board_models.py` (`RenderReport`)
- Modify: `services/local-api/src/laura/short_creator/production_tools.py` (`render_production`, report construction ~line 2170 and return ~line 2189)
- Test: `services/local-api/tests/test_production_tools_render.py` (append)

**Interfaces:**
- Consumes: `RenderReport` (existing fields incl. `video_s`, `checks`, `parents`), `board.meta().target_seconds` (BoardMeta), the render test file's existing seeding helpers (`_save_voice`, fake render fn, board fixtures — read the file's existing render_production tests and reuse their arrangement).
- Produces: `RenderReport.target_ratio: float | None = None`; render tool reply key `target_note: str` (only present when a ratio exists).

- [ ] **Step 1: Write the failing tests**

Append to `services/local-api/tests/test_production_tools_render.py`, copying the arrangement of an existing passing `render_production` test (board seeded through storyline/script/voice/cutlist, fake render segments fn, export row driven to ready) — only the assertions below are new contract:

```python
def test_render_report_carries_target_ratio_and_note(tmp_path: Path) -> None:
    """Live finding 2026-07-20: a 31.6s film against a 174s target shipped with QA 'ship' —
    voice_fits checks A/V cover, nothing reported target adherence. Reporting only, never a
    fourth check (a failing length check would provoke render thrashing)."""
    # ... arrange exactly like the neighboring successful render_production test ...
    out = specs["render_production"].func()

    assert out["ok"] is True
    assert "target_note" in out
    assert "vs target" in out["target_note"]
    render = board.load("render_report")
    assert isinstance(render, RenderReport)
    assert render.target_ratio is not None
    # ratio == video_s / meta.target_seconds, rounded to 3 places
    assert render.target_ratio == pytest.approx(
        round(render.video_s / board.meta().target_seconds, 3)
    )
    # the checks list stays exactly three — target adherence must never gate
    assert [c.name for c in render.checks] == [
        "voice_fits", "export_ready", "has_voice_timings"
    ]


def test_old_render_report_json_without_target_ratio_still_loads() -> None:
    report = RenderReport.model_validate_json(
        '{"export_id": "e1", "video_s": 10.0, "width": 1920, "height": 1080}'
    )
    assert report.target_ratio is None
```

NOTE: the `# ... arrange exactly like ...` line is deliberate — reuse the file's existing arrangement verbatim (it is long and already correct); the new tests' assertions are the contract. If `board.meta()` is not reachable in that arrangement, read the seeded `BoardMeta.target_seconds` value directly from the fixture and assert against that number.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_production_tools_render.py -k target -v`
Expected: FAIL — `target_note` missing / `RenderReport` has no attribute `target_ratio`.

- [ ] **Step 3: Implement**

In `services/local-api/src/laura/short_creator/board_models.py`, add to `RenderReport` (beside `video_s`/`voice_s`):

```python
    # video_s / the board's target_seconds, rounded to 3 places; None when no usable target.
    # Reporting only — never a member of checks (a failing length gate would provoke render
    # thrashing); QA reads it and weighs the shortfall in its verdict.
    target_ratio: float | None = None
```

In `services/local-api/src/laura/short_creator/production_tools.py`, inside `render_production` after `video_s` is known (before the `RenderReport(...)` construction):

```python
            target_s = board.meta().target_seconds
            target_ratio = round(video_s / target_s, 3) if target_s > 0 else None
```

Add `target_ratio=target_ratio,` to the `RenderReport(...)` constructor call, and change the return to:

```python
            reply: dict[str, Any] = {
                "ok": ok,
                "export_id": export_id,
                "checks": [c.model_dump() for c in checks],
            }
            if target_ratio is not None:
                reply["target_note"] = (
                    f"video {video_s:.1f}s vs target {target_s:.1f}s ({target_ratio:.0%})"
                )
            return reply
```

- [ ] **Step 4: Run the render test file**

Run: `uv run pytest tests/test_production_tools_render.py -q; echo EXIT=$?`
Expected: EXIT=0. If a pre-existing test asserts the reply as an EXACT dict, extend that assertion with the new `target_note` key (field additions break exact-dict asserts — known lesson); say so in the commit body.

- [ ] **Step 5: Full gates + commit**

Run sequentially, judging by exit code: `uv run pytest -q` (timeout 600000) → `uv run mypy` (bare!) → `uv run ruff check src tests`.
Expected: exit 0 / "Success" / "All checks passed!".

```bash
git add services/local-api/src/laura/short_creator/board_models.py services/local-api/src/laura/short_creator/production_tools.py services/local-api/tests/test_production_tools_render.py
git commit -m "feat(short-creator): the render report says how much of the target it hit

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```
