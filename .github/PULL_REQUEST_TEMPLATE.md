## Summary

<!-- What does this PR do, and why? Link the issue it addresses if any. -->

## Component(s) touched

<!-- Check all that apply -->
- [ ] services/local-api (backend/API)
- [ ] services/mcp (MCP server)
- [ ] apps/desktop (Electron/React app)
- [ ] services/tts-sidecar / services/ai-runtimes / services/analysis-runtime
- [ ] docs

## Verification

<!-- Check off only what you actually ran locally. These mirror the CI jobs
     in .github/workflows/ci.yml — a checked box should mean the command
     passed on your machine, not just that you glanced at the diff. -->

**Backend (`services/local-api`)** — if touched:
- [ ] `uv run ruff check .`
- [ ] `uv run mypy` (bare — no path argument, covers `src/` and `tests/`)
- [ ] `uv run pytest`

**MCP server (`services/mcp`)** — if touched:
- [ ] `uv run pytest tests/ -q`
- [ ] `uv run mypy src tests`
- [ ] `uv run ruff check src tests`

**Desktop app (`apps/desktop`)** — if touched:
- [ ] `pnpm typecheck`
- [ ] `pnpm build:renderer`
- [ ] `pnpm test` (vitest)

## Invariants

- [ ] This change respects the non-negotiable invariants in `CONTRIBUTING.md`
      (integer frames as timeline state, end-exclusive ranges, audio in
      samples, DF/NDF display-only, VFR→CFR proxy, OTIO as source of truth,
      idempotent analysis) — or N/A, this PR doesn't touch time/interchange
      code.

## Docs / lessons

- [ ] `docs/` updated if this changes behavior described there — or N/A.
- [ ] `lessons.md` updated if a real correction was learned while building
      this (not just a confirmation) — or N/A.

## Notes for reviewers

<!-- Anything a reviewer should know: tricky tradeoffs, follow-ups you're
     deliberately deferring, manual-only verification you couldn't automate. -->
