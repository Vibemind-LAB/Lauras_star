# AI Persona Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the AI Persona Foundation: runtime registry, container-first runtime management, setup assistant primitives, persona kit, and runtime-based routing for existing AI jobs.

**Architecture:** Laura remains model-free and orchestrates AI effects through registered runtimes. Runtimes can be `stub`, `external_http`, or `container`; container runtimes are managed through a small Docker adapter and exposed through FastAPI + Desktop UI. Persona profiles bind consent, reference assets, allowed effects, and preferred runtimes so subsequent pipeline flows can reuse them.

**Tech Stack:** FastAPI, SQLite/Postgres-compatible SQL migrations, existing DB repos, DB job runner, stdlib subprocess/urllib for Docker and HTTP checks, React + TypeScript strict + Vitest.

## Global Constraints

- Container-first, not container-only: support `stub`, `external_http`, and `container`.
- Laura core must not import heavy model stacks such as Torch/CUDA/LivePortrait/Wav2Lip/MuseTalk.
- Timeline state remains integer frames and audio remains samples; external runtimes may use seconds internally only behind the boundary.
- Every synthetic output keeps existing `synthetic=true`, `ai_effect`, and provenance behavior.
- No real model weights in Git and no silent download of proprietary/gated model weights.
- Backend must start when Docker, GPU, and model containers are absent.
- TypeScript remains strict; no `any`.
- Python remains typed and `ruff`/`mypy` clean for touched files.

---

## File Structure

Backend:

- Create `services/local-api/src/laura/db/migrations/0023_ai_persona_foundation.sql`
  - Adds `ai_runtimes`, `ai_personas`, and `ai_runtime_events`.
- Modify `services/local-api/src/laura/db/repos.py`
  - Adds repository helpers for runtimes, personas, and runtime events.
- Create `services/local-api/src/laura/ai/runtime_types.py`
  - Small dataclasses/enums and JSON helpers used by services and tests.
- Create `services/local-api/src/laura/ai/runtime_manager.py`
  - Health/capability refresh, runtime resolution, safe HTTP checks.
- Create `services/local-api/src/laura/ai/docker_runtime.py`
  - Thin subprocess-based Docker adapter, dependency-free and mockable.
- Create `services/local-api/src/laura/api/persona.py`
  - Runtime and persona API routes.
- Modify `services/local-api/src/laura/api/models.py`
  - Pydantic models for runtimes, personas, setup/start/stop responses.
- Modify `services/local-api/src/laura/main.py`
  - Mount persona runtime router.
- Modify `services/local-api/src/laura/ai/handlers.py`
  - Resolve `runtime_id` to legacy `backend` for `ai.reenact`, `ai.lipsync`, and `ai.voiceover`.
- Modify `services/local-api/src/laura/api/reenact.py`, `lipsync.py`, `voiceover.py`
  - Accept optional `runtime_id`, validate it can handle the requested effect.

Backend tests:

- Create `services/local-api/tests/test_ai_runtime_repos.py`
- Create `services/local-api/tests/test_ai_runtime_api.py`
- Create `services/local-api/tests/test_ai_runtime_manager.py`
- Create `services/local-api/tests/test_ai_personas.py`
- Extend `services/local-api/tests/test_reenact_job.py`, `test_lipsync_api.py`, `test_voiceover.py`

Desktop:

- Modify `apps/desktop/src/api.ts`
  - Typed client methods and interfaces for runtimes/personas.
- Create `apps/desktop/src/components/RuntimeStatusPanel.tsx`
  - Runtime list, health refresh, start/stop, logs/status.
- Create `apps/desktop/src/components/RuntimeSetupPanel.tsx`
  - Guided setup form for `stub`, `external_http`, and `container` runtimes.
- Create `apps/desktop/src/components/PersonaKitPanel.tsx`
  - Create/list personas with consent and preferred runtime choices.
- Modify `apps/desktop/src/components/AssembleView.tsx`
  - Add runtime/persona foundation panels to the Tools rail.

Desktop tests:

- Extend `apps/desktop/src/api.test.ts`
- Create `apps/desktop/src/components/RuntimeStatusPanel.test.tsx`
- Create `apps/desktop/src/components/RuntimeSetupPanel.test.tsx`
- Create `apps/desktop/src/components/PersonaKitPanel.test.tsx`
- Extend `apps/desktop/src/components/AssembleView.test.tsx`

---

### Task 1: AI Runtime + Persona Schema and Repos

**Files:**
- Create: `services/local-api/src/laura/db/migrations/0023_ai_persona_foundation.sql`
- Modify: `services/local-api/src/laura/db/repos.py`
- Test: `services/local-api/tests/test_ai_runtime_repos.py`

**Interfaces:**
- Consumes: existing `Database`, `new_id()`, `utcnow_iso()`.
- Produces:
  - `create_ai_runtime(db, *, kind: str, effect: str, display_name: str, base_url: str | None = None, container_image: str | None = None, container_name: str | None = None, port: int | None = None, workspace_mount: str | None = None, model_mount: str | None = None, requires_gpu: bool = False, enabled: bool = True, license_status: str = "unknown") -> dict[str, Any]`
  - `get_ai_runtime(db, runtime_id: str) -> dict[str, Any] | None`
  - `list_ai_runtimes(db, *, effect: str | None = None) -> list[dict[str, Any]]`
  - `update_ai_runtime_status(db, runtime_id: str, *, status: dict[str, Any], capabilities: dict[str, Any] | None = None) -> bool`
  - `create_ai_runtime_event(db, *, runtime_id: str, event_type: str, level: str, message: str, payload: dict[str, Any] | None = None) -> dict[str, Any]`
  - `list_ai_runtime_events(db, runtime_id: str, *, limit: int = 100) -> list[dict[str, Any]]`
  - `create_ai_persona(db, *, name: str, consent_id: str, project_id: str | None = None, face_reference_asset_id: str | None = None, voice_reference_asset_id: str | None = None, style: dict[str, Any] | None = None, allowed_effects: list[str] | None = None, preferred_runtimes: dict[str, str] | None = None) -> dict[str, Any]`
  - `get_ai_persona(db, persona_id: str) -> dict[str, Any] | None`
  - `list_ai_personas(db, *, project_id: str | None = None) -> list[dict[str, Any]]`

- [ ] **Step 1: Write failing repo tests**

Create `services/local-api/tests/test_ai_runtime_repos.py`:

```python
from __future__ import annotations

from laura.db import repos
from laura.db.database import create_database
from laura.config import Settings


def _db(tmp_path):
    db = create_database(Settings(workspace_root=tmp_path, start_runner=False))
    db.migrate()
    return db


def test_ai_runtime_round_trips_json_fields(tmp_path):
    db = _db(tmp_path)
    rt = repos.create_ai_runtime(
        db,
        kind="container",
        effect="lipsync",
        display_name="MuseTalk",
        container_image="laura-runtime-lipsync:local",
        container_name="laura-lipsync",
        port=8901,
        workspace_mount="workspace/ai-runtime/io",
        model_mount="E:/LauraModels/lipsync",
        requires_gpu=True,
        license_status="accepted",
    )

    assert rt["kind"] == "container"
    assert rt["effect"] == "lipsync"
    assert rt["requires_gpu"] is True
    assert rt["enabled"] is True

    ok = repos.update_ai_runtime_status(
        db,
        rt["id"],
        status={"state": "ready", "ready": True},
        capabilities={"effects": ["lipsync"], "metrics": ["sync_score"]},
    )
    assert ok is True

    loaded = repos.get_ai_runtime(db, rt["id"])
    assert loaded is not None
    assert loaded["status"] == {"state": "ready", "ready": True}
    assert loaded["capabilities"] == {"effects": ["lipsync"], "metrics": ["sync_score"]}
    assert repos.list_ai_runtimes(db, effect="lipsync")[0]["id"] == rt["id"]


def test_runtime_events_are_ordered_newest_first(tmp_path):
    db = _db(tmp_path)
    rt = repos.create_ai_runtime(db, kind="stub", effect="voice", display_name="Stub Voice")
    repos.create_ai_runtime_event(
        db,
        runtime_id=rt["id"],
        event_type="health",
        level="info",
        message="ready",
        payload={"ready": True},
    )
    repos.create_ai_runtime_event(
        db,
        runtime_id=rt["id"],
        event_type="error",
        level="error",
        message="boom",
        payload={"code": "test"},
    )

    events = repos.list_ai_runtime_events(db, rt["id"])
    assert [event["message"] for event in events] == ["boom", "ready"]
    assert events[0]["payload"] == {"code": "test"}


def test_ai_persona_round_trips_policy_fields(tmp_path):
    db = _db(tmp_path)
    project = repos.create_project(
        db,
        name="P",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root=str(tmp_path),
    )
    consent = repos.create_consent_record(
        db,
        project_id=project["id"],
        subject_label="Laura Persona",
    )
    persona = repos.create_ai_persona(
        db,
        project_id=project["id"],
        name="Laura Persona",
        consent_id=consent["id"],
        allowed_effects=["voice", "lipsync"],
        preferred_runtimes={"voice": "rt-voice", "lipsync": "rt-lipsync"},
        style={"tone": "clear"},
    )

    loaded = repos.get_ai_persona(db, persona["id"])
    assert loaded is not None
    assert loaded["allowed_effects"] == ["voice", "lipsync"]
    assert loaded["preferred_runtimes"] == {"voice": "rt-voice", "lipsync": "rt-lipsync"}
    assert loaded["style"] == {"tone": "clear"}
    assert repos.list_ai_personas(db, project_id=project["id"])[0]["id"] == persona["id"]
```

- [ ] **Step 2: Run RED**

Run:

```powershell
uv run pytest tests/test_ai_runtime_repos.py -q
```

Expected: FAIL with missing repo functions or missing tables.

- [ ] **Step 3: Add migration**

Create `services/local-api/src/laura/db/migrations/0023_ai_persona_foundation.sql`:

