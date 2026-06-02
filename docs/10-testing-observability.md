# 10 — Teststrategie & Observability

Drei Testschichten: **Medien-/Fixture-Tests**, **Zeitmodell-Tests**, **Interop-/Golden-Tests**.
Der Zeitmodell-Teil wird **hart** getestet (größtes Risiko).

## Testpyramide

| Testart | Beispiele |
|---|---|
| Unit | Timecode/DF/NDF, Range-Math, word-to-frame snapping, RationalTime-Rescale |
| Integration | ffprobe-Ingest, Proxy-Pipeline, ASR→Alignment→Diarization (mit kleinen Fixtures/Mocks) |
| Golden Export | OTIO, EDL, FCP7-XML, FCPXML, SRT, VTT gegen Fixture-Sets |
| UX-Regression | Transcript-Operationen erzeugen **exakt** erwartete Timeline-Deltas |
| Performance | Analysegeschwindigkeit, Queue-Latenzen, RAM/VRAM-Budgets |
| Chaos/Recovery | Worker-Absturz, halbe Exporte, Session-Reopen, geänderter Medienpfad |

## Golden-Corpus-Strategie

- `fixtures/timecode/` — Frame↔Timecode-Tabellen je Rate (inkl. DF-Drop-Punkte) als Erwartungswerte.
- `fixtures/ranges/` — end-exclusive Range-Operationen (cut/ripple/lift) mit Soll-Deltas.
- `fixtures/interchange/` — kleine Timelines + Soll-Exporte (OTIO/EDL/XML/SRT/VTT), byte-/struktur-stabil.
- `fixtures/media/` — **sehr kleine** generierte Clips (FFmpeg `testsrc`/`sine`) für Ingest-Tests;
  große Medien nicht ins Repo (LFS/extern).

Determinismus-Regel: gleiche Eingabe → gleiche Ausgabe, plattformunabhängig. Exporte byte-stabil
(sortierte Attribute, feste Zeitformatierung).

## Verifikationsgebot (für Implementierung)

- Zeitkern/Interchange: `uv run pytest` muss grün sein, **bevor** etwas „fertig" heißt.
- Ingest: echter ffprobe-Lauf gegen ein generiertes Fixture (kein Mock für den Happy Path).
- UI-Pfade, die headless nicht prüfbar sind, ausdrücklich als „manuell zu prüfen" kennzeichnen.

## Observability

- **OpenTelemetry** für API & Queue (offizielle FastAPI-/Celery-Instrumentierung).
- **Prometheus** für Worker/Queue/GPU (offizieller Python-Client).

### Metriken

| Kategorie | Metriken |
|---|---|
| API | Request-Latenz, Fehlerquote, Enqueue-Zeit |
| Worker | Joblaufzeit, Retries, Failures, Lease-Timeouts |
| AI | ASR × Realtime, Diarization-Latenz, Alignment-Failures |
| Media | Proxy-/Waveform-/Export-Durchsatz |
| UX | search-to-highlight, jump-to-frame, Playback-Stall-Count |
| Infra | CPU, RAM, VRAM, Disk-I/O, Queue-Tiefe |
