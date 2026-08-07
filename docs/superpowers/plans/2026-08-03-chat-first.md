# Chat-first Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Laura becomes a chat-first app: a conversation layer + one-turn router agent orchestrate the existing machinery (production, discovery, import), with server-enforced approval cards and live narration; the desktop gets a three-column chat stage as the default view.

**Architecture:** Backend first: migration 0034 + conversation repos, service extractions so the router can call what the endpoints call, an NDJSON events reader, the router (scout-style injectable runner), the executor, and `api/chat.py`. Frontend second: client methods, an export lane for `laura-media://`, thread components, and the `ChatStage` wired as default. Spec: `docs/superpowers/specs/2026-08-03-chat-first-design.md`.

**Tech Stack:** Python 3.11 + FastAPI + SQLite (uv, pytest, mypy, ruff) · React + TypeScript strict + Tailwind (pnpm, vitest) · Electron main-process protocol handler.

## Global Constraints

- Code identifiers/comments/commit messages **English**; UI copy **German** (CLAUDE.md).
- TypeScript `strict`, **never `any`** (→ `unknown` + narrow). Python: bare `uv run mypy` must stay clean (it checks tests too), `uv run ruff check src tests` clean.
- Auth on every new endpoint: `X-Laura-Token` via the existing `require_permission` dependencies; read = `"read"`, mutations = `"timeline:edit"`, project creation = `"project:write"`.
- **Polling, no SSE.** Frontend poll cadence 2500 ms (existing app pattern).
- **One tool call per router turn.** Router context = active project summary + last **20** messages compacted.
- Conversation `title` = first user message cut to **60** chars.
- Events endpoint contract: `GET /production/{session_id}/events?after=N`, N is a **0-based line index** (`after=0` = everything), response `{"events": [...], "next": int, "done": bool}`, `events` are the raw NDJSON objects, unparsable lines are skipped but still counted by the cursor.
- Approval: side-effectful execution lives **only** behind `POST /conversations/{id}/approvals/{message_id}`. v1 `action_type` is only `"import_urls"`. Double-decide → 409.
- The 7 existing views stay untouched (tool mode). Gates per task: the named tests; final task runs `uv run pytest -q`, `uv run mypy`, `uv run ruff check src tests`, `pnpm test -- --run`, `pnpm typecheck`.

---

## File map

| File | Responsibility |
|---|---|
| `services/local-api/src/laura/db/migrations/0034_conversations.sql` | Create | conversations + conversation_messages |
| `services/local-api/src/laura/db/repos.py` | Modify | conversation repos (append at end of file) |
| `services/local-api/src/laura/api/short_creator.py` | Modify | extract 4 service functions; add events endpoint |
| `services/local-api/src/laura/chat/__init__.py` | Create | empty package marker |
| `services/local-api/src/laura/chat/router.py` | Create | decision parse/validate, context compose, run_router |
| `services/local-api/src/laura/chat/executor.py` | Create | validated decision → messages + machinery calls |
| `services/local-api/src/laura/api/chat.py` | Create | conversations CRUD, message turn, approvals |
| `services/local-api/src/laura/main.py` | Modify | register chat router |
| `services/local-api/src/laura/api/timelines.py` | Modify | `GET /exports/{export_id}` |
| `apps/desktop/src/api.ts` | Modify | chat types + client methods, `getExport` |
| `apps/desktop/src/main.ts` | Modify | `laura-media://export/{export_id}` lane |
| `apps/desktop/src/components/ChatPanel.tsx` | Modify | `export` the existing `EventLine` |
| `apps/desktop/src/components/chat/ApprovalCard.tsx` etc. | Create | thread cards (see Task 9-11) |
| `apps/desktop/src/components/chat/ChatStage.tsx` | Create | three-column assembly |
| `apps/desktop/src/App.tsx` + `src/components/NavRail.tsx` | Modify | stage `"chat"` default (LAST task) |

---

### Task 1: Migration 0034 + conversation repos

**Files:**
- Create: `services/local-api/src/laura/db/migrations/0034_conversations.sql`
- Modify: `services/local-api/src/laura/db/repos.py` (append at end)
- Test: `services/local-api/tests/test_conversation_repos.py`

**Interfaces (Produces):**
```python
repos.create_conversation(db, *, conversation_id: str, created_utc: str) -> None
repos.list_conversations(db) -> list[dict[str, Any]]           # newest updated first
repos.get_conversation(db, conversation_id: str) -> dict[str, Any] | None
repos.delete_conversation(db, conversation_id: str) -> None     # messages cascade
repos.set_conversation_title(db, conversation_id: str, title: str) -> None
repos.set_conversation_project(db, conversation_id: str, project_id: str | None) -> None
repos.touch_conversation(db, conversation_id: str, updated_utc: str) -> None
repos.append_conversation_message(db, *, message_id: str, conversation_id: str,
    role: str, kind: str, content: dict[str, Any], created_utc: str) -> int  # returns seq
repos.list_conversation_messages(db, conversation_id: str) -> list[dict[str, Any]]  # seq order, content parsed
repos.get_conversation_message(db, message_id: str) -> dict[str, Any] | None        # content parsed
repos.update_conversation_message_content(db, message_id: str, content: dict[str, Any]) -> None
```

