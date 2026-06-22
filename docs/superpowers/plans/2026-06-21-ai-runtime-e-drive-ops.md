# AI Runtime E-Drive Ops Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the real Voice/Piper, LivePortrait and MuseTalk sidecars usable from Laura's normal AI job flow while keeping model weights and caches on `E:`.

**Architecture:** Keep Laura's core process model-free. Run heavy engines as optional HTTP sidecars, mounted read-only from an external model root, and let the existing `backend` request fields route jobs to those sidecars. Add scripts, compose files, runtime server tests, and ops docs without merging unrelated Persona-Foundation or Transition branch changes.

**Tech Stack:** PowerShell scripts, Docker Compose, FastAPI sidecar runtime, Python 3.11 tests, existing Laura HTTP sidecar adapters.

## Global Constraints

- Do not overwrite the open `feat/laura-build-ready` retry/dedup changes in `apps/desktop/src/hooks/useJobStatus.ts` or `services/local-api/src/laura/api/{voiceover,reenact,lipsync}.py`.
- Default heavy model storage to `E:\Laura\models` when drive `E:` exists; fall back to `workspace\models` on portable hosts.
- Keep heavy model packages optional: Laura's API must import and boot without Docker, GPU, Torch or model folders.
- Voiceover, Reenact and Lipsync must still support the existing stub path.
- Live end-to-end testing happens only after the E:-root and sidecar start scripts are in place.

---

### Task 1: Runtime Server Tests

**Files:**
- Create: `services/ai-runtimes/tests/test_runtime_server.py`
- Create: `services/ai-runtimes/tests/test_provider_runners.py`

**Interfaces:**
- Consumes: none.
- Produces: tested sidecar contract: `GET /healthz`, `POST /voiceover`, `POST /reenact`, `POST /probe`, `POST /lipsync`.

- [ ] **Step 1: Write failing tests**

```powershell
uv run pytest services\ai-runtimes\tests -q
```

Expected before implementation: fails because `services/ai-runtimes` does not exist in this branch.

- [ ] **Step 2: Add sidecar runtime implementation**

Copy the minimal runtime server and provider runner contracts from `codex/ai-persona-foundation`, then adjust only where this branch's existing Laura sidecar adapters require it.

- [ ] **Step 3: Verify tests pass**

```powershell
uv run pytest services\ai-runtimes\tests -q
```

Expected after implementation: all sidecar runtime tests pass.

### Task 2: E-Drive Model Root and Setup

**Files:**
- Create: `scripts/setup-ai-runtime-models.ps1`
- Create: `deploy/ai-runtimes/docker-compose.yml`
- Create: `deploy/ai-runtimes/docker-compose.models.yml`
- Create: `services/ai-runtimes/README.md`

**Interfaces:**
- Consumes: `LAURA_MODELS_ROOT`, `HF_HOME`, `HUGGINGFACE_HUB_CACHE`, `UV_CACHE_DIR`.
- Produces: host model layout with `voice`, `liveportrait`, `vibevideo` folders mounted read-only into sidecars.

- [ ] **Step 1: Write failing script/compose smoke checks**

```powershell
Test-Path scripts\setup-ai-runtime-models.ps1
docker compose -f deploy\ai-runtimes\docker-compose.yml -f deploy\ai-runtimes\docker-compose.models.yml config --quiet
```

Expected before implementation: missing script and compose files.

- [ ] **Step 2: Implement setup and compose**

Default the setup script to `E:\Laura\models` when `E:` exists, set Hugging Face and uv caches to existing E: cache folders when present, and keep all model mounts read-only in compose.

- [ ] **Step 3: Verify smoke checks**

```powershell
docker compose -f deploy\ai-runtimes\docker-compose.yml -f deploy\ai-runtimes\docker-compose.models.yml config --quiet
```

Expected after implementation: compose config exits 0.

### Task 3: Official Sidecar Start Flow

**Files:**
- Create: `scripts/ai-runtimes.ps1`
- Create: `scripts/register-ai-sidecars.ps1`
- Create: `scripts/ai-runtimes.sh`

**Interfaces:**
- Consumes: Docker images `laura-runtime-voice:local`, `laura-runtime-liveportrait-model:local`, `laura-runtime-musetalk-model:local`.
- Produces: sidecars on `127.0.0.1:8898`, `127.0.0.1:8899`, `127.0.0.1:8901`, matching existing backend defaults.

- [ ] **Step 1: Write failing command checks**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register-ai-sidecars.ps1 -WhatIf
```

Expected before implementation: script missing.

- [ ] **Step 2: Implement start/stop/register scripts**

Use additive scripts only; do not require a running local API runtime registry. The current branch routes sidecars via existing `backend` fields and default URLs.

- [ ] **Step 3: Verify script help/config**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ai-runtimes.ps1 -Help
```

Expected after implementation: usage text and no side effects.

### Task 4: Live End-to-End Check

**Files:**
- No code file required unless tests expose a real branch mismatch.

**Interfaces:**
- Consumes: E:-mounted models and running sidecars.
- Produces: actual WAV/MP4 outputs from the three sidecars and one Laura job-flow test where feasible.

- [ ] **Step 1: Start model sidecars**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ai-runtimes.ps1 -Mode model -Action up
```

- [ ] **Step 2: Probe health**

```powershell
Invoke-WebRequest http://127.0.0.1:8898/healthz
Invoke-WebRequest http://127.0.0.1:8899/healthz
Invoke-WebRequest http://127.0.0.1:8901/healthz
```

- [ ] **Step 3: Run real output checks**

Call `/voiceover`, `/reenact`, `/probe` and `/lipsync` with local fixtures. Inspect output files with `ffprobe`.

### Task 5: Quality and Ops Hardening

**Files:**
- Modify: `services/ai-runtimes/runtime_server.py`
- Modify: `services/ai-runtimes/README.md`
- Modify: `tasks/todo.md`

**Interfaces:**
- Consumes: provider quality metadata.
- Produces: visible headers for quality/probe output, documented cleanup/restart/model-root procedure.

- [ ] **Step 1: Add tests for quality headers and failure codes**

```powershell
uv run pytest services\ai-runtimes\tests -q
```

- [ ] **Step 2: Implement quality/ops details**

Return `X-Laura-Quality`, provide clear JSON errors, and document model version/pinning and cleanup.

- [ ] **Step 3: Final verification**

```powershell
uv run pytest tests/test_ai_job_retry_dedup.py tests/test_voiceover.py tests/test_reenact_backend.py tests/test_reenact_job.py tests/test_lipsync_job.py -q
uv run ruff check src/laura/api/voiceover.py src/laura/api/reenact.py src/laura/api/lipsync.py src/laura/ai
uv run mypy src/laura
```
