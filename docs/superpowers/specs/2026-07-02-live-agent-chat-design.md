# Live-Agent-Chat — Design

_Datum: 2026-07-02 · Status: Richtung freigegeben, Umsetzung in 4 Tasks · Branch: `feat/generate-ui`_

## Ziel

Ein **angedockter Chat** (immer sichtbare Seitenleiste), in dem der User in natürlicher Sprache
sagt, was er will („mach mir einen 60s-Short über X"), das **Agenten-Team live läuft** und der User
**gleichzeitig zusieht**: ein Live-Feed der Agenten-Schritte **und** die App-Views (Timeline/Player/
Szenen), die sich **live daneben füllen**. Baut auf dem bestehenden `short_creator`-Backend auf
(AutoGen 0.4, siehe [`2026-07-01-nl-agent-short-creator-design.md`](2026-07-01-nl-agent-short-creator-design.md)).

## Architektur (freigegeben)

- **Transport — SSE-Stream via `fetch`.** Neuer Endpoint `POST /assets/{id}/auto-short/stream` →
  FastAPI-`StreamingResponse` mit **NDJSON** (eine JSON-Zeile pro Event). Das Frontend liest den
  Stream via `fetch` + Response-Reader (kann Auth-Header, anders als natives `EventSource`).
  Echt-live, Standard-Muster für Agenten-Streaming. Der Lauf hängt an der Verbindung (Chat schließen
  = stoppen) — für „live zuschauen" gewollt. Der Job-Pfad `POST /auto-short` bleibt für fire-and-forget.
- **Backend — Streaming-Orchestrator.** Der Eskalations-Ladder bekommt eine Stream-Variante, die
  `team.run_stream()` fährt und **normalisierte Events** yieldt (unten). Kapselt AutoGens Roh-Events
  → stabiles Modell. `optional-extra`-sicher: kein autogen-Import beim Modul-Laden.
- **Frontend — angedocktes `ChatPanel`.** Immer sichtbare Seitenleiste (wie `MediaSidebar`), Input +
  Nachrichtenliste. Events → Chat-Bubbles (welcher Agent, welche Tools/Ergebnisse, Tool-Details
  einklappbar), am Ende „Short fertig" mit Öffnen-Button.
- **App füllt sich live.** `artifact`-Events → das Frontend invalidiert die passenden **TanStack-
  Queries** → Timeline/Player/Szenen aktualisieren sich live. Dank Cache fast geschenkt.

## Event-Modell (NDJSON, eine Zeile pro Event)

```
{ "type": "stage",       "stage": "A"|"B", "team": "magentic"|"graph" }
{ "type": "agent",       "agent": "<name>", "text": "<message>" }
{ "type": "tool_call",   "agent": "<name>", "tool": "<name>", "args": { … } }
{ "type": "tool_result", "tool": "<name>", "ok": true, "summary": "<short>" }
{ "type": "artifact",    "kind": "roughcut"|"scenes"|"timeline"|"render", "id": "<id>" }
{ "type": "escalated",   "to": "9router" }
{ "type": "done",        "ok": true, "stage": "A"|"B", "team": "…", "weak": false, "escalated": false, "summary": "…" }
{ "type": "error",       "message": "<why>" }
```

- `tool_result` → wenn das Ergebnis-Dict eine Artefakt-Id trägt (`timeline_id`, `export_id`,
  `scene_count`>0), zusätzlich ein `artifact`-Event → treibt die Cache-Invalidierung.
- Das Backend mappt AutoGens `run_stream`-Events (Text/ToolCall/ToolResult) → diese Typen; unbekannte
  Roh-Events werden verworfen (nie roh durchreichen).

## Tasks (1–4)

**Task 1 — Backend Streaming-Orchestrator (`short_creator/stream.py`).**
`run_short_creator_stream(db, config, *, asset_id, topic, target_seconds, execute_stream=None)` →
`AsyncIterator[dict]`. Fährt den Ladder mit Stream-Events: Stage A (magentic → bei hard-fail graph),
bei „zu schlecht" `escalated` + Stage B. `execute_stream(db, config, stage, kind, task)` ist ein
async-Generator (Default: treibt echtes `team.run_stream`, mappt → Events, liefert am Ende die
`StageOutcome`); injizierbar für Tests. **Tests:** Fake-`execute_stream` mit scripted Events →
per `asyncio.run` gesammelte Event-Sequenz asserten (inkl. stage/escalated/done); kein autogen nötig.

**Task 2 — Backend SSE-Endpoint (`api/short_creator.py`).**
`POST /assets/{id}/auto-short/stream` → `StreamingResponse(media_type="application/x-ndjson")`, das
`run_short_creator_stream` iteriert und je Event eine JSON-Zeile schreibt. 404 (Asset) vor 503
(Extra). **Tests:** 404/422/503 + ein 200-Stream mit injiziertem Fake-Orchestrator (Body = NDJSON-Zeilen).

**Task 3 — Frontend api + `ChatPanel` (`api.ts`, `components/ChatPanel.tsx`).**
`streamAutoShort(assetId, req, onEvent)`: `fetch` POST, Response-Body als Stream lesen, NDJSON parsen,
`onEvent` je Zeile (Auth-Header wie sonst). `ChatPanel`: Input + Nachrichtenliste; Events →
Bubbles (agent/tool/stage/artifact/done/error), Tool-Details `<details>`-einklappbar; „läuft"-Indikator;
Fehler sichtbar. **Tests (vitest):** gemockter Stream → Nachrichten rendern; Submit ruft `streamAutoShort`.

**Task 4 — Wire + Live-Updates (`App.tsx`).**
`ChatPanel` als angedockte rechte Seitenleiste (toggelbar). `onEvent`: bei `artifact`/`done` die
passenden TanStack-Queries invalidieren (`roughCut`, `scenes`, `timeline`, `exports`) → Views füllen
sich live. **Verify:** `pnpm --dir apps/desktop run typecheck` + `vitest`.

## Invarianten

- **Optionales Extra bleibt optional:** kein autogen-Import beim Laden; Endpoint 503 ohne Extra.
- **Local-first:** loopback, kein neuer Zwang; Stream via lokalem FastAPI.
- **TS strict, kein `any`; Tailwind; projektlokaler Logger; kein `console.log`.** Python mypy strict, ruff.
- **Codex-Subtree unangetastet** (`ai/runtime_*`, `services/ai-runtimes/`, `api/ai_runtimes.py`).
- **Kein Sensitives in URL-Params** (Topic geht im POST-Body, nicht in der Query).

## Fehlerbehandlung

- Stream bricht ab (Verbindung zu) → Backend-Lauf endet (gewollt); Frontend zeigt „abgebrochen".
- Agent/Tool-Fehler → `error`-Event, Chat zeigt es; der Lauf endet sauber (kein Crash).
- Fehlt das Extra → 503 vor dem Stream-Start.

## Manuell zu verifizieren

Der echte Live-Lauf braucht Ollama + `autoshort`-Extra + ein Sprechvideo (wie beim Short-Creator).
Alle Nicht-Modell-Pfade (Event-Mapping, Ladder-Streaming, Endpoint-NDJSON, ChatPanel-Rendering,
Cache-Invalidierung) sind mit Fakes getestet.