```sql
CREATE TABLE ai_runtimes (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    effect TEXT NOT NULL,
    display_name TEXT NOT NULL,
    base_url TEXT,
    container_image TEXT,
    container_name TEXT,
    port INTEGER,
    workspace_mount TEXT,
    model_mount TEXT,
    requires_gpu INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    license_status TEXT NOT NULL DEFAULT 'unknown',
    status_cache_json TEXT NOT NULL DEFAULT '{}',
    capabilities_json TEXT NOT NULL DEFAULT '{}',
    last_health_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_ai_runtimes_effect ON ai_runtimes(effect);
CREATE INDEX idx_ai_runtimes_kind ON ai_runtimes(kind);

CREATE TABLE ai_runtime_events (
    id TEXT PRIMARY KEY,
    runtime_id TEXT NOT NULL REFERENCES ai_runtimes(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX idx_ai_runtime_events_runtime_created
    ON ai_runtime_events(runtime_id, created_at DESC);

CREATE TABLE ai_personas (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    consent_id TEXT NOT NULL REFERENCES consent_records(id),
    face_reference_asset_id TEXT REFERENCES media_assets(id),
    voice_reference_asset_id TEXT REFERENCES media_assets(id),
    style_json TEXT NOT NULL DEFAULT '{}',
    allowed_effects_json TEXT NOT NULL DEFAULT '[]',
    preferred_runtimes_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_ai_personas_project ON ai_personas(project_id);
```

- [ ] **Step 4: Add repo helpers**

Append focused helpers near the consent/demo section in `services/local-api/src/laura/db/repos.py`:

```python
def _json_obj(value: dict[str, Any] | None = None) -> str:
    return json.dumps(value or {})


def _json_list(value: list[str] | None = None) -> str:
    return json.dumps(value or [])


def _decode_ai_runtime(row: dict[str, Any]) -> dict[str, Any]:
    row["requires_gpu"] = bool(row.get("requires_gpu"))
    row["enabled"] = bool(row.get("enabled"))
    row["status"] = json.loads(row.pop("status_cache_json") or "{}")
    row["capabilities"] = json.loads(row.pop("capabilities_json") or "{}")
    return row


def create_ai_runtime(
    db: Database,
    *,
    kind: str,
    effect: str,
    display_name: str,
    base_url: str | None = None,
    container_image: str | None = None,
    container_name: str | None = None,
    port: int | None = None,
    workspace_mount: str | None = None,
    model_mount: str | None = None,
    requires_gpu: bool = False,
    enabled: bool = True,
    license_status: str = "unknown",
) -> dict[str, Any]:
    runtime_id = new_id()
    now = utcnow_iso()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO ai_runtimes "
            "(id, kind, effect, display_name, base_url, container_image, container_name, "
            "port, workspace_mount, model_mount, requires_gpu, enabled, license_status, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                runtime_id,
                kind,
                effect,
                display_name,
                base_url,
                container_image,
                container_name,
                port,
                workspace_mount,
                model_mount,
                int(requires_gpu),
                int(enabled),
                license_status,
                now,
                now,
            ),
        )
    runtime = get_ai_runtime(db, runtime_id)
    assert runtime is not None
    return runtime


def get_ai_runtime(db: Database, runtime_id: str) -> dict[str, Any] | None:
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM ai_runtimes WHERE id=?", (runtime_id,)).fetchone()
    return _decode_ai_runtime(dict(row)) if row is not None else None


def list_ai_runtimes(db: Database, *, effect: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM ai_runtimes"
    params: list[Any] = []
    if effect is not None:
        sql += " WHERE effect=?"
        params.append(effect)
    sql += " ORDER BY effect, display_name"
    with db.connection() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [_decode_ai_runtime(dict(row)) for row in rows]


def update_ai_runtime_status(
    db: Database,
    runtime_id: str,
    *,
    status: dict[str, Any],
    capabilities: dict[str, Any] | None = None,
) -> bool:
    now = utcnow_iso()
    with db.transaction() as conn:
        if capabilities is None:
            cur = conn.execute(
                "UPDATE ai_runtimes SET status_cache_json=?, last_health_at=?, updated_at=? "
                "WHERE id=?",
                (json.dumps(status), now, now, runtime_id),
            )
        else:
            cur = conn.execute(
                "UPDATE ai_runtimes SET status_cache_json=?, capabilities_json=?, "
                "last_health_at=?, updated_at=? WHERE id=?",
                (json.dumps(status), json.dumps(capabilities), now, now, runtime_id),
            )
        return cur.rowcount > 0


def create_ai_runtime_event(
    db: Database,
    *,
    runtime_id: str,
    event_type: str,
    level: str,
    message: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event_id = new_id()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO ai_runtime_events "
            "(id, runtime_id, event_type, level, message, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event_id, runtime_id, event_type, level, message, json.dumps(payload or {}), utcnow_iso()),
        )
        row = conn.execute("SELECT * FROM ai_runtime_events WHERE id=?", (event_id,)).fetchone()
    event = dict(row)
    event["payload"] = json.loads(event.pop("payload_json") or "{}")
    return event


def list_ai_runtime_events(
    db: Database, runtime_id: str, *, limit: int = 100
) -> list[dict[str, Any]]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM ai_runtime_events WHERE runtime_id=? "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (runtime_id, limit),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        event = dict(row)
        event["payload"] = json.loads(event.pop("payload_json") or "{}")
        out.append(event)
    return out


def _decode_ai_persona(row: dict[str, Any]) -> dict[str, Any]:
    row["style"] = json.loads(row.pop("style_json") or "{}")
    row["allowed_effects"] = json.loads(row.pop("allowed_effects_json") or "[]")
    row["preferred_runtimes"] = json.loads(row.pop("preferred_runtimes_json") or "{}")
    return row


def create_ai_persona(
    db: Database,
    *,
    name: str,
    consent_id: str,
    project_id: str | None = None,
    face_reference_asset_id: str | None = None,
    voice_reference_asset_id: str | None = None,
    style: dict[str, Any] | None = None,
    allowed_effects: list[str] | None = None,
    preferred_runtimes: dict[str, str] | None = None,
) -> dict[str, Any]:
    persona_id = new_id()
    now = utcnow_iso()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO ai_personas "
            "(id, project_id, name, consent_id, face_reference_asset_id, "
            "voice_reference_asset_id, style_json, allowed_effects_json, "
            "preferred_runtimes_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                persona_id,
                project_id,
                name,
                consent_id,
                face_reference_asset_id,
                voice_reference_asset_id,
                _json_obj(style),
                _json_list(allowed_effects),
                _json_obj(preferred_runtimes),
                now,
                now,
            ),
        )
    persona = get_ai_persona(db, persona_id)
    assert persona is not None
    return persona


def get_ai_persona(db: Database, persona_id: str) -> dict[str, Any] | None:
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM ai_personas WHERE id=?", (persona_id,)).fetchone()
    return _decode_ai_persona(dict(row)) if row is not None else None


def list_ai_personas(db: Database, *, project_id: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM ai_personas"
    params: list[Any] = []
    if project_id is not None:
        sql += " WHERE project_id=? OR project_id IS NULL"
        params.append(project_id)
    sql += " ORDER BY created_at DESC"
    with db.connection() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [_decode_ai_persona(dict(row)) for row in rows]
```

- [ ] **Step 5: Run GREEN**

Run:

```powershell
uv run pytest tests/test_ai_runtime_repos.py -q
uv run ruff check src/laura/db/repos.py tests/test_ai_runtime_repos.py
uv run mypy src/laura/db/repos.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add services/local-api/src/laura/db/migrations/0023_ai_persona_foundation.sql services/local-api/src/laura/db/repos.py services/local-api/tests/test_ai_runtime_repos.py
git commit -m "feat: add AI runtime and persona storage"
```

### Task 2: Runtime Manager and Docker Adapter

**Files:**
- Create: `services/local-api/src/laura/ai/runtime_types.py`
- Create: `services/local-api/src/laura/ai/docker_runtime.py`
- Create: `services/local-api/src/laura/ai/runtime_manager.py`
- Test: `services/local-api/tests/test_ai_runtime_manager.py`

**Interfaces:**
- Consumes: repo helpers from Task 1.
- Produces:
  - `RuntimeHealth`: dataclass with `state: str`, `ready: bool`, `message: str | None`
  - `DockerAdapter.start(runtime: dict[str, Any]) -> RuntimeHealth`
  - `DockerAdapter.stop(runtime: dict[str, Any]) -> RuntimeHealth`
  - `DockerAdapter.logs(runtime: dict[str, Any], tail: int = 100) -> str`
  - `refresh_runtime(db, runtime_id: str, docker: DockerAdapter | None = None) -> dict[str, Any]`
  - `start_runtime(db, runtime_id: str, docker: DockerAdapter | None = None) -> dict[str, Any]`
  - `stop_runtime(db, runtime_id: str, docker: DockerAdapter | None = None) -> dict[str, Any]`

- [ ] **Step 1: Write failing manager tests**

Create `services/local-api/tests/test_ai_runtime_manager.py`:

```python
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from laura.ai.runtime_manager import refresh_runtime, start_runtime, stop_runtime
from laura.ai.runtime_types import RuntimeHealth
from laura.config import Settings
from laura.db import repos
from laura.db.database import create_database


def _db(tmp_path):
    db = create_database(Settings(workspace_root=tmp_path, start_runner=False))
    db.migrate()
    return db


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true, "ready": true}')
            return
        if self.path == "/capabilities":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"effects": ["lipsync"], "metrics": ["sync_score"]}).encode())
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args):  # noqa: ANN002
        return


def test_refresh_external_http_runtime_reads_health_and_capabilities(tmp_path):
    db = _db(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        rt = repos.create_ai_runtime(
            db,
            kind="external_http",
            effect="lipsync",
            display_name="Fake Lipsync",
            base_url=f"http://127.0.0.1:{server.server_port}",
        )
        refreshed = refresh_runtime(db, rt["id"])
        assert refreshed["status"]["state"] == "ready"
        assert refreshed["status"]["ready"] is True
        assert refreshed["capabilities"]["effects"] == ["lipsync"]
    finally:
        server.shutdown()


class FakeDocker:
    def __init__(self):
        self.started = False
        self.stopped = False

    def start(self, runtime):
        self.started = True
        return RuntimeHealth(state="starting", ready=False, message="container started")

    def stop(self, runtime):
        self.stopped = True
        return RuntimeHealth(state="stopped", ready=False, message="container stopped")

    def logs(self, runtime, tail=100):  # noqa: ARG002
        return "log line"


def test_start_and_stop_container_runtime_are_evented(tmp_path):
    db = _db(tmp_path)
    docker = FakeDocker()
    rt = repos.create_ai_runtime(
        db,
        kind="container",
        effect="voice",
        display_name="Voice",
        container_image="laura-runtime-voice:local",
        container_name="laura-voice",
        port=8898,
    )

    started = start_runtime(db, rt["id"], docker=docker)
    assert docker.started is True
    assert started["status"]["state"] == "starting"

    stopped = stop_runtime(db, rt["id"], docker=docker)
    assert docker.stopped is True
    assert stopped["status"]["state"] == "stopped"

    events = repos.list_ai_runtime_events(db, rt["id"])
    assert [e["event_type"] for e in events[:2]] == ["stop", "start"]
```

