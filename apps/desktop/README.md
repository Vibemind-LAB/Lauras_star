# @laura/desktop

Electron + React (Vite) desktop shell for Laura. Starts the local Python API as a
child process, talks to it over loopback HTTP with a per-session token, and renders
the transcript-first editorial UI.

## Architecture

- **Main** (`src/main.ts`) — window lifecycle, security hardening, spawns the local
  service (`src/service.ts`), exposes service info over IPC.
- **Preload** (`src/preload.ts`) — the only renderer surface: a typed
  `window.laura` bridge (no `nodeIntegration`, `contextIsolation` + `sandbox` on).
- **Renderer** (`src/App.tsx`, `src/api.ts`) — React UI + typed API client.

Security posture (docs/09-security.md): `contextIsolation: true`, `sandbox: true`,
`nodeIntegration: false`, strict CSP, external links open in the OS browser, in-app
navigation blocked.

## Run (development)

Requires the local service to be runnable: Python ≥ 3.11 + `uv`, FFmpeg on PATH.
The main process launches `uv run laura-api` from `../../services/local-api`
automatically — you do **not** start the backend separately.

```bash
pnpm install          # from the repo root (workspace)
pnpm dev              # = electron-forge start: launches app + local service
```

`pnpm dev` boots Electron, which spawns the service, polls `/healthz`, then loads the
UI. The health badge in the title bar turns green once the service is reachable.

## Verify without launching

```bash
pnpm typecheck        # tsc --noEmit (strict) — validates all main/preload/renderer code
pnpm build:renderer   # vite build of the renderer bundle
```

## Notes

- **Disk space:** Electron's binary (~110 MB) plus esbuild are downloaded on first
  `pnpm install`. Ensure a few hundred MB free, or the install cannot complete.
- Packaging/signing (Electron Forge makers, macOS notarization, Windows signing) and
  bundling the Python runtime + FFmpeg + libmpv are Portion 10 (Release).
- The service base URL/token are provided to the renderer via `window.laura.getServiceInfo()`;
  the renderer never touches the filesystem or models directly.
