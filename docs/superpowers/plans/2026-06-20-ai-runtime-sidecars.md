# AI Runtime Sidecars Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and start real Docker sidecar images for Laura's LivePortrait, VibeVideo/Lipsync, and Voice runtimes without adding heavy model dependencies to the Laura core.

**Architecture:** Laura keeps the existing `ai_runtimes` registry and HTTP adapters as the integration boundary. Each sidecar image runs a small stdlib HTTP service that exposes `/healthz`, `/version`, `/capabilities`, and one effect endpoint; in `model` mode it shells out to mounted model code/commands, and in explicit `smoke` mode it returns deterministic non-model media for container and contract tests. Docker start supports runtime environment variables and normalizes host-only model/workspace mount paths to stable container mount points.

**Tech Stack:** FastAPI/SQLite registry in `services/local-api`, stdlib Python runtime server in `services/ai-runtimes`, Docker/Compose, PowerShell/Bash helper scripts, pytest, ruff, mypy, Vitest/tsc.

## Global Constraints

- No model weights or gated model repos in Git.
- Laura core must not import Torch/CUDA/LivePortrait/VibeVideo/voice model libraries.
- Sidecar outputs remain verified by existing frame/sample sync guards before asset registration.
- Container health must distinguish `model` mode readiness from explicit `smoke` mode readiness.
- Docker/GPU/model absence must not prevent the local API or desktop app from starting.
- TypeScript remains strict with no `any`; Python remains ruff/mypy clean for touched Laura core files.

---

## File Structure

- Modify `services/local-api/src/laura/db/migrations/0024_ai_runtime_container_env.sql`
  - Adds `container_env_json` for per-runtime Docker environment variables.
- Modify `services/local-api/src/laura/db/repos.py`, `services/local-api/src/laura/api/models.py`, `apps/desktop/src/api.ts`
  - Round-trip `container_env` through storage/API/client types.
- Modify `services/local-api/src/laura/ai/docker_runtime.py`
  - Pass safe env vars to `docker run`; normalize host-only `workspace_mount` and `model_mount`.
- Add `services/local-api/tests/test_ai_runtime_container_env.py`
  - Covers env persistence and Docker CLI args without invoking Docker.
- Add `services/ai-runtimes/runtime_server.py`
  - Shared HTTP sidecar server for `liveportrait`, `vibevideo`, and `voice`.
- Add `services/ai-runtimes/tests/test_runtime_server.py`
  - Starts in-process servers and verifies health/capabilities/effect endpoints.
- Add `services/ai-runtimes/Dockerfile`, `.dockerignore`, `README.md`
  - Builds model-ready images from the shared server.
- Add `deploy/ai-runtimes/docker-compose.yml`, `deploy/ai-runtimes/docker-compose.gpu.yml`
  - Builds/starts the three sidecars with explicit smoke defaults and optional GPU override.
- Add `scripts/ai-runtimes.ps1`, `scripts/ai-runtimes.sh`, `scripts/register-ai-sidecars.ps1`
  - Build/start/stop/health and register sidecars against the local API.
- Modify `tasks/todo.md`
  - Mark the sidecar slice with exact verification evidence only after checks pass.

---

### Task 1: Container Env + Mount Normalization

**Files:**
- Create: `services/local-api/src/laura/db/migrations/0024_ai_runtime_container_env.sql`
- Modify: `services/local-api/src/laura/db/repos.py`
- Modify: `services/local-api/src/laura/api/models.py`
- Modify: `services/local-api/src/laura/ai/docker_runtime.py`
- Modify: `apps/desktop/src/api.ts`
- Test: `services/local-api/tests/test_ai_runtime_container_env.py`
- Test: `apps/desktop/src/api.test.ts`

**Interfaces:**
- `create_ai_runtime(..., container_env: dict[str, str] | None = None) -> dict[str, Any]`
- `AiRuntimeCreate.container_env: dict[str, str]`
- `AiRuntimeOut.container_env: dict[str, str]`
- `AiRuntime.container_env: Record<string, string>`
- `AiRuntimeCreate.containerEnv?: Record<string, string>`
- Docker run adds `-e KEY=VALUE` for uppercase env keys matching `[A-Z_][A-Z0-9_]*`.
- Host-only `model_mount` maps to `/models:ro`; host-only `workspace_mount` maps to `/workspace`.