- [ ] **Step 2: Run RED**

```powershell
uv run pytest tests/test_ai_runtime_manager.py -q
```

Expected: FAIL with missing modules.

- [ ] **Step 3: Implement runtime types**

Create `services/local-api/src/laura/ai/runtime_types.py`:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RuntimeHealth:
    state: str
    ready: bool
    message: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)
```

- [ ] **Step 4: Implement Docker adapter**

Create `services/local-api/src/laura/ai/docker_runtime.py`:

```python
from __future__ import annotations

import subprocess
from typing import Any

from .runtime_types import RuntimeHealth


class DockerAdapter:
    """Small Docker CLI adapter. Dependency-free and easy to fake in tests."""

    def start(self, runtime: dict[str, Any]) -> RuntimeHealth:
        name = str(runtime.get("container_name") or "")
        image = str(runtime.get("container_image") or "")
        port = runtime.get("port")
        if not name or not image:
            return RuntimeHealth("error", False, "container_name and container_image are required")

        existing = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if name in existing.stdout.splitlines():
            result = subprocess.run(["docker", "start", name], capture_output=True, text=True, check=False)
        else:
            cmd = ["docker", "run", "-d", "--name", name]
            if port is not None:
                cmd.extend(["-p", f"{int(port)}:{int(port)}"])
            workspace_mount = runtime.get("workspace_mount")
            if isinstance(workspace_mount, str) and workspace_mount:
                cmd.extend(["-v", workspace_mount])
            model_mount = runtime.get("model_mount")
            if isinstance(model_mount, str) and model_mount:
                cmd.extend(["-v", model_mount])
            if runtime.get("requires_gpu"):
                cmd.extend(["--gpus", "all"])
            cmd.append(image)
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)

        if result.returncode != 0:
            return RuntimeHealth("error", False, (result.stderr or result.stdout).strip())
        return RuntimeHealth("starting", False, "container started")

    def stop(self, runtime: dict[str, Any]) -> RuntimeHealth:
        name = str(runtime.get("container_name") or "")
        if not name:
            return RuntimeHealth("error", False, "container_name is required")
        result = subprocess.run(["docker", "stop", name], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return RuntimeHealth("error", False, (result.stderr or result.stdout).strip())
        return RuntimeHealth("stopped", False, "container stopped")

    def logs(self, runtime: dict[str, Any], tail: int = 100) -> str:
        name = str(runtime.get("container_name") or "")
        if not name:
            return ""
        result = subprocess.run(
            ["docker", "logs", "--tail", str(tail), name],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout if result.returncode == 0 else result.stderr
```

- [ ] **Step 5: Implement runtime manager**

Create `services/local-api/src/laura/ai/runtime_manager.py`:

```python
from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..db import repos
from ..db.database import Database
from .docker_runtime import DockerAdapter
from .runtime_types import RuntimeHealth


def _get_json(url: str, timeout: float = 2.0) -> dict[str, Any]:
    request = Request(url, method="GET")
    with urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
    data = json.loads(raw or "{}")
    return data if isinstance(data, dict) else {}


def _base_url(runtime: dict[str, Any]) -> str | None:
    raw = runtime.get("base_url")
    if isinstance(raw, str) and raw:
        return raw.rstrip("/")
    port = runtime.get("port")
    if port is not None:
        return f"http://127.0.0.1:{int(port)}"
    return None


def refresh_runtime(db: Database, runtime_id: str, docker: DockerAdapter | None = None) -> dict[str, Any]:
    runtime = repos.get_ai_runtime(db, runtime_id)
    if runtime is None:
        raise ValueError(f"runtime not found: {runtime_id}")
    if runtime["kind"] == "stub":
        status = RuntimeHealth("ready", True, "stub runtime").to_json()
        capabilities = {"effects": [runtime["effect"]], "stub": True}
    else:
        base = _base_url(runtime)
        if base is None:
            status = RuntimeHealth("error", False, "runtime has no base_url or port").to_json()
            capabilities = runtime.get("capabilities", {})
        else:
            try:
                health = _get_json(f"{base}/healthz")
                caps = _get_json(f"{base}/capabilities")
                ready = bool(health.get("ready", health.get("ok", False)))
                status = RuntimeHealth(
                    "ready" if ready else "not_ready",
                    ready,
                    health.get("message") if isinstance(health.get("message"), str) else None,
                ).to_json()
                capabilities = caps
            except (HTTPError, OSError, TimeoutError, URLError, ValueError, json.JSONDecodeError) as exc:
                status = RuntimeHealth("unreachable", False, str(exc)).to_json()
                capabilities = runtime.get("capabilities", {})
    repos.update_ai_runtime_status(db, runtime_id, status=status, capabilities=capabilities)
    repos.create_ai_runtime_event(
        db,
        runtime_id=runtime_id,
        event_type="health",
        level="info" if status.get("ready") else "warning",
        message=str(status.get("message") or status.get("state")),
        payload=status,
    )
    updated = repos.get_ai_runtime(db, runtime_id)
    assert updated is not None
    return updated


def start_runtime(db: Database, runtime_id: str, docker: DockerAdapter | None = None) -> dict[str, Any]:
    runtime = repos.get_ai_runtime(db, runtime_id)
    if runtime is None:
        raise ValueError(f"runtime not found: {runtime_id}")
    if runtime["kind"] != "container":
        health = RuntimeHealth("ready", True, f"{runtime['kind']} runtime does not need start")
    else:
        health = (docker or DockerAdapter()).start(runtime)
    repos.update_ai_runtime_status(db, runtime_id, status=health.to_json())
    repos.create_ai_runtime_event(
        db,
        runtime_id=runtime_id,
        event_type="start",
        level="info" if health.state != "error" else "error",
        message=health.message or health.state,
        payload=health.to_json(),
    )
    updated = repos.get_ai_runtime(db, runtime_id)
    assert updated is not None
    return updated


def stop_runtime(db: Database, runtime_id: str, docker: DockerAdapter | None = None) -> dict[str, Any]:
    runtime = repos.get_ai_runtime(db, runtime_id)
    if runtime is None:
        raise ValueError(f"runtime not found: {runtime_id}")
    if runtime["kind"] != "container":
        health = RuntimeHealth("stopped", False, f"{runtime['kind']} runtime cannot be stopped")
    else:
        health = (docker or DockerAdapter()).stop(runtime)
    repos.update_ai_runtime_status(db, runtime_id, status=health.to_json())
    repos.create_ai_runtime_event(
        db,
        runtime_id=runtime_id,
        event_type="stop",
        level="info" if health.state != "error" else "error",
        message=health.message or health.state,
        payload=health.to_json(),
    )
    updated = repos.get_ai_runtime(db, runtime_id)
    assert updated is not None
    return updated
```

- [ ] **Step 6: Run GREEN**

```powershell
uv run pytest tests/test_ai_runtime_manager.py -q
uv run ruff check src/laura/ai/runtime_types.py src/laura/ai/docker_runtime.py src/laura/ai/runtime_manager.py tests/test_ai_runtime_manager.py
uv run mypy src/laura/ai/runtime_types.py src/laura/ai/docker_runtime.py src/laura/ai/runtime_manager.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add services/local-api/src/laura/ai/runtime_types.py services/local-api/src/laura/ai/docker_runtime.py services/local-api/src/laura/ai/runtime_manager.py services/local-api/tests/test_ai_runtime_manager.py
git commit -m "feat: add AI runtime manager"
```

### Task 3: Runtime and Persona API

**Files:**
- Modify: `services/local-api/src/laura/api/models.py`
- Create: `services/local-api/src/laura/api/persona.py`
- Modify: `services/local-api/src/laura/main.py`
- Test: `services/local-api/tests/test_ai_runtime_api.py`
- Test: `services/local-api/tests/test_ai_personas.py`

**Interfaces:**
- Consumes: repo helpers from Task 1 and runtime manager from Task 2.
- Produces endpoints:
  - `POST /ai/runtimes`
  - `GET /ai/runtimes`
  - `POST /ai/runtimes/{id}/refresh`
  - `POST /ai/runtimes/{id}/start`
  - `POST /ai/runtimes/{id}/stop`
  - `GET /ai/runtimes/{id}/events`
  - `POST /ai/personas`
  - `GET /ai/personas?project_id=...`

- [ ] **Step 1: Write failing API tests**

Create `services/local-api/tests/test_ai_runtime_api.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient


def test_create_and_list_stub_runtime(client: TestClient) -> None:
    response = client.post(
        "/ai/runtimes",
        json={
            "kind": "stub",
            "effect": "voice",
            "display_name": "Stub Voice",
            "license_status": "not_required",
        },
    )
    assert response.status_code == 201
    created = response.json()
    assert created["kind"] == "stub"
    assert created["status"] == {}

    listed = client.get("/ai/runtimes?effect=voice")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == created["id"]


def test_refresh_stub_runtime_returns_ready_status(client: TestClient) -> None:
    created = client.post(
        "/ai/runtimes",
        json={"kind": "stub", "effect": "lipsync", "display_name": "Stub Lipsync"},
    ).json()

    refreshed = client.post(f"/ai/runtimes/{created['id']}/refresh")
    assert refreshed.status_code == 200
    body = refreshed.json()
    assert body["status"]["state"] == "ready"
    assert body["status"]["ready"] is True
    assert body["capabilities"]["effects"] == ["lipsync"]

    events = client.get(f"/ai/runtimes/{created['id']}/events")
    assert events.status_code == 200
    assert events.json()[0]["event_type"] == "health"
```

Create `services/local-api/tests/test_ai_personas.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient


def test_create_persona_requires_active_consent(client: TestClient, tmp_path) -> None:
    project = client.post(
        "/projects",
        json={
            "name": "P",
            "sequence_rate_num": 30,
            "sequence_rate_den": 1,
            "drop_frame": False,
        },
    ).json()
    consent = client.post(
        f"/projects/{project['id']}/consent",
        json={"subject_label": "Persona"},
    ).json()

    response = client.post(
        "/ai/personas",
        json={
            "project_id": project["id"],
            "name": "Persona",
            "consent_id": consent["id"],
            "allowed_effects": ["voice", "lipsync"],
            "preferred_runtimes": {"voice": "stub-voice"},
        },
    )
    assert response.status_code == 201
    persona = response.json()
    assert persona["name"] == "Persona"
    assert persona["allowed_effects"] == ["voice", "lipsync"]

    listed = client.get(f"/ai/personas?project_id={project['id']}")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == persona["id"]


def test_create_persona_rejects_revoked_consent(client: TestClient) -> None:
    project = client.post(
        "/projects",
        json={
            "name": "P",
            "sequence_rate_num": 30,
            "sequence_rate_den": 1,
            "drop_frame": False,
        },
    ).json()
    consent = client.post(
        f"/projects/{project['id']}/consent",
        json={"subject_label": "Persona"},
    ).json()
    client.post(f"/projects/{project['id']}/consent/{consent['id']}/revoke")

    response = client.post(
        "/ai/personas",
        json={"project_id": project["id"], "name": "Persona", "consent_id": consent["id"]},
    )
    assert response.status_code == 400
    assert "revoked" in response.text
```

- [ ] **Step 2: Run RED**

```powershell
uv run pytest tests/test_ai_runtime_api.py tests/test_ai_personas.py -q
```

Expected: FAIL with 404 or missing models.

- [ ] **Step 3: Add Pydantic models**

Append to `services/local-api/src/laura/api/models.py` before overlay models:

```python
RuntimeKind = Literal["stub", "external_http", "container"]
RuntimeEffect = Literal["voice", "reenact", "lipsync", "faceswap", "restore"]
LicenseStatus = Literal["unknown", "accepted", "rejected", "not_required"]


class AiRuntimeCreate(BaseModel):
    kind: RuntimeKind
    effect: RuntimeEffect
    display_name: str = Field(min_length=1, max_length=200)
    base_url: str | None = None
    container_image: str | None = None
    container_name: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    workspace_mount: str | None = None
    model_mount: str | None = None
    requires_gpu: bool = False
    enabled: bool = True
    license_status: LicenseStatus = "unknown"


class AiRuntimeOut(AiRuntimeCreate):
    id: str
    status: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    last_health_at: str | None = None
    created_at: str
    updated_at: str


class AiRuntimeEventOut(BaseModel):
    id: str
    runtime_id: str
    event_type: str
    level: str
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class AiPersonaCreate(BaseModel):
    project_id: str | None = None
    name: str = Field(min_length=1, max_length=200)
    consent_id: str
    face_reference_asset_id: str | None = None
    voice_reference_asset_id: str | None = None
    style: dict[str, Any] = Field(default_factory=dict)
    allowed_effects: list[RuntimeEffect] = Field(default_factory=list)
    preferred_runtimes: dict[str, str] = Field(default_factory=dict)


class AiPersonaOut(AiPersonaCreate):
    id: str
    created_at: str
    updated_at: str
```

- [ ] **Step 4: Implement API router**

Create `services/local-api/src/laura/api/persona.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from ..ai.runtime_manager import refresh_runtime, start_runtime, stop_runtime
from ..db import repos
from ..db.database import Database
from .models import (
    AiPersonaCreate,
    AiPersonaOut,
    AiRuntimeCreate,
    AiRuntimeEventOut,
    AiRuntimeOut,
)
from .security import require_token

router = APIRouter(tags=["ai-persona"], dependencies=[Depends(require_token)])


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


@router.post("/ai/runtimes", response_model=AiRuntimeOut, status_code=status.HTTP_201_CREATED)
def create_runtime(body: AiRuntimeCreate, request: Request) -> AiRuntimeOut:
    runtime = repos.create_ai_runtime(_db(request), **body.model_dump())
    return AiRuntimeOut(**runtime)


@router.get("/ai/runtimes", response_model=list[AiRuntimeOut])
def list_runtimes(
    request: Request, effect: str | None = Query(default=None)
) -> list[AiRuntimeOut]:
    return [AiRuntimeOut(**row) for row in repos.list_ai_runtimes(_db(request), effect=effect)]


@router.post("/ai/runtimes/{runtime_id}/refresh", response_model=AiRuntimeOut)
def refresh_runtime_route(runtime_id: str, request: Request) -> AiRuntimeOut:
    try:
        runtime = refresh_runtime(_db(request), runtime_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return AiRuntimeOut(**runtime)


@router.post("/ai/runtimes/{runtime_id}/start", response_model=AiRuntimeOut)
def start_runtime_route(runtime_id: str, request: Request) -> AiRuntimeOut:
    try:
        runtime = start_runtime(_db(request), runtime_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return AiRuntimeOut(**runtime)


@router.post("/ai/runtimes/{runtime_id}/stop", response_model=AiRuntimeOut)
def stop_runtime_route(runtime_id: str, request: Request) -> AiRuntimeOut:
    try:
        runtime = stop_runtime(_db(request), runtime_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return AiRuntimeOut(**runtime)


@router.get("/ai/runtimes/{runtime_id}/events", response_model=list[AiRuntimeEventOut])
def list_runtime_events(runtime_id: str, request: Request) -> list[AiRuntimeEventOut]:
    if repos.get_ai_runtime(_db(request), runtime_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "runtime not found")
    rows = repos.list_ai_runtime_events(_db(request), runtime_id)
    return [AiRuntimeEventOut(**row) for row in rows]


@router.post("/ai/personas", response_model=AiPersonaOut, status_code=status.HTTP_201_CREATED)
def create_persona(body: AiPersonaCreate, request: Request) -> AiPersonaOut:
    db = _db(request)
    consent = repos.get_consent_record(db, body.consent_id)
    if consent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "consent record not found")
    if consent.get("revoked_at") is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "consent has been revoked")
    if body.project_id is not None and consent["project_id"] != body.project_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "consent belongs to another project")
    persona = repos.create_ai_persona(db, **body.model_dump())
    return AiPersonaOut(**persona)


@router.get("/ai/personas", response_model=list[AiPersonaOut])
def list_personas(
    request: Request, project_id: str | None = Query(default=None)
) -> list[AiPersonaOut]:
    rows = repos.list_ai_personas(_db(request), project_id=project_id)
    return [AiPersonaOut(**row) for row in rows]
```

- [ ] **Step 5: Mount router**

In `services/local-api/src/laura/main.py`, add `persona` to the API imports and include it:

```python
from .api import (
    admin,
    analysis,
    assets,
    audio,
    demo,
    jobs,
    lipsync,
    overlays,
    persona,
    projects,
    reels,
    reenact,
    scenes,
    search,
    sequences,
    timelines,
    voiceover,
)
```

And near the other routers:

```python
app.include_router(persona.router)
```

- [ ] **Step 6: Run GREEN**

```powershell
uv run pytest tests/test_ai_runtime_api.py tests/test_ai_personas.py -q
uv run ruff check src/laura/api/persona.py src/laura/api/models.py src/laura/main.py tests/test_ai_runtime_api.py tests/test_ai_personas.py
uv run mypy src/laura/api/persona.py src/laura/api/models.py src/laura/main.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add services/local-api/src/laura/api/models.py services/local-api/src/laura/api/persona.py services/local-api/src/laura/main.py services/local-api/tests/test_ai_runtime_api.py services/local-api/tests/test_ai_personas.py
git commit -m "feat: expose AI persona runtime API"
```

### Task 4: Runtime ID Routing for Existing AI Jobs

**Files:**
- Modify: `services/local-api/src/laura/api/models.py`
- Modify: `services/local-api/src/laura/api/reenact.py`
- Modify: `services/local-api/src/laura/api/lipsync.py`
- Modify: `services/local-api/src/laura/api/voiceover.py`
- Modify: `services/local-api/src/laura/ai/handlers.py`
- Test: `services/local-api/tests/test_lipsync_api.py`
- Test: `services/local-api/tests/test_ai_runtime_job_routing.py`
- Test: `services/local-api/tests/test_reenact_job.py`
- Test: `services/local-api/tests/test_voiceover.py`

**Interfaces:**
- Consumes: `get_ai_runtime`.
- Produces:
  - Request models accept `runtime_id: str | None`.
  - Job payloads include `runtime_id`.
  - Handlers map `runtime_id` to existing backend names using runtime `kind/effect/base_url`.

- [ ] **Step 1: Write failing routing tests**

Extend `services/local-api/tests/test_lipsync_api.py` with:

```python
def test_lipsync_api_accepts_runtime_id(client: TestClient, tmp_path: Path) -> None:
    seeded = _seed_lipsync_api(client, tmp_path)
    runtime = client.post(
        "/ai/runtimes",
        json={"kind": "stub", "effect": "lipsync", "display_name": "Stub Lipsync"},
    ).json()

    response = client.post(
        f"/timelines/{seeded['timeline_id']}/lipsync",
        json={
            "seq_in_frame": 0,
            "seq_out_frame_exclusive": 12,
            "audio_asset_id": seeded["audio_asset_id"],
            "consent_id": seeded["consent_id"],
            "license_accepted": True,
            "runtime_id": runtime["id"],
        },
    )
    assert response.status_code == 202
    job = seeded["db_get_job"](response.json()["job_id"])
    assert job["payload"]["runtime_id"] == runtime["id"]
```

Create `services/local-api/tests/test_ai_runtime_job_routing.py`:

```python
from __future__ import annotations

from laura.ai.handlers import _backend_from_runtime
from laura.config import Settings
from laura.db import repos
from laura.db.database import create_database


def _db(tmp_path):
    db = create_database(Settings(workspace_root=tmp_path, start_runner=False))
    db.migrate()
    return db


def test_backend_from_runtime_maps_stub_to_stub(tmp_path):
    db = _db(tmp_path)
    runtime = repos.create_ai_runtime(
        db,
        kind="stub",
        effect="lipsync",
        display_name="Stub Lipsync",
    )

    assert _backend_from_runtime(db, runtime["id"], "vibevideo") == "stub"


def test_backend_from_runtime_maps_external_and_container_to_legacy_backend(tmp_path):
    db = _db(tmp_path)
    external = repos.create_ai_runtime(
        db,
        kind="external_http",
        effect="voice",
        display_name="Voice HTTP",
        base_url="http://127.0.0.1:8898",
    )
    container = repos.create_ai_runtime(
        db,
        kind="container",
        effect="reenact",
        display_name="LivePortrait",
        container_image="laura-runtime-liveportrait:local",
    )

    assert _backend_from_runtime(db, external["id"], None) == "sidecar"
    assert _backend_from_runtime(db, container["id"], None) == "liveportrait"


def test_backend_from_runtime_keeps_legacy_fallback_without_runtime(tmp_path):
    db = _db(tmp_path)

    assert _backend_from_runtime(db, None, "stub") == "stub"
```

- [ ] **Step 2: Run RED**

```powershell
uv run pytest tests/test_lipsync_api.py::test_lipsync_api_accepts_runtime_id tests/test_ai_runtime_job_routing.py -q
```

Expected: FAIL because request model ignores or rejects `runtime_id`, or payload omits it.

- [ ] **Step 3: Add `runtime_id` to request models**

In `services/local-api/src/laura/api/models.py`:

```python
class VoiceoverRequest(BaseModel):
    ...
    backend: str | None = None
    runtime_id: str | None = None
    ...


class ReenactRequest(BaseModel):
    ...
    backend: str | None = None
    runtime_id: str | None = None


class LipsyncRequest(BaseModel):
    ...
    backend: str | None = None
    runtime_id: str | None = None
    quality_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
```

- [ ] **Step 4: Validate runtime effect in endpoints**

In `reenact.py`, `lipsync.py`, and `voiceover.py`, add this local helper:

```python
def _validate_runtime(db: Database, runtime_id: str | None, effect: str) -> None:
    if runtime_id is None:
        return
    runtime = repos.get_ai_runtime(db, runtime_id)
    if runtime is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "runtime not found")
    if runtime["effect"] != effect:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"runtime effect must be {effect}",
        )
    if not runtime["enabled"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "runtime is disabled")