- [ ] **Step 1: Write the migration**

```sql
-- Chat-first (spec 2026-08-03): a global conversation list (ChatGPT-style). A conversation
-- "stands" on an active project (switchable per command); messages carry their variance in
-- content_json (kind: text | approval_request | action). seq gives gapless per-thread order.
CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    active_project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE conversation_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    kind TEXT NOT NULL CHECK (kind IN ('text', 'approval_request', 'action')),
    content_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (conversation_id, seq)
);

CREATE INDEX idx_conversation_messages_thread
    ON conversation_messages(conversation_id, seq);
```

- [ ] **Step 2: Write the failing tests**

```python
"""Conversation repos: the chat-first persistence layer (spec 2026-08-03).

seq must be gapless per conversation (the thread is rebuilt from it after restarts),
delete must cascade, and content_json round-trips as a dict.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase


def _db(tmp_path: Path) -> Database:
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db: Database = SqliteDatabase(settings.db_path)
    db.migrate()
    return db


def test_create_list_get_and_title(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repos.create_conversation(db, conversation_id="c1", created_utc="2026-08-03T10:00:00Z")
    repos.create_conversation(db, conversation_id="c2", created_utc="2026-08-03T11:00:00Z")
    repos.set_conversation_title(db, "c1", "Bau mir einen Short")
    repos.touch_conversation(db, "c1", "2026-08-03T12:00:00Z")

    rows = repos.list_conversations(db)
    assert [r["id"] for r in rows] == ["c1", "c2"], "newest updated first"
    assert repos.get_conversation(db, "c1")["title"] == "Bau mir einen Short"
    assert repos.get_conversation(db, "missing") is None


def test_messages_seq_is_gapless_and_content_round_trips(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repos.create_conversation(db, conversation_id="c1", created_utc="2026-08-03T10:00:00Z")
    s1 = repos.append_conversation_message(
        db, message_id="m1", conversation_id="c1", role="user", kind="text",
        content={"text": "hallo"}, created_utc="2026-08-03T10:00:01Z",
    )
    s2 = repos.append_conversation_message(
        db, message_id="m2", conversation_id="c1", role="assistant", kind="action",
        content={"tool": "start_short", "args": {}, "refs": {"job_id": "j1"}, "outcome": "running"},
        created_utc="2026-08-03T10:00:02Z",
    )
    assert (s1, s2) == (1, 2)
    msgs = repos.list_conversation_messages(db, "c1")
    assert [m["seq"] for m in msgs] == [1, 2]
    assert msgs[0]["content"] == {"text": "hallo"}
    assert msgs[1]["content"]["refs"]["job_id"] == "j1"


def test_update_message_content_and_get(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repos.create_conversation(db, conversation_id="c1", created_utc="2026-08-03T10:00:00Z")
    repos.append_conversation_message(
        db, message_id="m1", conversation_id="c1", role="assistant", kind="approval_request",
        content={"action_type": "import_urls", "payload": {"urls": ["u"]}, "status": "pending",
                 "decided_at": None, "result": None},
        created_utc="2026-08-03T10:00:01Z",
    )
    msg = repos.get_conversation_message(db, "m1")
    assert msg is not None and msg["content"]["status"] == "pending"
    repos.update_conversation_message_content(
        db, "m1", {**msg["content"], "status": "approved"}
    )
    assert repos.get_conversation_message(db, "m1")["content"]["status"] == "approved"


def test_delete_cascades_messages(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repos.create_conversation(db, conversation_id="c1", created_utc="2026-08-03T10:00:00Z")
    repos.append_conversation_message(
        db, message_id="m1", conversation_id="c1", role="user", kind="text",
        content={"text": "x"}, created_utc="2026-08-03T10:00:01Z",
    )
    repos.delete_conversation(db, "c1")
    assert repos.get_conversation(db, "c1") is None
    assert repos.get_conversation_message(db, "m1") is None
```

- [ ] **Step 3: Run tests to verify they fail** — `uv run pytest tests/test_conversation_repos.py -q` → AttributeError (missing repos functions).

