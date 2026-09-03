# Security Policy

## Supported versions

Laura is pre-1.0 (`0.1.0`) and under active, portion-wise development. There
are no maintained release branches yet — **only the `main` branch is
supported** for security fixes. If you are running an older commit or a fork,
update to current `main` before reporting an issue that may already be fixed.

## Reporting a vulnerability

Please **do not open a public GitHub issue** for a suspected security
vulnerability.

**Primary channel:** use [GitHub's private vulnerability reporting][private-reporting]
on this repository (`Vibemind-LAB/Lauras_star` → Security tab → "Report a
vulnerability"). This opens a private advisory visible only to maintainers
until a fix is ready.

**Alternate channel:** <security@ — TODO: set contact address>

[private-reporting]: https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability

When reporting, please include:

- The component affected (`services/local-api`, `services/mcp`,
  `apps/desktop`, `services/tts-sidecar`, `services/ai-runtimes`, …)
- Steps to reproduce, or a minimal repro if possible
- The impact you believe it has (e.g. local privilege escalation, token
  exposure, arbitrary file access, RCE via a crafted media file)

We do not currently run a bug bounty program.

## Scope

In scope:

- The local FastAPI service (`services/local-api`) and its API surface
- The MCP server (`services/mcp`)
- The Electron desktop app (`apps/desktop`), including preload/IPC surface
- The TTS sidecar and AI-runtime services (`services/tts-sidecar`,
  `services/ai-runtimes`, `services/analysis-runtime`)

Out of scope:

- Third-party services Laura optionally integrates with (e.g. ElevenLabs,
  Qdrant, Ollama) — report those upstream
- Issues that require an already-compromised local machine or an attacker
  with existing filesystem access equal to the user running Laura

## Security posture (verified against current code)

- **Local-first by default.** The local API binds to `127.0.0.1` by default
  (`DEFAULT_HOST` in `services/local-api/src/laura/config.py`, overridable via
  `LAURA_HOST`) — it is not exposed to the network unless explicitly
  reconfigured.
- **Token-protected API.** Requests must carry a matching `X-Laura-Token`
  header when `LAURA_TOKEN` (or the Electron main process's per-session
  token) is set; unauthenticated/mismatched requests get `401`
  (`services/local-api/src/laura/api/security.py`,
  `services/local-api/src/laura/auth/deps.py`).
- **Electron hardening.** The desktop app's `BrowserWindow` is created with
  `contextIsolation: true`, `sandbox: true`, `nodeIntegration: false`
  (`apps/desktop/src/main.ts`); the renderer only talks to the main process
  through a typed preload bridge, never `ipcRenderer` directly. A
  Content-Security-Policy is set via a `<meta>` tag in
  `apps/desktop/index.html`. External link targets are opened in the OS
  browser (`shell.openExternal`) and in-app navigation away from the app's
  own origin is blocked (`will-navigate` handler in `main.ts`).
  `ELECTRON_SKIP_BINARY_DOWNLOAD` is used only in CI, not in the shipped app.
- **Secrets stay out of version control.** `.env` and `.env.*` are
  git-ignored (only `.env.example`, which holds no real values, is tracked —
  see `.gitignore`). API keys (e.g. `HF_TOKEN`, `LAURA_ELEVENLABS_API_KEY`)
  and the local session token (`LAURA_TOKEN`) are read from the environment,
  never hardcoded.

We do not currently claim (and this document does not assert) code signing,
notarization, or auto-update integrity — those are tracked as future work in
`docs/09-security.md`, not shipped guarantees today. If you are relying on
any security property not explicitly listed above, please ask before
assuming it holds.