```

Call it before enqueueing, and add `"runtime_id": body.runtime_id` to each AI job payload.

- [ ] **Step 5: Resolve runtime in handlers**

In `services/local-api/src/laura/ai/handlers.py`, add:

```python
def _backend_from_runtime(db: Database, runtime_id: str | None, fallback: str | None) -> str | None:
    if runtime_id is None:
        return fallback
    runtime = repos.get_ai_runtime(db, runtime_id)
    if runtime is None:
        raise ValueError(f"runtime not found: {runtime_id!r}")
    if runtime["kind"] == "stub":
        return "stub"
    effect_backend = {
        "voice": "sidecar",
        "reenact": "liveportrait",
        "lipsync": "vibevideo",
    }.get(str(runtime["effect"]))
    if effect_backend is None:
        raise ValueError(f"unsupported runtime effect: {runtime['effect']!r}")
    if runtime["kind"] == "external_http":
        return effect_backend
    if runtime["kind"] == "container":
        return effect_backend
    raise ValueError(f"unsupported runtime kind: {runtime['kind']!r}")
```

Then replace resolver calls:

```python
backend = resolve_lipsync_backend(
    _backend_from_runtime(ctx.db, payload.get("runtime_id"), payload.get("backend"))
)
```

Use the same helper in reenact and voiceover resolver calls:

```python
backend = resolve_reenact_backend(
    _backend_from_runtime(ctx.db, payload.get("runtime_id"), payload.get("backend"))
)
```

```python
backend = resolve_voiceover_backend(
    _backend_from_runtime(ctx.db, payload.get("runtime_id"), payload.get("backend"))
)
```

This task preserves selected runtime identity and maps stubs cleanly. The runtime's `base_url` is persisted now; sidecar resolver URL injection can be added when the first real container contract is implemented.

- [ ] **Step 6: Run GREEN**

```powershell
uv run pytest tests/test_lipsync_api.py tests/test_ai_runtime_job_routing.py tests/test_reenact_job.py tests/test_voiceover.py -q
uv run ruff check src/laura/api/reenact.py src/laura/api/lipsync.py src/laura/api/voiceover.py src/laura/ai/handlers.py src/laura/api/models.py
uv run mypy src/laura/api/reenact.py src/laura/api/lipsync.py src/laura/api/voiceover.py src/laura/ai/handlers.py src/laura/api/models.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add services/local-api/src/laura/api/models.py services/local-api/src/laura/api/reenact.py services/local-api/src/laura/api/lipsync.py services/local-api/src/laura/api/voiceover.py services/local-api/src/laura/ai/handlers.py services/local-api/tests/test_lipsync_api.py services/local-api/tests/test_ai_runtime_job_routing.py services/local-api/tests/test_reenact_job.py services/local-api/tests/test_voiceover.py
git commit -m "feat: route AI jobs by runtime id"
```

### Task 5: Desktop Client Types for Runtimes and Personas

**Files:**
- Modify: `apps/desktop/src/api.ts`
- Test: `apps/desktop/src/api.test.ts`

**Interfaces:**
- Consumes: API routes from Task 3.
- Produces:
  - `AiRuntime`, `AiRuntimeCreate`, `AiRuntimeEvent`, `AiPersona`, `AiPersonaCreate`
  - `listAiRuntimes(effect?: RuntimeEffect)`
  - `createAiRuntime(input: AiRuntimeCreate)`
  - `refreshAiRuntime(runtimeId: string)`
  - `startAiRuntime(runtimeId: string)`
  - `stopAiRuntime(runtimeId: string)`
  - `listAiRuntimeEvents(runtimeId: string)`
  - `listAiPersonas(projectId?: string)`
  - `createAiPersona(input: AiPersonaCreate)`

- [ ] **Step 1: Write failing API tests**

Append to `apps/desktop/src/api.test.ts`:

```ts
it("creates and refreshes AI runtimes", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ id: "rt-1" }), { status: 201 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ id: "rt-1", status: { ready: true } }), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
  const client = new LauraClient("http://localhost", "token");

  await client.createAiRuntime({
    kind: "container",
    effect: "lipsync",
    displayName: "Lipsync",
    containerImage: "laura-runtime-lipsync:local",
  });
  await client.refreshAiRuntime("rt-1");

  expect(fetchMock).toHaveBeenNthCalledWith(
    1,
    "http://localhost/ai/runtimes",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({
        kind: "container",
        effect: "lipsync",
        display_name: "Lipsync",
        container_image: "laura-runtime-lipsync:local",
      }),
    }),
  );
  expect(fetchMock).toHaveBeenNthCalledWith(
    2,
    "http://localhost/ai/runtimes/rt-1/refresh",
    expect.objectContaining({ method: "POST" }),
  );
});