- [ ] **Step 1: Write RED backend tests**

Create tests proving:

```python
def test_ai_runtime_round_trips_container_env(tmp_path):
    # create container runtime with container_env={"LAURA_RUNTIME_MODE": "smoke"}
    # assert loaded runtime exposes the same dict
```

```python
def test_docker_adapter_passes_container_env_and_normalizes_mounts(monkeypatch):
    # fake subprocess.run
    # create runtime with model_mount="E:/LauraModels/liveportrait"
    # create runtime with workspace_mount="C:/Laura/workspace"
    # assert docker run contains "-e", "LAURA_RUNTIME_MODE=smoke"
    # assert docker run contains "-v", "E:/LauraModels/liveportrait:/models:ro"
    # assert docker run contains "-v", "C:/Laura/workspace:/workspace"
```

- [ ] **Step 2: Run RED**

Run:

```powershell
cd services/local-api
uv run pytest tests/test_ai_runtime_container_env.py -q
```

Expected: FAIL because `container_env` does not exist.

- [ ] **Step 3: Implement storage/API/client and Docker args**

Add the migration, repo JSON encode/decode, Pydantic field, TypeScript field mapping, and Docker adapter helpers.

- [ ] **Step 4: Run GREEN**

Run:

```powershell
cd services/local-api
uv run pytest tests/test_ai_runtime_container_env.py tests/test_ai_runtime_manager.py tests/test_ai_runtime_api.py -q
uv run ruff check src/laura/ai/docker_runtime.py src/laura/db/repos.py src/laura/api/models.py tests/test_ai_runtime_container_env.py
uv run mypy src/laura/ai/docker_runtime.py src/laura/api/models.py
pnpm --dir apps/desktop test -- src/api.test.ts
pnpm --dir apps/desktop exec tsc --noEmit
```

Expected: PASS.

### Task 2: Shared Sidecar Runtime Server

**Files:**
- Create: `services/ai-runtimes/runtime_server.py`
- Create: `services/ai-runtimes/tests/test_runtime_server.py`
- Create: `services/ai-runtimes/README.md`

**Interfaces:**
- `GET /healthz`
- `GET /version`
- `GET /capabilities`
- `POST /reenact` for `LAURA_RUNTIME_KIND=liveportrait`
- `POST /probe` and `POST /lipsync` for `LAURA_RUNTIME_KIND=vibevideo`
- `POST /voiceover` for `LAURA_RUNTIME_KIND=voice`

- [ ] **Step 1: Write RED server tests**

Tests start `ThreadingHTTPServer(("127.0.0.1", 0), build_handler(config))` and verify:

```python
def test_voice_smoke_health_capabilities_and_voiceover():
    # mode=smoke, kind=voice
    # /healthz ready true, /capabilities effect voice
    # POST /voiceover returns WAV bytes beginning with RIFF
```

```python
def test_liveportrait_model_mode_not_ready_without_command():
    # mode=model, no command
    # /healthz ready false with clear message
```

```python
def test_lipsync_smoke_probe_and_lipsync_contract():
    # mode=smoke, kind=vibevideo
    # /probe returns face/audio booleans
    # /lipsync returns uploaded video bytes and X-Laura-Quality
```

- [ ] **Step 2: Run RED**

Run:

```powershell
cd services/local-api
uv run pytest ..\ai-runtimes\tests\test_runtime_server.py -q
```

Expected: FAIL because the server module does not exist.

- [ ] **Step 3: Implement server**

Use only stdlib modules: `http.server`, `json`, `email.parser`, `tempfile`, `subprocess`, `wave`, and `logging`. Model mode validates configured commands and executes them with deterministic placeholders expanded into real paths. Smoke mode is explicit and labelled in health/capabilities.

- [ ] **Step 4: Run GREEN**

Run:

```powershell
cd services/local-api
uv run pytest ..\ai-runtimes\tests\test_runtime_server.py -q
uv run ruff check ..\ai-runtimes\runtime_server.py ..\ai-runtimes\tests\test_runtime_server.py
```

Expected: PASS.

### Task 3: Docker Images + Compose + Scripts

