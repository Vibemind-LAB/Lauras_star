# Laura ↔ VibeMind Integration v2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate Laura into VibeMind as a video-space submodule: backend as a native venv service in the start script, UI as an Electron BrowserView space, and control via an MCP server on the OpenFang hub.

**Architecture (from the owner-verified spec v2):** UI = Electron `BrowserView` (mirror `rowboat-manager.js`) — NOT Tauri. Backend = `Start-VenvPython` phase-3 line in `Vibemind_V1/scripts/vibemind-start.ps1`. Control = a FastMCP `laura_server.py` under `openfang/mcp/` wrapping Laura's REST :8765 — NOT a new event agent. Laura's own pipeline work is Phase 0 and lands first.

**Tech Stack:** PowerShell (start/stop), Git submodules, Python venv + FastMCP (MCP server), Node/Electron (BrowserView manager + preload). Laura backend = FastAPI :8765 (existing).

**Repos & roots:**
- Super-root: `C:\Users\User\Desktop\Vibemind_V1` (has `scripts/vibemind-start.ps1`).
- `Vibemind_V1\vibemind-os` (submodule host: `spaces/video/laura`, `voice/electron-app`, `openfang/mcp`).
- Laura: `C:\Users\User\Desktop\Laura` (remote `Vibemind-LAB/Lauras_star`).

**Spec:** the owner's verified v2 design (saved alongside as `2026-06-08-vibemind-laura-integration-v2-spec.md`).

**Cross-repo & manual reality:** Most tasks edit the VibeMind monorepo (PowerShell/Electron/Python) and are verified by **manual smoke** (no headless harness there); the MCP server gets pytest. Commit in the repo each file lives in. If `No space left on device`, STOP and report BLOCKED.

---

## PHASE 0 — Laura readiness (Laura repo) — GATE, do first

Covered by Laura's own plans; this integration is blocked until Phase 0 completes.
- [ ] **0a** Finish Laura's pipeline UI + the export/render→MP4 stage (see Laura `docs/superpowers/plans/2026-06-03-pipeline-foundation.md` + `2026-06-03-pipeline-export-stage.md`).
- [ ] **0b** Decouple the renderer from Electron `main.ts`: `window.laura.*` must come from an **injectable preload** (so a foreign host — VibeMind — can supply it). Concretely: the renderer must not assume Laura's own `main.ts`; it consumes a `LauraBridge` that a host preload provides.
- [ ] **0c** Clean working tree: commit/push open changes; add `.claude/` + `build/` to `.gitignore`.
- [ ] **0d** Verify headless: `uv run laura-api` starts on 127.0.0.1:8765, `/healthz` green (no Electron); `pnpm --filter desktop build` produces a reproducible renderer `dist`.
- **Gate:** a pin-able commit on `feat/pipeline-foundation` (or merged to `main`).

---

## PHASE 1 — Submodule + venv backend service

### Task 1.1: Add Laura as a submodule (pinned to the Phase-0 commit)
**Repo:** `vibemind-os`. **cwd:** `Vibemind_V1\vibemind-os`.
- [ ] **Step 1:** `git submodule add https://github.com/Vibemind-LAB/Lauras_star.git spaces/video/laura`
- [ ] **Step 2:** `cd spaces/video/laura && git checkout <phase-0 commit/tag> && cd -` to pin the verified commit.
- [ ] **Step 3:** `git add .gitmodules spaces/video/laura && git commit -m "$(printf 'feat(spaces): add Laura editorial editor submodule\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"`