it("creates AI personas with preferred runtimes", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ id: "persona-1" }), { status: 201 }),
  );
  vi.stubGlobal("fetch", fetchMock);
  const client = new LauraClient("http://localhost", "token");

  await client.createAiPersona({
    projectId: "project-1",
    name: "Persona",
    consentId: "consent-1",
    allowedEffects: ["voice", "lipsync"],
    preferredRuntimes: { voice: "rt-voice" },
  });

  expect(fetchMock).toHaveBeenCalledWith(
    "http://localhost/ai/personas",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({
        project_id: "project-1",
        name: "Persona",
        consent_id: "consent-1",
        allowed_effects: ["voice", "lipsync"],
        preferred_runtimes: { voice: "rt-voice" },
      }),
    }),
  );
});
```

- [ ] **Step 2: Run RED**

```powershell
pnpm --dir apps/desktop test -- src/api.test.ts
```

Expected: FAIL with missing client methods.

- [ ] **Step 3: Add TypeScript interfaces and client methods**

In `apps/desktop/src/api.ts`, add:

```ts
export type RuntimeKind = "stub" | "external_http" | "container";
export type RuntimeEffect = "voice" | "reenact" | "lipsync" | "faceswap" | "restore";
export type LicenseStatus = "unknown" | "accepted" | "rejected" | "not_required";

