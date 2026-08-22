# Contributing to Laura

Laura is a frame-accurate, local-first AI film-editing platform: a FastAPI backend
(`services/local-api`), an Electron + React desktop app (`apps/desktop`), an MCP
server (`services/mcp`) that exposes the same backend to agent sessions, and an
optional TTS sidecar (`services/tts-sidecar`). This document covers everything
you need to set up, verify, and propose a change.

## Setup

```bash
# POSIX (macOS/Linux/WSL)
./scripts/setup.sh

# Windows (PowerShell)
./scripts/setup.ps1
```

These scripts install the Python toolchain (`uv`), sync `services/local-api` and
`services/mcp`, and install the desktop workspace (`pnpm install`). Prerequisites
they assume: Node ≥ 22 + `pnpm`, Python ≥ 3.11 + `uv`, FFmpeg/ffprobe on `PATH`.

If you prefer to set up by hand, or the scripts aren't available yet in your
checkout:

```bash
# Backend
cd services/local-api
uv sync --extra scene --extra otel --extra autoshort
uv run laura-api          # FastAPI on http://127.0.0.1:8765

# MCP server
cd services/mcp
uv sync

# Desktop app
pnpm install               # from the repo root (pnpm workspace)
pnpm dev                   # electron-forge start
```

Heavy ML components (ASR, forced alignment, diarization, learned scene
detection) are **optional `uv` extras** (`asr`, `align`, `diarize`,
`scene-ml`), never a hard dependency. The backend must start and run
ingest/probe/proxy/export without any of them installed — keep that true in
anything you contribute.

## Non-negotiable invariants

These are the core of the product (time model and interchange correctness).
Violating one of these is a bug, not a style nit — code review will block on
it:

1. **Timeline edits are always stored as integer frames** relative to the
   sequence. Never store float seconds as state.
2. **Ranges are end-exclusive** (`out_frame_exclusive`). Consistently,
   everywhere.
3. **Audio/alignment is stored in samples.** Frames are a *projection* for
   the UI, not the source of truth for audio-accurate positions.
4. **DF/NDF (drop-frame/non-drop-frame) is display-only.** Internal
   computation always uses NDF frame indices; drop-frame notation is applied
   only when formatting a timecode string.
5. **VFR sources get a CFR proxy for editorial.** Keep source-frame mapping
   separate from the editorial (proxy) frame indices.
6. **OTIO (OpenTimelineIO) is the source of truth.** EDL, FCP7 XML, FCPXML,
   SRT, and VTT are *exports*, never project state.
7. **Idempotency:** `(input, pipeline_version)` uniquely determines analysis
   state. Re-running analysis on the same input and pipeline version must not
   produce a different result or a duplicate run.

## Code style

- **Python** (3.11+, managed with `uv`): `ruff` for lint/format, `mypy --strict`
  for typing, no `print()` in committed code — use the project-local logger
  (`logging.getLogger(__name__)`). Tests with `pytest`.
- **TypeScript** (`apps/desktop`, strict mode): never use `any` — use
  `unknown` and narrow. Tailwind for styling. Electron: `contextIsolation: true`,
  `sandbox: true`, `nodeIntegration: false` in the renderer; all IPC goes
  through the typed preload bridge (`contextBridge.exposeInMainWorld`), never
  direct `ipcRenderer` access from the renderer.
- **Commits:** Conventional Commits (`feat:`, `fix:`, `refactor:`, `docs:`,
  `test:`, `chore:`, …). Feature branches — never commit directly to
  `main`/`master`.
- Match the existing style in the file/module you're touching if it differs
  from the above; don't drive-by reformat unrelated code.

## Verification (must pass before you open a PR)

These are exactly the commands CI runs (`.github/workflows/ci.yml`) — run them
locally before pushing:

```bash
# services/local-api
cd services/local-api
uv run ruff check .
uv run mypy            # bare, no path argument — covers src/ and tests/
uv run pytest

# services/mcp
cd services/mcp
uv run pytest tests/ -q
uv run mypy src tests
uv run ruff check src tests

# apps/desktop
pnpm install --frozen-lockfile   # from repo root
pnpm typecheck                    # tsc --noEmit, strict
pnpm build:renderer                # vite build of the renderer bundle
pnpm test                          # vitest run
```

CI installs the backend with `--extra scene --extra otel --extra autoshort`;
if your change touches code gated behind another extra (`asr`, `align`,
`diarize`, `scene-ml`, `server`, `semantic`, `mcp`), sync that extra locally
and test it too — CI will not catch a regression in an extra it doesn't
install.

If you touch the frame/sample time core (`services/local-api/src/laura/timebase/`)
or interchange (OTIO) code, treat a green `uv run pytest` there as necessary
but not sufficient — reason explicitly about the invariants above.

## Proposing changes

1. **Open an issue first** for anything beyond a trivial fix (bug report or
   feature request — use the issue templates under `.github/ISSUE_TEMPLATE/`).
   This avoids duplicate work and lets us discuss the approach before code is
   written, especially for anything touching the time core or interchange.
2. **Branch from `main`**, one feature/fix per branch.
3. **Commit with Conventional Commits.** Small, reviewable commits over one
   giant diff.
4. **Run the verification commands above** for every service/app you
   touched.
5. **Open a PR** against `main` using the PR template
   (`.github/PULL_REQUEST_TEMPLATE.md`) — fill in the checklist honestly,
   including the "invariants respected" and "docs/lessons updated" items.
6. If you learned something the hard way during the change (a real
   correction, not just a confirmation), add it to `lessons.md` in the same
   PR — that's how the project accumulates institutional memory.

## Where things live

- `docs/00-overview.md` is the doc entry point; `docs/03-time-model.md` and
  `docs/07-interchange.md` cover the invariants above in depth.
- `tasks/todo.md` is the living source of truth for project status and
  sequencing.
- `lessons.md` is the log of real corrections — read it before touching an
  area you're unfamiliar with; someone may have already hit the bug you're
  about to reintroduce.
