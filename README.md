<div align="center">

# Laura

**A frame-accurate, local-first AI film-editing platform.**

*Understand raw footage on your own machine → prepare cuts transcript-first → export
reliably into professional NLE workflows.*

[![CI](https://github.com/Vibemind-LAB/Lauras_star/actions/workflows/ci.yml/badge.svg)](https://github.com/Vibemind-LAB/Lauras_star/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-%E2%89%A53.11-3776ab.svg)](services/local-api/pyproject.toml)
[![Node](https://img.shields.io/badge/node-%E2%89%A522-5fa04e.svg)](package.json)

</div>

---

## What Laura is

Laura is an **AI-first editorial assistant**, not another NLE. It does the work that eats an
editor's day before the first cut is made: watching hours of footage, finding what was said
and by whom, proposing selects, and handing a clean, frame-accurate timeline to the tool the
editor already uses.

Three properties define it:

- **Local-first.** Ingest, analysis, rough cut and export run without internet. Footage stays
  on the machine; the backend binds to loopback only.
- **Frame-accurate.** All edits are integer frames relative to the sequence, all ranges are
  end-exclusive, audio is stored in samples. Timecode drift is treated as a bug, not a
  rounding detail.
- **Interchange you can trust.** OpenTimelineIO is the internal source of truth; EDL,
  FCP7-XML, FCPXML, SRT and VTT are exports — never project state.

Deliberately **not** in scope: a full effects and compositing suite. Laura hands off to
Resolve, Premiere and Final Cut instead of competing with them.

## What it can do today

| Capability | What that means in practice |
|---|---|
| **Ingest & analysis** | Import local files or URLs; shot detection, word-level transcription, speaker diarization, semantic search across the material |
| **Transcript-first editing** | Cut by reading: word-accurate ranges, corrections that reindex automatically, confirmation gates before production |
| **Chat-first operation** | Plain-language instructions drive analysis and production through typed tools — the primary interface is a conversation |
| **Automated short production** | Source video to finished short: scene selection → grounded script → voice-over → captions → render, each step a checkpointed tool call |
| **Narrated reels** | One call turns a beat list (narration text + source window per beat) into a finished collage: synthesized voice, crossfades, karaoke captions, fade-out |
| **Voice** | Pluggable TTS backends — dependency-free stub, Windows system voice, ElevenLabs, or a local neural voice-cloning sidecar |
| **Interchange & export** | OTIO, EDL, FCP7-XML, FCPXML, SRT/VTT, and rendered MP4 |
| **Agent access** | A stdio [MCP](https://modelcontextprotocol.io) server exposes 27 typed tools plus a raw API escape hatch, so assistants can drive Laura directly |

## How it works

```
        Desktop app (Electron + React)          MCP client (assistants, agents)
                    |                                      |
                    +------------------+-------------------+
                                       v
                       Local service - FastAPI on 127.0.0.1:8765
                       (token-protected, loopback only)
                                       |
        +--------------+---------------+------------+------------------+
        v              v               v            v                  v
   Job runner     Time core       Analysis     Interchange         Renderer
   (SQLite-       (frames /       (shots,      (OTIO source        (FFmpeg,
    backed         samples,        ASR,         of truth ->         captions,
    queue)         rational        speakers,    EDL/XML/SRT)        transitions)
                   time)           embeddings)
                                       |
                                       v
                     Workspace on disk + SQLite   (Postgres/Qdrant optional)
```

Details: [`docs/01-architecture.md`](docs/01-architecture.md) ·
[`docs/03-time-model.md`](docs/03-time-model.md) ·
[`docs/07-interchange.md`](docs/07-interchange.md)

## Quickstart

**Prerequisites:** Python ≥ 3.11 with [uv](https://docs.astral.sh/uv/), Node ≥ 22 with
[pnpm](https://pnpm.io/), and FFmpeg + ffprobe on `PATH`.

```bash
git clone https://github.com/Vibemind-LAB/Lauras_star.git laura
cd laura
```

```bash
# macOS / Linux
./scripts/setup.sh
```

```powershell
# Windows
./scripts/setup.ps1
```

The setup script checks every prerequisite (and names what is missing and where to get it),
installs both Python services and the JavaScript workspace, creates `.env` from
`.env.example`, and runs a smoke check. Pass `--check` / `-Check` to verify prerequisites
without installing anything.

Then start Laura:

```bash
# Backend only — the full API, no UI
cd services/local-api && uv run laura-api        # http://127.0.0.1:8765

# Desktop app (starts its own backend)
pnpm dev
```

Optional extras — local neural voice cloning
([`services/tts-sidecar/README.md`](services/tts-sidecar/README.md)), URL ingest for site
links (`scripts/setup-fetch.sh`), and GPU analysis runtimes (`scripts/ai-runtimes.sh`) — are
documented where they live and are never required for the core path.

## Verify it

The same three gates run in CI on every push and pull request, so a green local run means a
green pipeline:

```bash
# Backend: lint, strict types, full test suite
cd services/local-api && uv run ruff check . && uv run mypy && uv run pytest

# MCP server
cd services/mcp && uv run ruff check src tests && uv run mypy src tests && uv run pytest tests/ -q

# Desktop: types, unit tests, renderer bundle
cd apps/desktop && pnpm typecheck && pnpm test && pnpm build:renderer
```

`uv run mypy` is intentionally called without a path argument — the configuration covers
sources *and* tests, and CI does exactly the same.

## Repository layout

```
apps/desktop/           Electron + React (Vite) — the desktop application
services/
  local-api/            FastAPI service, job runner, time core, analysis, renderer (Python/uv)
  mcp/                  stdio MCP server — 27 typed tools + raw API escape hatch
  tts-sidecar/          Optional local neural TTS (own venv, HTTP contract)
  ai-runtimes/          Optional GPU analysis runtimes
docs/                   Implementation documentation (00–17), ADRs, research report
  superpowers/          Specs and implementation plans per feature arc
scripts/                Setup, prerequisite checks, optional installers
fixtures/               Golden fixtures for time-model and interchange tests
tasks/todo.md           Living, portioned task list
workspace/              (gitignored) local media, analysis and export artifacts
```

## Non-negotiable invariants

These are the engineering heart of the product; violating one is a bug, not a trade-off:

1. **Timeline edits are integer frames** relative to the sequence — never float seconds as state.
2. **Ranges are end-exclusive** (`out_frame_exclusive`), consistently, everywhere.
3. **Audio and alignment live in samples**; frames are a projection for the UI.
4. **Drop-frame is display only.** Internal math always uses NDF frame indices.
5. **VFR sources get a CFR proxy** for editorial; source mapping stays separate.
6. **OTIO is the source of truth.** EDL/FCP7-XML/FCPXML/SRT/VTT are exports, never state.
7. **Analysis is idempotent:** `(input, pipeline_version)` determines the analysis state.

## Documentation

| Start here | |
|---|---|
| [`docs/00-overview.md`](docs/00-overview.md) | Product thesis, the four cores, documentation index |
| [`docs/01-architecture.md`](docs/01-architecture.md) | System architecture |
| [`docs/03-time-model.md`](docs/03-time-model.md) | The time core — frames, samples, rational time |
| [`docs/04-api.md`](docs/04-api.md) | HTTP API surface |
| [`docs/09-security.md`](docs/09-security.md) | Security model |
| [`docs/17-runbook.md`](docs/17-runbook.md) | Operations runbook — processes, ports, real-world failure modes |
| [`docs/adr/`](docs/adr/) | Architecture Decision Records |
| [`docs/research/deep-research-report.md`](docs/research/deep-research-report.md) | The original research report this product is derived from |

Implementation prose is German (mirroring the research report); code, comments and commit
messages are English.

## Status

Pre-1.0 and under active, deliberately portioned development — see
[`tasks/todo.md`](tasks/todo.md) for the current increment and
[`docs/11-roadmap.md`](docs/11-roadmap.md) for the roadmap. The core path (ingest → analysis
→ rough cut → export) works end to end today; interfaces may still change between increments.

## Contributing

Contributions are welcome — please read [`CONTRIBUTING.md`](CONTRIBUTING.md) first: it covers
setup, the invariants above, the verification gates, and the commit conventions. Security
issues go through [`SECURITY.md`](SECURITY.md), never a public issue.

## License

Licensed under the [Apache License 2.0](LICENSE). See [`NOTICE`](NOTICE) for attribution and
third-party components.
