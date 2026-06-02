# Laura

**Frame-genaue, multimodale, local-first KI-Filmschnitt-Plattform.**

Laura ist ein professioneller, desktop-first Editorial- und Analyse-Assistent: Rohmaterial
lokal verstehen → Schnitte vorbereiten (transcript-first) → zuverlässiger NLE-Interchange.
Die Leitidee ist **Analyse zuerst, Schnittentscheidungen zweitens, NLE-Export drittens** —
**kein** vollwertiger Resolve-Klon, sondern ein AI-first Editorial Assistant mit
frame-/sample-genauer Zeitbasis.

> Die vollständige Spezifikation stammt aus einer Deep-Research-Analyse und liegt unter
> [`docs/research/deep-research-report.md`](docs/research/deep-research-report.md).
> Die implementierungsnahe, aufgeteilte Doku liegt in [`docs/`](docs/) — Einstieg:
> [`docs/00-overview.md`](docs/00-overview.md).

## Stack (Kurzfassung)

| Schicht | Wahl | Doku |
|---|---|---|
| Desktop-Shell | Electron + React (Vite) | [01-architecture](docs/01-architecture.md) |
| Lokaler Service | FastAPI (Python, `uv`) | [04-api](docs/04-api.md) |
| Worker | Python, DB-basierter Job-Runner | [05-workers-queue](docs/05-workers-queue.md) |
| Media I/O | FFmpeg + ffprobe | [01-architecture](docs/01-architecture.md) |
| Playback | libmpv (primär), WebCodecs nur ergänzend | [ADR-0002](docs/adr/0002-libmpv-primary-playback.md) |
| Zeitbasis | Eigenes Frame-/Sample-Modell (RationalTime) | [03-time-model](docs/03-time-model.md) |
| Interchange | OpenTimelineIO intern als Source of Truth | [07-interchange](docs/07-interchange.md) |
| Analyse | PySceneDetect/TransNetV2, faster-whisper, WhisperX, pyannote | [08-components](docs/08-components.md) |
| Store | SQLite lokal (Postgres-kompatibles Schema), Qdrant optional | [02-data-model](docs/02-data-model.md) |

## Monorepo-Struktur

```
Laura/
  apps/
    desktop/            # Electron + React (Vite) — die Pro-Desktop-App
  services/
    local-api/          # FastAPI lokaler Service + Worker + Zeitkern (Python/uv)
  packages/             # geteilte TS-Pakete (IPC-Typen, UI-Kit) — folgt
  docs/                 # implementierungsnahe Projekt-Doku (aus der Deep-Research)
    research/           # Original-Report (unverändert archiviert)
    adr/                # Architecture Decision Records
  tasks/todo.md         # lebende, portionierte Aufgabenliste
  fixtures/             # Golden-Fixtures für Interchange-/Zeitmodell-Tests
  workspace/            # (gitignored) lokale Medien-/Analyse-/Export-Artefakte
```

## Schnellstart (Entwicklung)

Voraussetzungen: Node ≥ 22 + pnpm, Python ≥ 3.11 + `uv`, FFmpeg/ffprobe im PATH.

```bash
# 1) Python-Service + Zeitkern (verifizierbar ohne UI)
cd services/local-api
uv sync                  # erstellt .venv aus pyproject.toml
uv run pytest -q         # Zeitmodell-/Range-/Sampling-Tests müssen grün sein
uv run laura-api         # FastAPI lokal auf http://127.0.0.1:8765

# 2) Desktop-App (Electron + React)
cd apps/desktop
pnpm install
pnpm dev                 # startet Electron mit Vite-Renderer
```

## Status

In aktiver, **portionsweiser** Entwicklung. Der aktuelle Plan und Fortschritt stehen in
[`tasks/todo.md`](tasks/todo.md). Die 12-Wochen-Roadmap steht in
[`docs/11-roadmap.md`](docs/11-roadmap.md).

## Leitplanken (nicht verhandelbar)

1. **Frames intern, end-exclusive Ranges.** Alle Edits als Ganzzahl-Frames relativ zur Sequence.
2. **Audio in Samples.** Wortgrenzen sample-genau speichern, für UI auf Frames projizieren.
3. **OTIO ist Source of Truth.** Niemals EDL/XML als Projektzustand — nur als Export erzeugen.
4. **Local-first.** Voller Ingest-/Analyse-/Rough-Cut-/Export-Pfad ohne Internet.
5. **Idempotente Analyse.** Gleicher Input + gleiche Pipeline-Version → gleicher Zustand.
