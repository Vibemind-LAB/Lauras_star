# AGENTS.md — Laura (projektlokale Anweisungen)

Diese Datei wird in jeder Codex-Session in diesem Repo geladen. Sie ergänzt die
globale `~/.Codex/AGENTS.md`. Hier stehen **projektspezifische** Regeln.

## Was Laura ist

Frame-genaue, multimodale, **local-first** KI-Filmschnitt-Plattform. AI-first Editorial
Assistant (kein Resolve-Klon). Vollständige Spezifikation:
[`docs/research/deep-research-report.md`](docs/research/deep-research-report.md);
implementierungsnahe Doku in [`docs/`](docs/), Einstieg [`docs/00-overview.md`](docs/00-overview.md).

## Arbeitsweise in diesem Repo

- **Portionsweise bauen.** Quelle der Wahrheit für Reihenfolge/Stand ist [`tasks/todo.md`](tasks/todo.md).
- **Verifizieren vor „fertig".** Zeitkern → `uv run pytest`. Ingest → echter ffprobe-Lauf gegen ein Fixture.
  UI-Pfade, die headless nicht prüfbar sind, ausdrücklich als „manuell zu prüfen" markieren.
- **Reale Korrekturen** des Users in [`lessons.md`](lessons.md) festhalten (nicht nur bestätigen).

## Nicht verhandelbare Invarianten (Zeit/Interchange)

Diese Regeln sind der Kern des Produkts (größtes Risiko laut Report). Verstöße = Bugs:

1. **Timeline-Edits immer in Ganzzahl-Frames** relativ zur Sequence speichern. Nie Float-Sekunden als Zustand.
2. **Ranges sind end-exclusive** (`out_frame_exclusive`). Konsistent überall.
3. **Audio/Alignment in Samples** speichern; Frames sind eine *Projektion* für die UI.
4. **DF/NDF ist reine Anzeige.** Interne Rechnung immer NDF-Frame-Indizes; Drop-Frame nur beim Formatieren.
5. **VFR-Quellen** → CFR-Proxy fürs Editorial; Source-Mapping getrennt halten.
6. **OTIO ist Source of Truth.** EDL/FCP7-XML/FCPXML/SRT/VTT sind *Exporte*, nie Projektzustand.
7. **Idempotenz:** `(input, pipeline_version)` bestimmt den Analysezustand eindeutig.

## Tech-Konventionen

- **Python:** 3.11+, verwaltet mit `uv`. Typing strikt (mypy), `ruff` für Lint/Format. Kein `print` in
  committed Code → projektlokaler Logger. Tests mit `pytest`.
- **TypeScript:** `strict`, **niemals `any`** (→ `unknown` + narrowen). Tailwind fürs Styling.
  Electron mit `contextIsolation: true`, kein `nodeIntegration` im Renderer; IPC über typisierte Preload-Bridge.
- **Conventional Commits.** Feature-Branches. Niemals direkt auf `main`/`master` ohne Ansage.
- **Schwere Modelle** (Whisper/pyannote/TransNetV2) sind **optionale Extras** — niemals harte Default-Dependency.
  Backend muss ohne GPU/Modelle starten und ingest/probe/proxy/export können.

## Lokaler Service — Ports & Pfade

- FastAPI lokal: `http://127.0.0.1:8765` (siehe `services/local-api`).
- Runtime-Artefakte unter `workspace/` (gitignored). Layout: [`docs/06-storage.md`](docs/06-storage.md).

## Sprache

Doku-Prosa: Deutsch (wie der Report). Code-Identifier, Kommentare, Commit-Messages: Englisch.