export interface AiRuntime {
  id: string;
  kind: RuntimeKind;
  effect: RuntimeEffect;
  display_name: string;
  base_url: string | null;
  container_image: string | null;
  container_name: string | null;
  port: number | null;
  workspace_mount: string | null;
  model_mount: string | null;
  requires_gpu: boolean;
  enabled: boolean;
  license_status: LicenseStatus;
  status: Record<string, unknown>;
  capabilities: Record<string, unknown>;
  last_health_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AiRuntimeCreate {
  kind: RuntimeKind;
  effect: RuntimeEffect;
  displayName: string;
  baseUrl?: string;
  containerImage?: string;
  containerName?: string;
  port?: number;
  workspaceMount?: string;
  modelMount?: string;
  requiresGpu?: boolean;
  enabled?: boolean;
  licenseStatus?: LicenseStatus;
}

export interface AiRuntimeEvent {
  id: string;
  runtime_id: string;
  event_type: string;
  level: string;
  message: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface AiPersona {
  id: string;
  project_id: string | null;
  name: string;
  consent_id: string;
  face_reference_asset_id: string | null;
  voice_reference_asset_id: string | null;
  style: Record<string, unknown>;
  allowed_effects: RuntimeEffect[];
  preferred_runtimes: Record<string, string>;
  created_at: string;
  updated_at: string;
}

export interface AiPersonaCreate {
  projectId?: string;
  name: string;
  consentId: string;
  faceReferenceAssetId?: string;
  voiceReferenceAssetId?: string;
  style?: Record<string, unknown>;
  allowedEffects?: RuntimeEffect[];
  preferredRuntimes?: Record<string, string>;
}
```

Inside `LauraClient`:

```ts
createAiRuntime(input: AiRuntimeCreate): Promise<AiRuntime> {
  const body: Record<string, unknown> = {
    kind: input.kind,
    effect: input.effect,
    display_name: input.displayName,
  };
  if (input.baseUrl !== undefined) body.base_url = input.baseUrl;
  if (input.containerImage !== undefined) body.container_image = input.containerImage;
  if (input.containerName !== undefined) body.container_name = input.containerName;
  if (input.port !== undefined) body.port = input.port;
  if (input.workspaceMount !== undefined) body.workspace_mount = input.workspaceMount;
  if (input.modelMount !== undefined) body.model_mount = input.modelMount;
  if (input.requiresGpu !== undefined) body.requires_gpu = input.requiresGpu;
  if (input.enabled !== undefined) body.enabled = input.enabled;
  if (input.licenseStatus !== undefined) body.license_status = input.licenseStatus;
  return this.request<AiRuntime>("/ai/runtimes", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

listAiRuntimes(effect?: RuntimeEffect): Promise<AiRuntime[]> {
  const qs = effect ? `?effect=${encodeURIComponent(effect)}` : "";
  return this.request<AiRuntime[]>(`/ai/runtimes${qs}`);
}

refreshAiRuntime(runtimeId: string): Promise<AiRuntime> {
  return this.request<AiRuntime>(`/ai/runtimes/${runtimeId}/refresh`, { method: "POST" });
}

startAiRuntime(runtimeId: string): Promise<AiRuntime> {
  return this.request<AiRuntime>(`/ai/runtimes/${runtimeId}/start`, { method: "POST" });
}

stopAiRuntime(runtimeId: string): Promise<AiRuntime> {
  return this.request<AiRuntime>(`/ai/runtimes/${runtimeId}/stop`, { method: "POST" });
}

listAiRuntimeEvents(runtimeId: string): Promise<AiRuntimeEvent[]> {
  return this.request<AiRuntimeEvent[]>(`/ai/runtimes/${runtimeId}/events`);
}

createAiPersona(input: AiPersonaCreate): Promise<AiPersona> {
  const body: Record<string, unknown> = {
    name: input.name,
    consent_id: input.consentId,
  };
  if (input.projectId !== undefined) body.project_id = input.projectId;
  if (input.faceReferenceAssetId !== undefined) body.face_reference_asset_id = input.faceReferenceAssetId;
  if (input.voiceReferenceAssetId !== undefined) body.voice_reference_asset_id = input.voiceReferenceAssetId;
  if (input.style !== undefined) body.style = input.style;
  if (input.allowedEffects !== undefined) body.allowed_effects = input.allowedEffects;
  if (input.preferredRuntimes !== undefined) body.preferred_runtimes = input.preferredRuntimes;
  return this.request<AiPersona>("/ai/personas", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

listAiPersonas(projectId?: string): Promise<AiPersona[]> {
  const qs = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
  return this.request<AiPersona[]>(`/ai/personas${qs}`);
}
```

- [ ] **Step 4: Run GREEN**

```powershell
pnpm --dir apps/desktop test -- src/api.test.ts
pnpm --dir apps/desktop exec tsc --noEmit
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add apps/desktop/src/api.ts apps/desktop/src/api.test.ts
git commit -m "feat: add AI persona runtime client"
```

### Task 6: Runtime Status UI

**Files:**
- Create: `apps/desktop/src/components/RuntimeStatusPanel.tsx`
- Create: `apps/desktop/src/components/RuntimeStatusPanel.test.tsx`
- Modify: `apps/desktop/src/components/AssembleView.tsx`

**Interfaces:**
- Consumes: client methods from Task 5.
- Produces: `RuntimeStatusPanel({ client, reloadKey }: { client: LauraClient; reloadKey?: number })`.

- [ ] **Step 1: Write failing UI tests**

Create `apps/desktop/src/components/RuntimeStatusPanel.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { type LauraClient } from "../api";
import { RuntimeStatusPanel } from "./RuntimeStatusPanel";

function client(overrides: Partial<LauraClient> = {}): LauraClient {
  return {
    listAiRuntimes: vi.fn().mockResolvedValue([
      {
        id: "rt-1",
        kind: "stub",
        effect: "lipsync",
        display_name: "Stub Lipsync",
        status: { state: "ready", ready: true },
        capabilities: { effects: ["lipsync"] },
        base_url: null,
        container_image: null,
        container_name: null,
        port: null,
        workspace_mount: null,
        model_mount: null,
        requires_gpu: false,
        enabled: true,
        license_status: "not_required",
        last_health_at: null,
        created_at: "",
        updated_at: "",
      },
    ]),
    refreshAiRuntime: vi.fn().mockResolvedValue({}),
    startAiRuntime: vi.fn().mockResolvedValue({}),
    stopAiRuntime: vi.fn().mockResolvedValue({}),
    listAiRuntimeEvents: vi.fn().mockResolvedValue([]),
    ...overrides,
  } as unknown as LauraClient;
}

describe("RuntimeStatusPanel", () => {
  it("lists runtimes with their status", async () => {
    render(<RuntimeStatusPanel client={client()} />);
    expect(await screen.findByText("Stub Lipsync")).toBeTruthy();
    expect(screen.getByText("ready")).toBeTruthy();
  });

  it("refreshes a runtime", async () => {
    const refreshAiRuntime = vi.fn().mockResolvedValue({});
    render(<RuntimeStatusPanel client={client({ refreshAiRuntime } as Partial<LauraClient>)} />);
    await screen.findByText("Stub Lipsync");
    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));
    await waitFor(() => expect(refreshAiRuntime).toHaveBeenCalledWith("rt-1"));
  });
});
```

- [ ] **Step 2: Run RED**

```powershell
pnpm --dir apps/desktop test -- src/components/RuntimeStatusPanel.test.tsx
```

Expected: FAIL because component does not exist.

- [ ] **Step 3: Implement component**

Create `apps/desktop/src/components/RuntimeStatusPanel.tsx`:

```tsx
import { type ReactElement, useEffect, useState } from "react";
import { type AiRuntime, type LauraClient } from "../api";

function statusText(runtime: AiRuntime): string {
  const state = runtime.status.state;
  return typeof state === "string" ? state : "unknown";
}

export function RuntimeStatusPanel({
  client,
  reloadKey = 0,
}: {
  client: LauraClient;
  reloadKey?: number;
}): ReactElement {
  const [runtimes, setRuntimes] = useState<AiRuntime[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function load(): Promise<void> {
    try {
      setRuntimes(await client.listAiRuntimes());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  useEffect(() => {
    void load();
  }, [client, reloadKey]);

  async function refresh(id: string): Promise<void> {
    await client.refreshAiRuntime(id);
    await load();
  }

  async function start(id: string): Promise<void> {
    await client.startAiRuntime(id);
    await load();
  }

  async function stop(id: string): Promise<void> {
    await client.stopAiRuntime(id);
    await load();
  }

  return (
    <section className="rounded border border-edge bg-panel/50 p-3">
      <div className="mb-2 flex items-center justify-between">
        <div className="text-xs font-semibold text-slate-200">AI Runtimes</div>
        <button
          type="button"
          onClick={() => void load()}
          className="rounded border border-edge px-2 py-1 text-[11px] text-slate-300"
        >
          Neu laden
        </button>
      </div>
      {error !== null && <div className="mb-2 text-xs text-red-400">{error}</div>}
      <div className="flex flex-col gap-2">
        {runtimes.length === 0 ? (
          <div className="text-xs text-slate-500">Noch keine Runtime registriert.</div>
        ) : (
          runtimes.map((runtime) => (
            <article key={runtime.id} className="rounded border border-edge bg-ink/60 p-2">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <div className="text-xs font-medium text-slate-100">{runtime.display_name}</div>
                  <div className="text-[11px] text-slate-500">
                    {runtime.effect} - {runtime.kind} - {statusText(runtime)}
                  </div>
                </div>
                <div className="flex gap-1">
                  <button
                    type="button"
                    aria-label={`Refresh ${runtime.display_name}`}
                    onClick={() => void refresh(runtime.id)}
                    className="rounded bg-slate-700 px-2 py-1 text-[11px] text-slate-200"
                  >
                    Refresh
                  </button>
                  {runtime.kind === "container" && (
                    <>
                      <button
                        type="button"
                        onClick={() => void start(runtime.id)}
                        className="rounded bg-emerald-700 px-2 py-1 text-[11px] text-white"
                      >
                        Start
                      </button>
                      <button
                        type="button"
                        onClick={() => void stop(runtime.id)}
                        className="rounded bg-red-800 px-2 py-1 text-[11px] text-white"
                      >
                        Stop
                      </button>
                    </>
                  )}
                </div>
              </div>
            </article>
          ))
        )}
      </div>
    </section>
  );
}
```

- [ ] **Step 4: Wire into Assemble tools**

In `apps/desktop/src/components/AssembleView.tsx`, import and render before the existing KI-Status block:

```tsx
import { RuntimeStatusPanel } from "./RuntimeStatusPanel";
```

Inside the Tools rail:

```tsx
<RuntimeStatusPanel client={client} />
```

- [ ] **Step 5: Run GREEN**

```powershell
pnpm --dir apps/desktop test -- src/components/RuntimeStatusPanel.test.tsx src/components/AssembleView.test.tsx
pnpm --dir apps/desktop exec tsc --noEmit
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add apps/desktop/src/components/RuntimeStatusPanel.tsx apps/desktop/src/components/RuntimeStatusPanel.test.tsx apps/desktop/src/components/AssembleView.tsx
git commit -m "feat: show AI runtime status"
```

### Task 7: Runtime Setup Assistant UI

**Files:**
- Create: `apps/desktop/src/components/RuntimeSetupPanel.tsx`
- Create: `apps/desktop/src/components/RuntimeSetupPanel.test.tsx`
- Modify: `apps/desktop/src/components/AssembleView.tsx`

**Interfaces:**
- Consumes: `createAiRuntime`, `refreshAiRuntime`.
- Produces: `RuntimeSetupPanel({ client, onCreated }: { client: LauraClient; onCreated?: () => void })`.

- [ ] **Step 1: Write failing setup UI tests**

Create `apps/desktop/src/components/RuntimeSetupPanel.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { type LauraClient } from "../api";
import { RuntimeSetupPanel } from "./RuntimeSetupPanel";

function client(overrides: Partial<LauraClient> = {}): LauraClient {
  return {
    createAiRuntime: vi.fn().mockResolvedValue({ id: "rt-1" }),
    refreshAiRuntime: vi.fn().mockResolvedValue({ id: "rt-1" }),
    ...overrides,
  } as unknown as LauraClient;
}

describe("RuntimeSetupPanel", () => {
  it("creates an external HTTP runtime and refreshes it", async () => {
    const createAiRuntime = vi.fn().mockResolvedValue({ id: "rt-1" });
    const refreshAiRuntime = vi.fn().mockResolvedValue({ id: "rt-1" });
    const onCreated = vi.fn();
    render(
      <RuntimeSetupPanel
        client={client({ createAiRuntime, refreshAiRuntime } as Partial<LauraClient>)}
        onCreated={onCreated}
      />,
    );

    fireEvent.change(screen.getByLabelText("Runtime-Name"), {
      target: { value: "Local Lipsync" },
    });
    fireEvent.change(screen.getByLabelText("Runtime-Art"), {
      target: { value: "external_http" },
    });
    fireEvent.change(screen.getByLabelText("Effekt"), {
      target: { value: "lipsync" },
    });
    fireEvent.change(screen.getByLabelText("Base-URL"), {
      target: { value: "http://127.0.0.1:8901" },
    });
    fireEvent.click(screen.getByRole("button", { name: /runtime registrieren/i }));

    await waitFor(() =>
      expect(createAiRuntime).toHaveBeenCalledWith(
        expect.objectContaining({
          kind: "external_http",
          effect: "lipsync",
          displayName: "Local Lipsync",
          baseUrl: "http://127.0.0.1:8901",
        }),
      ),
    );
    expect(refreshAiRuntime).toHaveBeenCalledWith("rt-1");
    expect(onCreated).toHaveBeenCalled();
  });

  it("creates a container runtime with image, port, GPU and model path", async () => {
    const createAiRuntime = vi.fn().mockResolvedValue({ id: "rt-2" });
    render(<RuntimeSetupPanel client={client({ createAiRuntime } as Partial<LauraClient>)} />);

    fireEvent.change(screen.getByLabelText("Runtime-Art"), {
      target: { value: "container" },
    });
    fireEvent.change(screen.getByLabelText("Runtime-Name"), {
      target: { value: "LivePortrait" },
    });
    fireEvent.change(screen.getByLabelText("Effekt"), {
      target: { value: "reenact" },
    });
    fireEvent.change(screen.getByLabelText("Container-Image"), {
      target: { value: "laura-runtime-liveportrait:local" },
    });
    fireEvent.change(screen.getByLabelText("Port"), {
      target: { value: "8899" },
    });
    fireEvent.change(screen.getByLabelText("Modellpfad"), {
      target: { value: "E:/LauraModels/liveportrait" },
    });
    fireEvent.click(screen.getByLabelText("GPU verwenden"));
    fireEvent.click(screen.getByLabelText("Lizenz akzeptiert"));
    fireEvent.click(screen.getByRole("button", { name: /runtime registrieren/i }));

    await waitFor(() =>
      expect(createAiRuntime).toHaveBeenCalledWith(
        expect.objectContaining({
          kind: "container",
          effect: "reenact",
          displayName: "LivePortrait",
          containerImage: "laura-runtime-liveportrait:local",
          containerName: "laura-reenact-liveportrait",
          port: 8899,
          modelMount: "E:/LauraModels/liveportrait",
          requiresGpu: true,
          licenseStatus: "accepted",
        }),
      ),
    );
  });
});
```

- [ ] **Step 2: Run RED**

```powershell
pnpm --dir apps/desktop test -- src/components/RuntimeSetupPanel.test.tsx
```

Expected: FAIL because component does not exist.

- [ ] **Step 3: Implement setup component**

Create `apps/desktop/src/components/RuntimeSetupPanel.tsx`:

```tsx
import { type ReactElement, useState } from "react";
import {
  type LauraClient,
  type RuntimeEffect,
  type RuntimeKind,
} from "../api";

const EFFECTS: RuntimeEffect[] = ["voice", "reenact", "lipsync", "faceswap", "restore"];
const KINDS: RuntimeKind[] = ["stub", "external_http", "container"];

function defaultContainerName(effect: RuntimeEffect, name: string): string {
  const slug = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
  return `laura-${effect}${slug ? `-${slug}` : ""}`;
}

export function RuntimeSetupPanel({
  client,
  onCreated,
}: {
  client: LauraClient;
  onCreated?: () => void;
}): ReactElement {
  const [kind, setKind] = useState<RuntimeKind>("stub");
  const [effect, setEffect] = useState<RuntimeEffect>("voice");
  const [displayName, setDisplayName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [containerImage, setContainerImage] = useState("");
  const [port, setPort] = useState("");
  const [modelMount, setModelMount] = useState("");
  const [requiresGpu, setRequiresGpu] = useState(false);
  const [licenseAccepted, setLicenseAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(): Promise<void> {
    if (displayName.trim() === "") return;
    setBusy(true);
    setError(null);
    try {
      const runtime = await client.createAiRuntime({
        kind,
        effect,
        displayName: displayName.trim(),
        baseUrl: kind === "external_http" && baseUrl.trim() ? baseUrl.trim() : undefined,
        containerImage: kind === "container" && containerImage.trim() ? containerImage.trim() : undefined,
        containerName:
          kind === "container" ? defaultContainerName(effect, displayName) : undefined,
        port: port.trim() ? Number(port) : undefined,
        modelMount: kind === "container" && modelMount.trim() ? modelMount.trim() : undefined,
        requiresGpu: kind === "container" ? requiresGpu : false,
        licenseStatus: licenseAccepted ? "accepted" : kind === "stub" ? "not_required" : "unknown",
      });
      await client.refreshAiRuntime(runtime.id);
      setDisplayName("");
      setBaseUrl("");
      setContainerImage("");
      setPort("");
      setModelMount("");
      setRequiresGpu(false);
      setLicenseAccepted(false);
      onCreated?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded border border-edge bg-panel/50 p-3">
      <div className="mb-2 text-xs font-semibold text-slate-200">Runtime Setup</div>
      {error !== null && <div className="mb-2 text-xs text-red-400">{error}</div>}
      <div className="flex flex-col gap-2">
        <label className="flex flex-col gap-1 text-[11px] text-slate-400">
          Runtime-Name
          <input
            aria-label="Runtime-Name"
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            className="rounded border border-edge bg-ink px-2 py-1 text-xs text-slate-100"
          />
        </label>
        <div className="grid grid-cols-2 gap-2">
          <label className="flex flex-col gap-1 text-[11px] text-slate-400">
            Runtime-Art
            <select
              aria-label="Runtime-Art"
              value={kind}
              onChange={(event) => setKind(event.target.value as RuntimeKind)}
              className="rounded border border-edge bg-ink px-2 py-1 text-xs text-slate-100"
            >
              {KINDS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-[11px] text-slate-400">
            Effekt
            <select
              aria-label="Effekt"
              value={effect}
              onChange={(event) => setEffect(event.target.value as RuntimeEffect)}
              className="rounded border border-edge bg-ink px-2 py-1 text-xs text-slate-100"
            >
              {EFFECTS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
        </div>
        {kind === "external_http" && (
          <label className="flex flex-col gap-1 text-[11px] text-slate-400">
            Base-URL
            <input
              aria-label="Base-URL"
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              className="rounded border border-edge bg-ink px-2 py-1 text-xs text-slate-100"
            />
          </label>
        )}
        {kind === "container" && (
          <>
            <label className="flex flex-col gap-1 text-[11px] text-slate-400">
              Container-Image
              <input
                aria-label="Container-Image"
                value={containerImage}
                onChange={(event) => setContainerImage(event.target.value)}
                className="rounded border border-edge bg-ink px-2 py-1 text-xs text-slate-100"
              />
            </label>
            <div className="grid grid-cols-2 gap-2">
              <label className="flex flex-col gap-1 text-[11px] text-slate-400">
                Port
                <input
                  aria-label="Port"
                  value={port}
                  onChange={(event) => setPort(event.target.value)}
                  inputMode="numeric"
                  className="rounded border border-edge bg-ink px-2 py-1 text-xs text-slate-100"
                />
              </label>
              <label className="flex flex-col gap-1 text-[11px] text-slate-400">
                Modellpfad
                <input
                  aria-label="Modellpfad"
                  value={modelMount}
                  onChange={(event) => setModelMount(event.target.value)}
                  className="rounded border border-edge bg-ink px-2 py-1 text-xs text-slate-100"
                />
              </label>
            </div>
            <label className="flex items-center gap-2 text-xs text-slate-300">
              <input
                aria-label="GPU verwenden"
                type="checkbox"
                checked={requiresGpu}
                onChange={(event) => setRequiresGpu(event.target.checked)}
              />
              GPU verwenden
            </label>
          </>
        )}
        <label className="flex items-center gap-2 text-xs text-slate-300">
          <input
            aria-label="Lizenz akzeptiert"
            type="checkbox"
            checked={licenseAccepted}
            onChange={(event) => setLicenseAccepted(event.target.checked)}
          />
          Lizenz akzeptiert
        </label>
        <button
          type="button"
          onClick={() => void submit()}
          disabled={busy || displayName.trim() === ""}
          className="rounded bg-sky-700 px-3 py-1 text-xs font-medium text-white disabled:opacity-40"
        >
          {busy ? "Registriert..." : "Runtime registrieren"}
        </button>
      </div>
    </section>
  );
}
```

- [ ] **Step 4: Wire into Assemble tools**

In `apps/desktop/src/components/AssembleView.tsx`, import:

```tsx
import { RuntimeSetupPanel } from "./RuntimeSetupPanel";
```

Render directly below `RuntimeStatusPanel`:

```tsx
const [runtimeReloadKey, setRuntimeReloadKey] = useState(0);
```

Replace the status panel call:

```tsx
<RuntimeStatusPanel client={client} reloadKey={runtimeReloadKey} />
```

Render the setup panel directly below it:

```tsx
<RuntimeSetupPanel client={client} onCreated={() => setRuntimeReloadKey((key) => key + 1)} />
```

- [ ] **Step 5: Run GREEN**

```powershell
pnpm --dir apps/desktop test -- src/components/RuntimeSetupPanel.test.tsx src/components/AssembleView.test.tsx
pnpm --dir apps/desktop exec tsc --noEmit
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add apps/desktop/src/components/RuntimeSetupPanel.tsx apps/desktop/src/components/RuntimeSetupPanel.test.tsx apps/desktop/src/components/AssembleView.tsx
git commit -m "feat: add AI runtime setup panel"
```

### Task 8: Persona Kit UI

**Files:**
- Create: `apps/desktop/src/components/PersonaKitPanel.tsx`
- Create: `apps/desktop/src/components/PersonaKitPanel.test.tsx`
- Modify: `apps/desktop/src/components/AssembleView.tsx`

**Interfaces:**
- Consumes: `createAiPersona`, `listAiPersonas`, `createConsent`, `listAiRuntimes`.
- Produces: `PersonaKitPanel({ client, projectId }: { client: LauraClient; projectId: string | null })`.

- [ ] **Step 1: Write failing UI tests**

Create `apps/desktop/src/components/PersonaKitPanel.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { type LauraClient } from "../api";
import { PersonaKitPanel } from "./PersonaKitPanel";

function client(): LauraClient {
  return {
    listAiPersonas: vi.fn().mockResolvedValue([]),
    listAiRuntimes: vi.fn().mockResolvedValue([
      {
        id: "rt-voice",
        effect: "voice",
        kind: "stub",
        display_name: "Stub Voice",
        status: {},
        capabilities: {},
        base_url: null,
        container_image: null,
        container_name: null,
        port: null,
        workspace_mount: null,
        model_mount: null,
        requires_gpu: false,
        enabled: true,
        license_status: "not_required",
        last_health_at: null,
        created_at: "",
        updated_at: "",
      },
    ]),
    createConsent: vi.fn().mockResolvedValue({
      id: "consent-1",
      project_id: "project-1",
      subject_label: "Persona",
      confirmed_at: "",
      confirmed_by: null,
      source_asset_id: null,
      note: null,
      revoked_at: null,
    }),
    createAiPersona: vi.fn().mockResolvedValue({ id: "persona-1" }),
  } as unknown as LauraClient;
}

describe("PersonaKitPanel", () => {
  it("creates consent and persona", async () => {
    const c = client();
    render(<PersonaKitPanel client={c} projectId="project-1" />);
    fireEvent.change(await screen.findByLabelText("Persona-Name"), {
      target: { value: "Persona" },
    });
    fireEvent.click(screen.getByRole("checkbox", { name: /voice/i }));
    fireEvent.click(screen.getByRole("button", { name: /persona erstellen/i }));

    await waitFor(() => expect(c.createConsent).toHaveBeenCalledWith("project-1", {
      subjectLabel: "Persona",
    }));
    expect(c.createAiPersona).toHaveBeenCalledWith(
      expect.objectContaining({
        projectId: "project-1",
        name: "Persona",
        consentId: "consent-1",
        allowedEffects: ["voice"],
      }),
    );
  });
});
```

- [ ] **Step 2: Run RED**

```powershell
pnpm --dir apps/desktop test -- src/components/PersonaKitPanel.test.tsx
```

Expected: FAIL because component does not exist.

- [ ] **Step 3: Implement component**

Create `apps/desktop/src/components/PersonaKitPanel.tsx`:

```tsx
import { type ReactElement, useEffect, useState } from "react";
import {
  type AiPersona,
  type AiRuntime,
  type LauraClient,
  type RuntimeEffect,
} from "../api";

const EFFECTS: RuntimeEffect[] = ["voice", "reenact", "lipsync", "faceswap"];

export function PersonaKitPanel({
  client,
  projectId,
}: {
  client: LauraClient;
  projectId: string | null;
}): ReactElement {
  const [personas, setPersonas] = useState<AiPersona[]>([]);
  const [runtimes, setRuntimes] = useState<AiRuntime[]>([]);
  const [name, setName] = useState("");
  const [effects, setEffects] = useState<RuntimeEffect[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function load(): Promise<void> {
    if (projectId === null) {
      setPersonas([]);
      setRuntimes([]);
      return;
    }
    const [nextPersonas, nextRuntimes] = await Promise.all([
      client.listAiPersonas(projectId),
      client.listAiRuntimes(),
    ]);
    setPersonas(nextPersonas);
    setRuntimes(nextRuntimes);
  }

  useEffect(() => {
    void load().catch((err: unknown) => {
      setError(err instanceof Error ? err.message : String(err));
    });
  }, [client, projectId]);

  function toggle(effect: RuntimeEffect): void {
    setEffects((current) =>
      current.includes(effect) ? current.filter((item) => item !== effect) : [...current, effect],
    );
  }

  async function create(): Promise<void> {
    if (projectId === null || name.trim() === "") return;
    try {
      const consent = await client.createConsent(projectId, { subjectLabel: name.trim() });
      const preferredRuntimes: Record<string, string> = {};
      for (const effect of effects) {
        const runtime = runtimes.find((candidate) => candidate.effect === effect);
        if (runtime) preferredRuntimes[effect] = runtime.id;
      }
      await client.createAiPersona({
        projectId,
        name: name.trim(),
        consentId: consent.id,
        allowedEffects: effects,
        preferredRuntimes,
      });
      setName("");
      setEffects([]);
      await load();
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <section className="rounded border border-edge bg-panel/50 p-3">
      <div className="mb-2 text-xs font-semibold text-slate-200">AI Persona Kit</div>
      {error !== null && <div className="mb-2 text-xs text-red-400">{error}</div>}
      <div className="flex flex-col gap-2">
        <input
          aria-label="Persona-Name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          disabled={projectId === null}
          placeholder="Persona-Name"
          className="rounded border border-edge bg-ink px-2 py-1 text-xs text-slate-100"
        />
        <div className="grid grid-cols-2 gap-1">
          {EFFECTS.map((effect) => (
            <label key={effect} className="flex items-center gap-2 text-xs text-slate-300">
              <input
                type="checkbox"
                checked={effects.includes(effect)}
                onChange={() => toggle(effect)}
              />
              {effect}
            </label>
          ))}
        </div>
        <button
          type="button"
          onClick={() => void create()}
          disabled={projectId === null || name.trim() === ""}
          className="rounded bg-sky-700 px-3 py-1 text-xs font-medium text-white disabled:opacity-40"
        >
          Persona erstellen
        </button>
      </div>
      <div className="mt-3 flex flex-col gap-1">
        {personas.length === 0 ? (
          <div className="text-xs text-slate-500">Noch keine Persona.</div>
        ) : (
          personas.map((persona) => (
            <div key={persona.id} className="rounded border border-edge bg-ink/60 p-2">
              <div className="text-xs font-medium text-slate-100">{persona.name}</div>
              <div className="text-[11px] text-slate-500">
                {persona.allowed_effects.length ? persona.allowed_effects.join(", ") : "keine Effekte"}
              </div>
            </div>
          ))
        )}
      </div>
    </section>
  );
}
```

- [ ] **Step 4: Wire into Assemble tools**

In `apps/desktop/src/components/AssembleView.tsx`:

```tsx
import { PersonaKitPanel } from "./PersonaKitPanel";
```

Render below `RuntimeStatusPanel`:

```tsx
<PersonaKitPanel client={client} projectId={projectId} />
```

- [ ] **Step 5: Run GREEN**

```powershell
pnpm --dir apps/desktop test -- src/components/PersonaKitPanel.test.tsx src/components/AssembleView.test.tsx
pnpm --dir apps/desktop exec tsc --noEmit
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add apps/desktop/src/components/PersonaKitPanel.tsx apps/desktop/src/components/PersonaKitPanel.test.tsx apps/desktop/src/components/AssembleView.tsx
git commit -m "feat: add AI persona kit panel"
```

### Task 9: Full Verification and Todo Update

**Files:**
- Modify: `tasks/todo.md`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: tracked project status for AI Persona Foundation.

- [ ] **Step 1: Add task status to `tasks/todo.md`**

Add after `VV7 AI Provenance Inspector v1`:

```markdown
- [x] **APF1 AI Persona Foundation Design + Runtime Base** - Container-first
  Persona-Fundament geplant und umgesetzt: Runtime Registry (`stub`/`external_http`/`container`),
  Health/Capability-Refresh, Container Start/Stop/Events, Persona-Kit mit Consent und bevorzugten
  Runtimes, sowie `runtime_id`-Routing fuer bestehende Voice/Reenact/Lipsync-Jobs. Echte Modell-
  Container bleiben optional und werden ueber Setup/Runtime-Status aktiviert.
```

- [ ] **Step 2: Run full backend checks**

```powershell
cd services/local-api
uv run pytest -q
uv run ruff check .
uv run mypy src
```

Expected: pytest/ruff/mypy pass. If full `mypy src tests` is still red from known test typing debt, do not use it as the gate; use `uv run mypy src`.

- [ ] **Step 3: Run full desktop checks**

```powershell
pnpm --dir apps/desktop test
pnpm --dir apps/desktop exec tsc --noEmit
pnpm --dir apps/desktop run build:renderer
```

Expected: all pass.

- [ ] **Step 4: Commit verification status**

```powershell
git add tasks/todo.md
git commit -m "docs: mark AI persona foundation complete"
```

## Execution Notes

- Do not install Docker images or model weights during these tasks.
- Do not add Docker SDK dependencies; the first adapter is CLI-based and mockable.
- Do not route real model URLs through global env only; `runtime_id` is the future source of truth.
- Keep `backend` support until all existing UI callers are migrated.
- The next plan after this foundation should implement the actual MVP chain:
  `Persona + Text + Timeline Range -> Voice WAV -> Lipsync MP4 -> Replace-Overlay`.