**Files:**
- Create: `services/ai-runtimes/Dockerfile`
- Create: `services/ai-runtimes/.dockerignore`
- Create: `deploy/ai-runtimes/docker-compose.yml`
- Create: `deploy/ai-runtimes/docker-compose.gpu.yml`
- Create: `scripts/ai-runtimes.ps1`
- Create: `scripts/ai-runtimes.sh`
- Create: `scripts/register-ai-sidecars.ps1`
- Modify: `tasks/todo.md`

**Interfaces:**
- Images:
  - `laura-runtime-liveportrait:local`
  - `laura-runtime-vibevideo:local`
  - `laura-runtime-voice:local`
- Ports:
  - LivePortrait/Reenact: `8899`
  - VibeVideo/Lipsync: `8901`
  - Voice: `8898`
- Compose command:
  - `docker compose -f deploy/ai-runtimes/docker-compose.yml up -d --build`
- Registration script creates container runtimes with `container_env={"LAURA_RUNTIME_MODE":"smoke"}` unless overridden.

- [ ] **Step 1: Add files**

Dockerfile builds one shared server image and sets runtime kind by build arg. Compose builds all three images, mounts `${LAURA_RUNTIME_WORKSPACE:-./workspace/ai-runtime}` and `${LAURA_MODELS_ROOT:-./workspace/models}`, and uses smoke mode by default for local readiness.

- [ ] **Step 2: Add docs and todo status**

`README.md` must include:
- smoke mode vs model mode
- LivePortrait command contract
- VibeVideo/Lipsync command contract
- Voice command contract
- no weights in Git
- Docker Desktop GPU override

- [ ] **Step 3: Verify scripts lint lightly**

Run PowerShell parse check:

```powershell
[scriptblock]::Create((Get-Content scripts\ai-runtimes.ps1 -Raw)) | Out-Null
[scriptblock]::Create((Get-Content scripts\register-ai-sidecars.ps1 -Raw)) | Out-Null
```

Expected: no parser errors.

### Task 4: Docker Build/Start Smoke

**Files:** no code changes unless verification exposes bugs.

- [ ] **Step 1: Build images**

Run:

```powershell
docker compose -f deploy/ai-runtimes/docker-compose.yml build
```

Expected: all three images build.

- [ ] **Step 2: Start images**

Run:

```powershell
docker compose -f deploy/ai-runtimes/docker-compose.yml up -d
docker compose -f deploy/ai-runtimes/docker-compose.yml ps
```

Expected: all three containers are running.

- [ ] **Step 3: Health smoke**

Run:

```powershell
Invoke-RestMethod http://127.0.0.1:8898/healthz
Invoke-RestMethod http://127.0.0.1:8899/healthz
Invoke-RestMethod http://127.0.0.1:8901/healthz
```

Expected: `ready: true`, `mode: smoke` for each.

- [ ] **Step 4: Full gates**

Run:

```powershell
cd services/local-api
uv run pytest -q
uv run ruff check .
uv run mypy src
cd ..\..
pnpm --dir apps/desktop test
pnpm --dir apps/desktop exec tsc --noEmit
pnpm --dir apps/desktop run build:renderer
git diff --check
```

Expected: PASS. If Docker is unavailable or GPU-specific checks fail, record the exact command and reason instead of claiming success.

### Task 5: Review, Commit, and Finish

**Files:** all touched files.

- [ ] **Step 1: Inspect final diff**

Run:

```powershell
git status --short
git diff --stat
```

- [ ] **Step 2: Commit**

Run:

```powershell
git add docs/superpowers/plans/2026-06-20-ai-runtime-sidecars.md services/local-api/src/laura/db/migrations/0024_ai_runtime_container_env.sql services/local-api/src/laura/db/repos.py services/local-api/src/laura/api/models.py services/local-api/src/laura/ai/docker_runtime.py services/local-api/tests/test_ai_runtime_container_env.py apps/desktop/src/api.ts apps/desktop/src/api.test.ts services/ai-runtimes deploy/ai-runtimes scripts/ai-runtimes.ps1 scripts/ai-runtimes.sh scripts/register-ai-sidecars.ps1 tasks/todo.md
git commit -m "feat: add AI runtime sidecar containers"
```

- [ ] **Step 3: Report status**

Report:
- commit hash
- exact checks run
- Docker containers/images started
- remaining real-model work: mount model repos/weights and configure model commands.