- [ ] **Step 4: Implement the repos** (append to `repos.py`, mirror the `production_sessions` style; `append_conversation_message` computes `seq = COALESCE(MAX(seq),0)+1` inside the same transaction as the INSERT; `list/get` parse `content_json` into a `content` key and drop the raw column; ordering `ORDER BY updated_at DESC, id` for the list. SQLite needs `PRAGMA foreign_keys=ON` for the cascade — the codebase's `SqliteDatabase` already sets it; if the cascade test fails, delete messages explicitly in `delete_conversation` instead of relying on the pragma.)

- [ ] **Step 5: Run tests green** — same command, 4 passed.

- [ ] **Step 6: Commit** — `git add services/local-api/src/laura/db/migrations/0034_conversations.sql services/local-api/src/laura/db/repos.py services/local-api/tests/test_conversation_repos.py` · `git commit -m "feat(chat): conversations schema + repos (migration 0034)"`

---

### Task 2: Service extractions in `api/short_creator.py`

The router executor must call what the endpoints call — without HTTP. Pure refactor: move each endpoint body into a module-level service function; the endpoint keeps only dependency resolution and delegates. HTTPException stays the error channel (the executor catches it).

**Files:**
- Modify: `services/local-api/src/laura/api/short_creator.py`
- Test: existing suites pin behavior — `tests/test_auto_short_endpoint.py`, `tests/test_production_api.py`, `tests/test_production_liveness.py`, `tests/test_production_revert.py` must stay green **unchanged**.

**Interfaces (Produces):**
```python
run_project_auto_short(db, project_id: str, *, topic: str, target_seconds: int,
    format: Format, language: str) -> dict[str, Any]        # body of create_project_auto_short
run_project_auto_overview(db, project_id: str, *, topic: str, target_seconds: int,
    language: str) -> dict[str, Any]                         # body of create_project_auto_overview
run_production_follow_up(db, session_id: str, text: str) -> dict[str, Any]  # body of send_production_message
run_production_revert(db, session_id: str, artifact: str, version: int) -> dict[str, Any]
```

- [ ] **Step 1:** For each of the four endpoints, cut the body (everything after dependency resolution: the `db = _db(request)` line stays in the endpoint, which then calls the new function with `db` + parameters). Preflights (`_require_autoshort`, `_require_usable_agent_config`) move INTO the service functions so the executor gets them too.
- [ ] **Step 2:** Endpoints become thin delegates, e.g.:

```python
@router.post("/projects/{project_id}/auto-short", status_code=status.HTTP_202_ACCEPTED)
def create_project_auto_short(
    project_id: str, body: ProjectAutoShortRequest, request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> dict[str, Any]:
    """(docstring unchanged — move it onto the service function, keep a one-liner here)"""
    return run_project_auto_short(
        _db(request), project_id, topic=body.topic,
        target_seconds=body.target_seconds, format=body.format, language=body.language,
    )
```

- [ ] **Step 3:** Run the pinning suites: `uv run pytest tests/test_auto_short_endpoint.py tests/test_production_api.py tests/test_production_liveness.py tests/test_production_revert.py -q` → all green, zero test edits.
- [ ] **Step 4:** `uv run mypy` clean.
- [ ] **Step 5: Commit** — `git commit -m "refactor(chat): extract project/production service functions from endpoints"` (explicit add of `short_creator.py` only).

---

### Task 3: Events reader endpoint

**Files:**
- Modify: `services/local-api/src/laura/api/short_creator.py`
- Test: `services/local-api/tests/test_production_events.py`

**Interfaces (Produces):** `GET /production/{session_id}/events?after=N` → `{"events": [dict], "next": int, "done": bool}` (contract per Global Constraints). Session unknown → 404. No runs dir / no ndjson yet → `{"events": [], "next": after, "done": false}`.

- [ ] **Step 1: Failing tests** — seed a session row (`repos.create_production_session`) + a fake runs dir with an NDJSON fixture; the board root comes from `board_root_for(db, asset_id, session_id)` whose **parent** contains `runs/`:

```python
"""GET /production/{sid}/events — the run NDJSON as a pollable stream (spec 2026-08-03).

The agent events have been on disk since v2; nothing served them, so the UI could only say
"running…". Cursor is a 0-based line index; unparsable lines are skipped but counted.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.main import create_app

_TOKEN = "test-token"
_H = {"X-Laura-Token": _TOKEN}


def _app(tmp_path: Path) -> tuple[TestClient, Any, Settings]:
    settings = Settings(workspace_root=tmp_path / "ws", token=_TOKEN, start_runner=False)
    app = create_app(settings)
    return TestClient(app), app.state.db, settings


def _seed(db: Any, settings: Settings, tmp_path: Path) -> tuple[str, str]:
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(settings.workspace_root / "project-x"),
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video",
        display_name="a.mp4", source_path=str(tmp_path / "a.mp4"),
    )
    repos.create_production_session(
        db, session_id="sess1", asset_id=str(asset["id"]),
        created_utc="2026-08-03T10:00:00Z",
    )
    return str(asset["id"]), project["id"]


def _write_run_log(settings: Settings, project_id: str, lines: list[dict[str, Any] | str]) -> None:
    runs = (
        Path(settings.workspace_root) / f"project-{project_id}"  # adjust to board_root_for's real layout
        / "agent-runs" / "sess1" / "runs"
    )
    runs.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(
        line if isinstance(line, str) else json.dumps(line) for line in lines
    )
    (runs / "20260803T100000Z.ndjson").write_text(payload, encoding="utf-8")


def test_events_cursor_and_done(tmp_path: Path) -> None:
    client, db, settings = _app(tmp_path)
    _asset_id, project_id = _seed(db, settings, tmp_path)
    _write_run_log(settings, project_id, [
        {"type": "meta", "session_id": "sess1"},
        {"type": "agent", "agent": "vision_reviewer", "text": "scene 1 ok"},
        "NOT-JSON",
        {"type": "done", "ok": True},
    ])

    first = client.get("/production/sess1/events?after=0", headers=_H).json()
    assert [e["type"] for e in first["events"]] == ["meta", "agent", "done"]
    assert first["next"] == 4, "cursor counts the unparsable line too"
    assert first["done"] is True

    tail = client.get("/production/sess1/events?after=4", headers=_H).json()
    assert tail == {"events": [], "next": 4, "done": True}


def test_events_before_any_run_is_empty_not_500(tmp_path: Path) -> None:
    client, db, settings = _app(tmp_path)
    _seed(db, settings, tmp_path)
    body = client.get("/production/sess1/events?after=0", headers=_H).json()
    assert body == {"events": [], "next": 0, "done": False}


def test_events_unknown_session_404(tmp_path: Path) -> None:
    client, _db, _settings = _app(tmp_path)
    assert client.get("/production/nope/events", headers=_H).status_code == 404
```

**Implementer note:** derive the runs dir from `board_root_for(db, asset_id, session_id).parent / "runs"` — do NOT re-derive the project path by hand; fix the fixture helper to match the real layout (`board_root_for` is the authority; read it first).

- [ ] **Step 2: red** — `uv run pytest tests/test_production_events.py -q`.
- [ ] **Step 3: Implement** in `api/short_creator.py`:

```python
@router.get("/production/{session_id}/events")
def get_production_events(
    session_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("read"))],
    after: int = 0,
) -> dict[str, Any]:
    """The session's newest run log as a pollable event stream (spec 2026-08-03).

    Cursor = 0-based line index into the newest ``runs/*.ndjson``; unparsable lines are
    skipped but still advance the cursor, so a client can never loop on a bad line.
    ``done`` mirrors whether a terminal ``{"type": "done"}`` line exists in the file.
    """
    from ..short_creator.production_orchestrator import board_root_for

    db = _db(request)
    session = repos.get_production_session(db, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    try:
        runs_dir = board_root_for(db, str(session["asset_id"]), session_id).parent / "runs"
    except ValueError:
        return {"events": [], "next": max(0, after), "done": False}
    logs = sorted(runs_dir.glob("*.ndjson"), key=lambda p: p.stat().st_mtime) if runs_dir.is_dir() else []
    if not logs:
        return {"events": [], "next": max(0, after), "done": False}
    lines = logs[-1].read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(0, after)
    events: list[dict[str, Any]] = []
    done = False
    for line in lines:
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed.get("type") == "done":
            done = True
    for line in lines[start:]:
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return {"events": events, "next": len(lines), "done": done}
```

- [ ] **Step 4: green**, `uv run mypy` clean.
- [ ] **Step 5: Commit** — `git commit -m "feat(chat): pollable events reader over the production run log"`

---

### Task 4: Router core (`laura/chat/router.py`)

Scout pattern (`scout.py` is the template — read it first): injectable `runner: Callable[[str], str]`, JSON extraction, validation, ONE retry with the validation error appended, deterministic fallback = `reply` asking to rephrase.

**Files:**
- Create: `services/local-api/src/laura/chat/__init__.py` (empty), `services/local-api/src/laura/chat/router.py`
- Test: `services/local-api/tests/test_chat_router.py`

**Interfaces (Produces):**
```python
class RouterDecision(TypedDict):
    tool: str                    # one of TOOLS
    args: dict[str, Any]
    fallback: bool               # True when the deterministic fallback produced it

TOOLS: frozenset[str]  # {"reply","create_project","switch_project","propose_import",
                       #  "start_short","start_overview","follow_up","revert"}

compose_context(*, project: dict[str, Any] | None, running_jobs: int,
    messages: list[dict[str, Any]]) -> str      # last 20, cards as one-liners incl. approval status

run_router(config: AgentConfig, *, context: str, user_text: str,
    runner: Callable[[str], str] | None = None) -> RouterDecision
```

Required args per tool (validation): `reply.text: str` · `create_project.name: str` · `switch_project.ref: str` · `propose_import.urls: list[str]` (non-empty, each starts `http`) · `start_short.topic: str` (optional `target_seconds: int`, `format: str in {insta,x,linkedin}`) · `start_overview.topic: str` (optional `target_seconds: int`) · `follow_up.session_ref: str, text: str` · `revert.session_ref: str, artifact: str, version: int`.

- [ ] **Step 1: Failing tests** (all with injected fake runners — no LLM):

```python
"""Chat router: one turn, one tool call, never a crash (spec 2026-08-03).

Same seam design as scout.run_scout: runner is injectable, a bad reply gets exactly one
retry with the validation error appended, and a runner exception goes straight to the
deterministic fallback (a reply asking to rephrase) — the thread must never 500 on a turn.
"""
from __future__ import annotations

import json

import pytest

from laura.chat.router import TOOLS, compose_context, run_router
from laura.short_creator.providers import AgentConfig


def _config() -> AgentConfig:
    return AgentConfig(provider="openai-compat", model="test", api_key="k")  # adjust to real ctor


def test_valid_tool_call_passes_through() -> None:
    reply = json.dumps({"tool": "start_short", "args": {"topic": "Automatisierung"}})
    decision = run_router(_config(), context="", user_text="bau mir was", runner=lambda _t: reply)
    assert decision == {"tool": "start_short", "args": {"topic": "Automatisierung"}, "fallback": False}


def test_unknown_tool_gets_one_retry_then_fallback() -> None:
    calls: list[str] = []

    def runner(task: str) -> str:
        calls.append(task)
        return json.dumps({"tool": "explode", "args": {}})

    decision = run_router(_config(), context="", user_text="x", runner=runner)
    assert len(calls) == 2, "exactly one retry"
    assert "explode" in calls[1], "the retry names the validation error"
    assert decision["tool"] == "reply" and decision["fallback"] is True


def test_missing_required_arg_is_a_validation_error() -> None:
    replies = iter([
        json.dumps({"tool": "propose_import", "args": {"urls": []}}),
        json.dumps({"tool": "propose_import", "args": {"urls": ["https://x/a.mp4"]}}),
    ])
    decision = run_router(_config(), context="", user_text="x", runner=lambda _t: next(replies))
    assert decision["tool"] == "propose_import" and decision["fallback"] is False


def test_runner_exception_goes_straight_to_fallback() -> None:
    def runner(_task: str) -> str:
        raise TimeoutError("model down")

    decision = run_router(_config(), context="", user_text="x", runner=runner)
    assert decision["tool"] == "reply" and decision["fallback"] is True
    assert decision["args"]["text"], "the fallback reply carries readable text"


def test_compose_context_compacts_cards_and_caps_at_20() -> None:
    messages = [
        {"role": "user", "kind": "text", "content": {"text": f"m{i}"}} for i in range(25)
    ] + [
        {"role": "assistant", "kind": "approval_request",
         "content": {"action_type": "import_urls", "status": "pending",
                     "payload": {"urls": ["https://x/a"]}}},
        {"role": "assistant", "kind": "action",
         "content": {"tool": "start_short", "outcome": "done",
                     "refs": {"session_id": "s9"}, "args": {}}},
    ]
    ctx = compose_context(project={"name": "Drive-Test", "id": "p1"}, running_jobs=1,
                          messages=messages)
    assert "m4" not in ctx and "m24" in ctx, "only the last 20 messages"
    assert "import_urls" in ctx and "pending" in ctx
    assert "s9" in ctx, "action refs survive compaction (follow_up needs them)"
    assert "Drive-Test" in ctx


def test_every_tool_is_reachable() -> None:
    assert TOOLS == frozenset({
        "reply", "create_project", "switch_project", "propose_import",
        "start_short", "start_overview", "follow_up", "revert",
    })
```

- [ ] **Step 2: red.** Check `AgentConfig`'s real constructor in `laura/short_creator/providers.py` first and fix `_config()` accordingly.
- [ ] **Step 3: Implement** `router.py`: `_SYSTEM_PROMPT` (English, describes Laura + tools + "exactly ONE JSON object `{tool, args}`, ask via reply when unsure, never invent project names/URLs"); `_parse` = first `{` to last `}` + `json.loads` (pattern `production_tools._parse_review_reply`); `_validate(decision) -> str | None` per the table above; `run_router` = compose task (system + context + user text) → `_safe_call` → validate → one retry with error appended → fallback `{"tool": "reply", "args": {"text": "Ich bin mir nicht sicher, was ich tun soll — formulier es bitte einmal anders (z. B. 'bau mir einen 60s-Short über …')."}, "fallback": True}`. `_default_runner(config)` mirrors `scout._default_runner` (AssistantAgent, `asyncio.wait_for`, timeout 30 s) — lazy autogen import.
- [ ] **Step 4: green**, mypy clean.
- [ ] **Step 5: Commit** — `git commit -m "feat(chat): one-turn router with injectable runner and deterministic fallback"`

---

### Task 5: Executor (`laura/chat/executor.py`)

Maps a validated `RouterDecision` to conversation messages + machinery calls. Never raises: every failure becomes an assistant `text` message.

**Files:**
- Create: `services/local-api/src/laura/chat/executor.py`
- Test: `services/local-api/tests/test_chat_executor.py`

**Interfaces (Produces):**
```python
execute_decision(db, settings, *, conversation_id: str, decision: RouterDecision,
    now_utc: str) -> list[dict[str, Any]]   # the messages appended (content parsed), in order
execute_import_approval(db, *, message_id: str, now_utc: str) -> list[dict[str, Any]]
    # called ONLY by the approvals endpoint; raises HTTPException(409) when not pending
```

**Behavior table (each row = one test):**

| decision.tool | effect |
|---|---|
| `reply` | append assistant `text` |
| `create_project` | mirror `api/projects.create_project` core: `new_id()`, `project-{pid}` dir under `settings.workspace_root`, `repos.create_project(...)` with 30/1 non-drop default rate; set conversation `active_project_id`; assistant text „Projekt ‚X' angelegt und aktiviert." |
| `switch_project` | resolve `ref` against `repos.list_projects(db)` by case-insensitive exact name OR id prefix; exactly one hit → set active + confirm; zero/many → assistant text asking (names the candidates) |
| `propose_import` | no active project → asking text; else append `approval_request` (`status: "pending"`, payload `{urls, project_id}`) |
| `start_short` / `start_overview` | no active project → asking text; else call `run_project_auto_short` / `run_project_auto_overview` (Task 2); success → assistant text (scout rationale / overview rationale + warnings) **and** `action` message with refs (`session_id`+`job_id` / `export_id`+`job_id`+`sequence_id`), `outcome: "running"`; `HTTPException` → assistant text with the `detail`'s `reason` (or `str(detail)`) |
| `follow_up` | resolve `session_ref`: exact `session_id` match against the thread's `action` messages, else the NEWEST action with a `session_id` when ref is `"last"`/not found → if none, asking text; else `run_production_follow_up` → action message (`outcome: "running"`, same session_id, new job_id) |
| `revert` | resolve session like follow_up; `run_production_revert` → assistant text („zurückgedreht auf v{n}") ; HTTPException 409 (run in progress) → its detail as text |

`execute_import_approval`: load message (404 unknown), require `kind == "approval_request"` and `status == "pending"` (else 409 with the current status); set `approved`; for each URL call `_enqueue_url_fetch(db, project_id, url)` (import from `laura.api.assets`); append an `action` message `{tool: "import_urls", refs: {asset_ids, job_ids}, outcome: "running"}`; set card `executed` with `result: {asset_ids}`; return `[updated card, action message]`. On reject (handled in the endpoint by a `decision` param — see Task 6) only the status flips.

- [ ] **Step 1: Failing tests** — one per table row plus the approval trio (approve executes + appends; reject only flips; second decide → 409). Monkeypatch the Task-2 service functions (`laura.chat.executor.run_project_auto_short = fake`) — import them INTO executor by name so tests can patch `laura.chat.executor.<name>`. Seed projects/conversations with the Task-1 repos. Use `HTTPException(422, detail={"reason": "no material found for topic", ...})` in a fake to pin the honest-passthrough text.
- [ ] **Step 2: red.**
- [ ] **Step 3: Implement.** Every appended message goes through `repos.append_conversation_message` + `repos.touch_conversation`. Import for approval: `from ..api.assets import _enqueue_url_fetch` (documented exception to the private-import rule, mirroring `discovery`'s use of `context._scene_src_ranges`).
- [ ] **Step 4: green**, mypy clean.
- [ ] **Step 5: Commit** — `git commit -m "feat(chat): executor maps router decisions onto the existing machinery"`

---

### Task 6: Chat endpoints (`api/chat.py`) + registration

**Files:**
- Create: `services/local-api/src/laura/api/chat.py`
- Modify: `services/local-api/src/laura/main.py` (import + `app.include_router(chat.router)`)
- Test: `services/local-api/tests/test_chat_api.py`

**Interfaces (Produces):** routes per spec table. Request models: `ChatMessageIn(text: str, 1..4000)`, `ApprovalDecisionIn(decision: Literal["approve","reject"])`. The message turn:

```python
@router.post("/conversations/{conversation_id}/message", status_code=status.HTTP_202_ACCEPTED)
def post_message(...) -> dict[str, Any]:
    # 404 unknown conversation. Persist user text message (title := first 60 chars when
    # the conversation has none). Build context via compose_context (project row loaded from
    # active_project_id, running_jobs = count of queued/running jobs), run_router with
    # runner=getattr(request.app.state, "chat_runner", None)  ← test seam,
    # then execute_decision. Returns {"messages": [user_msg, *appended]}.
```

`app.state.chat_runner` is the injectable seam: tests set it to a fake `Callable[[str], str]`; production leaves it unset (router builds the real one).

- [ ] **Step 1: Failing tests** — TestClient app like `test_production_events.py`; set `app.state.chat_runner = lambda task: json.dumps({...})` per test:
  - CRUD: create → list → get (messages in order) → delete → 404 afterwards
  - message turn: fake runner returns `reply` → response carries user+assistant messages; title set to first 60 chars
  - message turn with `propose_import` → approval card in response with `status: "pending"`
  - approvals: approve → 200, card `executed`, action message exists (monkeypatch `laura.chat.executor._enqueue_url_fetch` to a fake returning `("a1", "j1")`); reject → card `rejected`, no action; approve again → 409
  - auth: no token → 401/403 (match the repo's convention — read one existing auth test first)
- [ ] **Step 2: red.**
- [ ] **Step 3: Implement** `api/chat.py` (thin: parse/auth/404s, delegate to executor + repos), register in `main.py`.
- [ ] **Step 4: green**; run `uv run pytest -q` once here (first task where all layers meet); mypy + ruff clean.
- [ ] **Step 5: Commit** — `git commit -m "feat(chat): conversations API — message turn, approvals, CRUD"`

---

### Task 7: Desktop client (`api.ts`)

**Files:**
- Modify: `apps/desktop/src/api.ts`
- Test: `apps/desktop/src/api.chat.test.ts` (new; mockFetch pattern from `api.production.test.ts`)

**Interfaces (Produces):**
```typescript
export interface ConversationSummary { id: string; title: string; updated_at: string }
export type ChatMessageKind = "text" | "approval_request" | "action";
export interface ChatMessage {
  id: string; conversation_id: string; seq: number;
  role: "user" | "assistant"; kind: ChatMessageKind;
  content: Record<string, unknown>; created_at: string;
}
export interface ChatTurnResult { messages: ChatMessage[] }
export interface ProductionEvents { events: AgentEvent[]; next: number; done: boolean }

createConversation(): Promise<{ id: string }>
listConversations(): Promise<ConversationSummary[]>
getConversation(id: string): Promise<{ id: string; title: string; active_project_id: string | null; messages: ChatMessage[] }>
deleteConversation(id: string): Promise<void>
sendChatMessage(id: string, text: string): Promise<ChatTurnResult>
decideApproval(id: string, messageId: string, decision: "approve" | "reject"): Promise<ChatTurnResult>
getProductionEvents(sessionId: string, after: number): Promise<ProductionEvents>
getExport(exportId: string): Promise<{ id: string; status: string; path: string | null; size_bytes: number | null }>
```

- [ ] **Step 1: Failing tests** — one per method: URL, method, token header, body, parsed response (mirror `api.production.test.ts` style exactly; `deleteConversation` uses a raw fetch + ok-check like `cancelImport`).
- [ ] **Step 2: red** → **Step 3: implement** (all via `this.request` except delete) → **Step 4: green + `pnpm typecheck`**.
- [ ] **Step 5: Commit** — `git commit -m "feat(desktop): chat client methods + typed contracts"`

---

### Task 8: `GET /exports/{export_id}` + laura-media export lane

**Files:**
- Modify: `services/local-api/src/laura/api/timelines.py` (single-export endpoint next to `list_project_exports`)
- Modify: `apps/desktop/src/main.ts` (protocol branch + resolver)
- Test: `services/local-api/tests/test_export_get.py`; main.ts is untested infrastructure (no test harness exists) — **manual check listed in Task 12**.

- [ ] **Step 1: Failing backend test** — create export via `repos.create_export` + `set_export_done`, `GET /exports/{id}` → 200 with `status`/`path`; unknown id → 404.
- [ ] **Step 2: red → implement:**

```python
@router.get("/exports/{export_id}", response_model=RenderExportOut)
def get_export(export_id: str, request: Request) -> RenderExportOut:
    """One export row — the laura-media export lane resolves file paths through this."""
    e = repos.get_export(_db(request), export_id)
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "export not found")
    opts: dict[str, object] = e.get("options") or {}
    return RenderExportOut(
        **{k: v for k, v in e.items() if k != "options"},
        quality_status=opts.get("quality_status"),  # type: ignore[arg-type]
        quality_verified=opts.get("quality_verified"),  # type: ignore[arg-type]
    )
```

- [ ] **Step 3: green.**
- [ ] **Step 4: main.ts** — in the `protocol.handle("laura-media", ...)` handler the pathname currently splits into `[assetId, kind]`. Add the lane BEFORE the asset resolve: when `assetId === "export"`, resolve via a new `resolveExportPath(exportId)` (same shape as `resolveMediaPath`: cache map, `net.fetch(`${serviceInfo.baseUrl}/exports/${exportId}`)`, return `path` when `status === "ready"`, else null → 404 response). Everything downstream (stat, Range, streaming) is shared — only the path resolution branches.
- [ ] **Step 5:** `pnpm typecheck` clean.
- [ ] **Step 6: Commit** — `git commit -m "feat(desktop): laura-media export lane + GET /exports/{id}"`

---

### Task 9: Thread cards (`ApprovalCard`, `ActionCard`) + `EventLine` export

**Files:**
- Modify: `apps/desktop/src/components/ChatPanel.tsx` (add `export` to the existing `EventLine` function — no other change)
- Create: `apps/desktop/src/components/chat/ApprovalCard.tsx`, `apps/desktop/src/components/chat/ActionCard.tsx`
- Test: `apps/desktop/src/components/chat/ApprovalCard.test.tsx`, `ActionCard.test.tsx`

**Interfaces (Produces):**
```typescript
ApprovalCard({ message, onDecide }: {
  message: ChatMessage;                       // kind approval_request
  onDecide: (decision: "approve" | "reject") => void;
}): ReactElement
ActionCard({ message, client, onFocus }: {
  message: ChatMessage;                       // kind action
  client: LauraClient;
  onFocus?: () => void;                       // preview focus (Task 11)
}): ReactElement
```

**ApprovalCard:** renders the URL list + „Freigeben" / „Ablehnen" buttons while `content.status === "pending"`; any other status renders a read-only line („✓ freigegeben & ausgeführt" / „✗ abgelehnt") — the persisted decision, no buttons. Tests: pending shows both buttons and clicking calls `onDecide`; `executed` shows no buttons.

**ActionCard:** switch on `content.tool`:
- production tools (`start_short`, `follow_up`): while `outcome === "running"` poll `getProductionEvents(refs.session_id, cursor)` every 2500 ms (fake timers in tests), render the last 5 events via the re-exported `EventLine` with an „alle anzeigen" expander; when `done` arrives, poll `getProductionStatus` once and render the result line (Export-Id + `target_ratio` percent + QA verdict when present) with „▶ ansehen" calling `onFocus`
- `start_overview` / `import_urls`: poll `getJob(refs.job_id)` (existing `useJobStatus`), render „⚙ läuft" → „✓ fertig" / „✗ fehlgeschlagen: {grund}"
Tests: running production card renders event lines from a mocked events response; done shows the ratio; failed import shows the reason; „▶ ansehen" fires `onFocus`.

- [ ] Steps: failing tests → red → implement → green → commit `git commit -m "feat(desktop): chat thread cards — approval + action with live narration"`

---

### Task 10: `ConversationList`, `ChatComposer`, `ChatThread`

**Files:**
- Create: `apps/desktop/src/components/chat/ConversationList.tsx`, `ChatComposer.tsx`, `ChatThread.tsx`
- Test: one `.test.tsx` per component

**Interfaces (Produces):**
```typescript
ConversationList({ items, activeId, onSelect, onNew, onDelete }): ReactElement
  // items: ConversationSummary[]; „Neuer Chat" button; delete per row with confirm
ChatComposer({ disabled, onSend }: { disabled: boolean; onSend: (text: string) => void }): ReactElement
  // textarea + „Senden", Enter sends, Shift+Enter newline, empty text never fires
ChatThread({ messages, client, onDecide, onFocusAction }): ReactElement
  // renders by kind: text bubbles (role-styled), ApprovalCard, ActionCard; auto-scrolls to tail
```

Tests: list renders + callbacks fire; composer blocks empty/disabled and sends trimmed text; thread renders one of each kind (fixtures from Task 7 types) and routes callbacks.

- [ ] Steps: failing tests → red → implement (German copy: „Neuer Chat", „Senden", „Noch keine Unterhaltungen") → green → commit `git commit -m "feat(desktop): chat list, composer and thread"`

---

### Task 11: `ChatPreview`

**Files:**
- Create: `apps/desktop/src/components/chat/ChatPreview.tsx`
- Test: `apps/desktop/src/components/chat/ChatPreview.test.tsx`

**Interfaces (Produces):**
```typescript
export type PreviewTarget =
  | { kind: "none" }
  | { kind: "contact_sheet"; sessionId: string; version: number }
  | { kind: "export"; exportId: string };
ChatPreview({ target, client }: { target: PreviewTarget; client: LauraClient }): ReactElement
```

- `none` → „Noch nichts zu zeigen — bau etwas." · `contact_sheet` → `client.contactSheetUrl(sessionId)` img (revoke on change, version busts) · `export` → `<video controls src={`laura-media://export/${exportId}`}>` (the Task-8 lane; in vitest/jsdom only the `src` attribute is asserted, no playback).
Tests: all three branches; sheet failure shows „Bogen konnte nicht geladen werden".

- [ ] Steps: failing tests → red → implement → green → commit `git commit -m "feat(desktop): chat preview pane (sheet + export player)"`

---

### Task 12: `ChatStage` assembly + App wiring (LAST) + full gates

**Files:**
- Create: `apps/desktop/src/components/chat/ChatStage.tsx` + `ChatStage.test.tsx`
- Modify: `apps/desktop/src/components/NavRail.tsx` (extend `Stage` union with `"chat"`, first nav item „💬 Chat")
- Modify: `apps/desktop/src/App.tsx` (`useState<Stage>("chat")` as default; `{stage === "chat" && client && <ChatStage client={client} />}`)

**ChatStage owns:** conversation list state (load on mount, create/delete), active conversation + messages (reload after `sendChatMessage`/`decideApproval` responses by merging returned messages), composer disabled while a turn is in flight, preview focus state (`PreviewTarget`, default = derived from the newest action message: production done → its export; running production → its contact sheet when the board reports one; overview → its export). Three-column Tailwind grid (`grid-cols-[220px_1fr_380px]`, tokens like the rest of the app).

- [ ] **Step 1: Failing ChatStage tests** — mocked client: mount loads list; „Neuer Chat" creates + activates; sending renders the returned turn; approval decision calls through and updates the card; preview switches when an ActionCard's „▶ ansehen" fires. Plus one `App.test.tsx` addition: default stage renders the chat nav item as active.
- [ ] **Step 2: red → implement → green.**
- [ ] **Step 3: Full gates** — `cd services/local-api && uv run pytest -q && uv run mypy && uv run ruff check src tests`; `cd apps/desktop && pnpm test -- --run && pnpm typecheck`.
- [ ] **Step 4: Manual live check** (laura-media lane is untestable headless): start the app on a workspace with a ready export, open Chat, „▶ ansehen" on a done card must play — note the stale-backend rule: verify the serving PID's StartTime postdates the build (memory `stale-backend-holds-port`).
- [ ] **Step 5: Commit** — `git commit -m "feat(desktop): chat stage as the default view"`

---

## Self-review (done while writing)

- **Spec coverage:** conversation layer (T1), router (T4), executor+tools (T5), endpoints+approval enforcement (T6), events narration (T3+T9), client (T7), export lane incl. missing `GET /exports/{id}` (T8), stage+default (T12), German copy + honesty passthrough pinned in tests (T5/T9). Spec's "Bewusst NICHT in v1" items are absent here by design.
- **Type consistency:** `RouterDecision` (T4) consumed by `execute_decision` (T5); `ChatMessage`/`ChatTurnResult` (T7) consumed by T9-T12; `PreviewTarget` (T11) consumed by T12; service functions (T2) consumed by T5.
- **Known judgment calls for implementers:** `_config()` in T4 tests and the runs-dir fixture in T3 must be adjusted to the real constructors/layout — both marked inline with "read X first".