### Task 1.2: Laura Python deps in a venv
**Repo:** `Vibemind_V1` (start infra).
- [ ] **Step 1:** Decide venv: reuse `$SHARED_VENV` or a dedicated `spaces/video/laura/.venv` (record the choice; dedicated keeps Laura's torch/ML isolated — recommended). Install Laura's `services/local-api` deps (`uv sync` in the submodule, or `pip install -e`), keeping ML extras optional (local-first invariant).
- [ ] **Step 2:** Verify `python -m laura.main` / `uv run laura-api` launches headless from the submodule path. Record exact command.

### Task 1.3: Start/stop wiring
**Repo:** `Vibemind_V1`. **Files:** `scripts/vibemind-start.ps1`, `scripts/vibemind-stop.ps1`.
- [ ] **Step 1: Read** the `Start-VenvPython` function (≈ lines 333–386) and an existing phase-3 usage (`la_fungus`, `bridge`, `automation_backend`) to match its exact idiom (`-Name -VenvPath -WorkDir -ScriptArgs -ExtraEnv -Port`, PID → `logs/services.registry.jsonl`).
- [ ] **Step 2: Add a phase-3 line** for Laura, mirroring the existing ones:
```powershell
# --- Laura editorial editor (video space) ---
$lauraToken = [guid]::NewGuid().ToString("N")
Set-Content -Path (Join-Path $RepoRoot 'logs/laura.token') -Value $lauraToken -NoNewline
Start-VenvPython -Name 'laura_api' `
  -VenvPath $LauraVenv `
  -WorkDir (Join-Path $RepoRoot 'vibemind-os\spaces\video\laura\services\local-api') `
  -ScriptArgs @('-m','uvicorn','laura.main:create_app','--factory','--host','127.0.0.1','--port','8765') `
  -ExtraEnv @{ LAURA_HOST='127.0.0.1'; LAURA_PORT='8765'; LAURA_TOKEN=$lauraToken; LAURA_WORKSPACE=(Join-Path $HOME '.rowboat\Laura'); LAURA_FFMPEG=$LauraFfmpeg; LAURA_FFPROBE=$LauraFfprobe } `
  -Port 8765
```
(Adapt `$LauraVenv`/`$LauraFfmpeg`/`$LauraFfprobe`/`$RepoRoot` to the script's actual variables; confirm the uvicorn factory invocation matches Laura's `create_app`. If Laura's console-script `laura-api` is preferred, use `-ScriptArgs @()` with the script entry instead.)
- [ ] **Step 3: Token-file contract** — the `logs/laura.token` write above is the single source consumers read (MCP server + BrowserView preload). Document the path in the script's header comment.
- [ ] **Step 4: Stop** — in `vibemind-stop.ps1`, add Laura to the by-port/cmdline kill (port 8765, pattern `laura\.main|local-api`).
- [ ] **Step 5: Health-gate** — add `:8765/healthz` (with `X-Laura-Token` from the token file) to phase-6 health checks.
- [ ] **Step 6: Manual verify** — run `scripts/vibemind-start.ps1`; confirm `logs/laura.token` written and `curl -H "X-Laura-Token: <tok>" http://127.0.0.1:8765/healthz` is green. Commit.

---

## PHASE 2 — Electron BrowserView space "Laura"

**Repo:** `vibemind-os`. **Files:** `voice/electron-app/laura-manager.js`, `voice/electron-app/laura-preload.js`, `voice/electron-app/renderer/space-navigator.js`, main process wiring.

### Task 2.1: `laura-manager.js` (BrowserView)
- [ ] **Step 1: Read** `voice/electron-app/rowboat-manager.js` fully (the BrowserView lifecycle: `_resolveRendererPath`, show/hide, `updateBounds`, dev vs prod load, `relayEvent`).
- [ ] **Step 2: Create** `laura-manager.js` mirroring it: a `LauraManager` that mounts Laura's renderer `dist` as a `BrowserView` (prod `loadFile(spaces/video/laura/apps/desktop/dist/index.html)`; dev `loadURL(process.env.LAURA_DEV_URL)`), same `topOffset`, resize handling, and a `laura-preload.js` as the `preload`. No bridge child-process is needed (Laura talks to its own :8765 over HTTP), so drop rowboat's bridge plumbing.
- [ ] **Step 3:** Typecheck/lint per the electron-app's tooling; commit.

### Task 2.2: `laura-preload.js` (the `window.laura` bridge in VibeMind)
- [ ] **Step 1: Implement** `laura-preload.js` exposing `window.laura` with the SAME shape Laura's renderer expects (`getServiceInfo`, `pickMediaFile(s)`, `pickFolder`, `listMediaInFolder`, `saveTextFile`, `pathForFile`):
  - `getServiceInfo` → read `logs/laura.token` + return `{ baseUrl: "http://127.0.0.1:8765", token }`.
  - file pickers → VibeMind main-process IPC using Electron `dialog` (add the IPC handlers in the electron-app main if not present).
  - `pathForFile` → Electron `File.path` (Electron renderer has it) or webUtils.
- [ ] **Step 2: `laura-media://`** — register the protocol in the VibeMind electron main to range-stream from Laura's workspace, OR point the renderer at Laura's `:8765` proxy endpoint (decide; simplest = use the backend HTTP proxy URL). Document the choice.
- [ ] **Step 3:** Commit.

### Task 2.3: Register the space
- [ ] **Step 1:** In `renderer/space-navigator.js`, register the "laura" space (or a sub-tab under the existing "Video Studio") wired to `LauraManager`. Mirror how rowboat/video spaces are registered.
- [ ] **Step 2: Manual verify** — open the "Laura" space: renderer loads, imports a URL, edits a timeline, exports — all against the phase-1 backend. Commit.

---

## PHASE 3 — MCP server on the OpenFang hub

**Repo:** `vibemind-os`. **Files:** `openfang/mcp/laura_server.py`, `openfang/mcp/test_laura_server.py`, hub registration.

### Task 3.1: `laura_server.py` (FastMCP wrapping Laura REST)
- [ ] **Step 1: Read** an existing server under `openfang/mcp/` (e.g. `openfang_agents_server.py`) to match the FastMCP idiom (server construction, `@tool` decorators, transport, how the stdio bridge consumes it).
- [ ] **Step 2: Write failing tests** — `test_laura_server.py` (mock httpx): each tool posts/gets the right Laura endpoint and returns structured output. Cover `laura_import` (→ `/projects/{id}/assets/import`), `laura_status` (→ `/assets/{id}/import-status`), `laura_export` (→ `/timelines/{id}/exports`).
- [ ] **Step 3: Implement** `laura_server.py`:
  - Read `LAURA_BASE_URL` (default `http://127.0.0.1:8765`) + token from `logs/laura.token` at call time (token rotates per start).
  - Tools: `laura_project_create`, `laura_import`, `laura_analyze`, `laura_timeline_op`, `laura_export`, `laura_status` — thin httpx wrappers returning `{success, message, data}`, matching the FastMCP server pattern from Step 1.
- [ ] **Step 4: Run tests → pass; commit.**

### Task 3.2: Register on the hub
- [ ] **Step 1:** Register `laura_server` with the OpenFang MCP hub (:4200/mcp) per the hub's config/registration mechanism (read how `openfang/scripts/mcp_stdio_bridge.py` + the hub discover servers).
- [ ] **Step 2: E2E manual** — call `laura_status` and `laura_import` through the hub → REST → Laura job → result. Commit.

---

## PHASE 4 — Voice/Intent (OPTIONAL, later)
Out of scope for iteration 1 (recorded in the spec). Later: add a VIDEO/LAURA section to `voice/python/swarm/orchestrator/intent_classifier.py` mapping German phrases ("importiere <link>", "exportiere als EDL") to the MCP tools.

---

## Risks (from the spec) carried into execution
- **Per-start `LAURA_TOKEN`** → the `logs/laura.token` contract (Task 1.3 writes it, Phases 2–3 read it at call time).
- **Renderer/IPC coupling** → Phase-0 gate (injectable preload) must be done before Phase 2.
- **MP4 render missing today** → part of Laura Phase 0 (export stage plan); until then only interchange formats.
- **Port 8765** → no real conflict (Coding-Engine uses it as a WebSocket, different process) — still health-gate it.
- **Start-script path** → confirmed at `Vibemind_V1/scripts/vibemind-start.ps1` (super-root), not under `vibemind-os`.

## Out of scope (YAGNI, per spec)
No voice/intent in iteration 1; no event-handler agent (MCP suffices); no Docker for Laura (native venv); no sync/symlink for the import-alias path; no Tauri plugins (UI is Electron); no merge into `VideoBackendAgent` (coexistence).
