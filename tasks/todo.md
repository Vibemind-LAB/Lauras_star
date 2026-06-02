# Laura — Arbeitsportionen (lebende Aufgabenliste)

Quelle der Wahrheit für Reihenfolge & Stand. Eine **Portion** = ein in sich abschließbarer,
verifizierbarer Brocken. Reihenfolge folgt der Report-Doktrin:
**präzise Zeitbasis → Backend → Ingest → Interchange → Electron → Analyse → Rough-Cut → Playback → Release.**

Legende: `[ ]` offen · `[~]` in Arbeit · `[x]` fertig & verifiziert · `[!]` blockiert/Risiko

---

## Portion 0 — Repo-Gerüst & Doku-Transfer  `[x]`
- [x] git init, Monorepo-Skelett (`apps/ services/ packages/ docs/ tasks/ fixtures/`)
- [x] Deep-Research-Report nach `docs/research/` archiviert
- [x] Strukturierte Doku 00–12 + 5 ADRs aus dem Report destilliert
- [x] Root `README.md`, projektlokale `CLAUDE.md`, `lessons.md`, `.gitignore`
- [x] Diese `tasks/todo.md`
- **Exit:** Spezifikation als lebende Doku im Repo verankert. ✓

## Portion 1 — Zeitkern `timebase/` + Tests  `[x]`  ← **Kern-Risiko, erledigt**
- [x] `services/local-api` als uv-Projekt (`pyproject.toml`, ruff, mypy, pytest)
- [x] `timebase/rounding.py` — deterministische Integer-Rundung (floor/ceil/half-up/down/even)
- [x] `timebase/rational.py` — RationalTime (rescale, Vergleich über Raten, Arithmetik)
- [x] `timebase/framerate.py` — FrameRate-Presets, DF/NDF-Regeln (29.97 & 59.94)
- [x] `timebase/timecode.py` — frames↔SMPTE-Timecode (DF `;`, NDF `:`)
- [x] `timebase/ranges.py` — FrameRange + MediaRange, end-exclusive Math, Speed-Mapping
- [x] `timebase/sampling.py` — sample↔frame Projektion, Word-Snapping (floor/ceil)
- [x] `tests/` — 82 Tests: DF-Drop-Punkte, Round-trips alle Raten, Range-Deltas, Snapping, Speed
- **Exit:** ✓ `uv run pytest` 82/82 grün · ruff clean · mypy strict clean · keine Timecode-Drifts.

## Portion 2 — Backend-Grundgerüst (FastAPI + DB + Job-Runner)  `[x]`
- [x] `config.py` (Pfade, Workspace-Root, Port 8765, Token, Pipeline-Version)
- [x] `db/migrations/0001_init.sql` (Schema v1, 13 Tabellen) + Migrations-Runner + `schema_meta`
- [x] `db/database.py` (Connection, WAL, Tx-Helper, `BEGIN IMMEDIATE` für Claims)
- [x] `jobs/runner.py` (Claim/Lease/Heartbeat/Reaper, Idempotenz, Handler-Registry)
- [x] `main.py` (FastAPI app, Lifespan, `/healthz`, lokales Token, 127.0.0.1)
- [x] `api/projects.py` (`POST/GET /projects`) + FrameRate-Validierung
- [x] Konsole-Entrypoint `laura-api` + `scripts/smoke_server.py`
- **Exit:** ✓ 92 Tests · ruff/mypy clean · realer uvicorn-HTTP-Smoke (healthz + Projekt-Roundtrip).

## Portion 3 — Ingest-Pipeline (ffprobe → proxy → audio → waveform)  `[x]`
- [x] `ingest/ffmpeg.py` (ffprobe/ffmpeg-Aufruf, Binary-Discovery, kein Shell)
- [x] `ingest/probe.py` (ffprobe JSON → Metadaten, Timecode, VFR-Heuristik, sha256-Stream)
- [x] `api/assets.py` (`POST /projects/{id}/assets/import`, `GET /assets/{id}`) + `api/jobs.py`
- [x] `ingest/proxy.py` (CFR-Proxy all-intra, kein Upscale, + Poster)
- [x] `ingest/audio.py` (mono-16k für ASR, mix-48k für Playback)
- [x] `ingest/waveform.py` (Peaks → waveform.json, stdlib-only)
- [x] `ingest/handlers.py` — Job-Verkettung `ingest.probe → proxy/audio → waveform`
- [x] `repos` Asset-Operationen (create/get/update_probe/add_file/list_files, idempotent)
- [x] Integrationstest gegen echtes FFmpeg-`testsrc`/`sine`-Fixture (probe→proxy→audio→waveform)
- **Exit:** ✓ 94 Tests · ruff/mypy clean · ganze Pipeline end-to-end gegen reales FFmpeg, 0 failed jobs.

## Portion 4 — Interchange-Grundlage (OTIO/EDL/Captions + Preflight)  `[x]`
- [x] `interchange/timeline.py` — kanonisches Timeline/Clip-Modell + `timeline_from_rows`
- [x] `interchange/otio_io.py` — OTIO read/write via echter Lib (kanonisch), Round-trip-getestet
- [x] `interchange/edl.py` — CMX3600 deterministisch (inkl. Drop-Frame `;`)
- [x] `interchange/captions.py` — SRT/VTT aus Segmenten (golden-getestet, ms-genau)
- [x] `interchange/validate.py` — Capability/Degradation-Preflight (EDL: lanes/speaker/speed)
- [x] API: `POST/GET …/timelines`, `POST /timelines/{id}/exports`, `POST /interop/validate`, `GET /assets/{id}/captions.{srt,vtt}`
- [x] Frontend: SRT/VTT-Export-Buttons im `AnalysisPanel` (Save-Dialog via IPC)
- [x] **Verifiziert:** 114 Tests (golden SRT/VTT/EDL, OTIO-Round-trip, Preflight, API) · ruff/mypy strict clean · `tsc` + `vite build` grün
- **Exit:** ✓ OTIO/EDL/SRT/VTT deterministisch; Preflight meldet lossy/drops; Captions direkt aus der App exportierbar.

## Portion 5 — Electron + React Shell  `[x]`  ← Code + Build verifiziert; GUI-Launch bereit
- [x] `apps/desktop` — Electron Forge + Vite + React + TS (strict) + Tailwind
- [x] Main-Prozess: Fenster, `contextIsolation`, sandbox, no nodeIntegration, lokalen Service als Child starten/stoppen, CSP, Navigations-/Window-Open-Schutz
- [x] Typisierte Preload-Bridge (`window.laura`, IPC-Allowlist)
- [x] `service.ts`: spawnt `uv run laura-api`, generiert Session-Token, pollt `/healthz`
- [x] Renderer: typed API-Client (Token-Header), Health-Badge, Projektliste, Projekt-anlegen (FPS-Presets inkl. DF)
- [x] Root pnpm-Workspace + `node-linker=hoisted` + `onlyBuiltDependencies`
- [x] **Verifiziert:** `tsc --noEmit` (strict) grün · `vite build` Renderer-Bundle gebaut (147 kB JS / 9,4 kB CSS) · Electron-Binary installiert (`require('electron')` löst auf)
- **Exit:** ✓ App kompiliert & bündelt; `pnpm dev` startklar. Visueller GUI-Launch noch manuell zu prüfen.

## Portion 6 — Renderer-UX: Import / Asset-View / Waveform  `[x]`
- [x] Backend: `GET /projects/{id}/assets` (Liste) + `GET /assets/{id}/files/{kind}` (Serving) — getestet
- [x] Preload `pickMediaFile()` + Main-Prozess `dialog.showOpenDialog` (Media-Filter)
- [x] API-Client erweitert: listAssets/importAsset/getAsset/getWaveform/fileObjectUrl (Blob+Token)
- [x] 3-Spalten-UI: Projekte · Medienliste + Import · Asset-Detail
- [x] Import-Flow: Datei wählen → import → Asset live pollen bis Waveform fertig
- [x] `AssetView`: Metadaten-Grid, Poster (Blob via Token), `Waveform`-Canvas
- [ ] Transcript-Panel — verschoben nach Portion 7 (braucht Analysekern)
- [x] **Verifiziert:** Backend 97 Tests + ruff/mypy clean · `tsc` strict grün · `vite build` Bundle (34 Module)
- **Exit:** ✓ Code: Klick „Import" → Asset erscheint mit Metadaten + Waveform. Visueller GUI-Lauf manuell.

## Portion 7 — Analysekern (Shots zuerst, ASR/Align/Diarize als Extras)  `[x]`
- [x] `analysis/shots.py` (PySceneDetect, lazy) — **real verifiziert** (Cut bei Frame 30 erkannt)
- [x] `analysis/asr.py` (faster-whisper, Wort-Timestamps) — **optional extra `[asr]`**, lazy
- [x] `analysis/diarize.py` (pyannote + reine `assign_speakers`) — **optional extra `[diarize]`**, lazy
- [x] `analysis/mapping.py` — Sekunden→Samples→Frames über Zeitkern (floor/ceil-Snapping), unit-getestet
- [x] `analysis/manifest.py` + `analysis_runs`-Idempotenz (`pipeline_version`)
- [x] `analysis/handlers.py` — Orchestrator `analysis.run`, **Graceful-Skip** je Stufe in Diagnostics
- [x] API: `POST /assets/{id}/analysis`, `GET …/analysis/latest`, `…/shots`, `…/transcript`
- [x] Frontend: `AnalysisPanel` (Analyse-Button, Shot-Strip, Transkript-Liste) in `AssetView`
- [~] WhisperX-Alignment + TransNetV2-Refinement — als spätere Quality-Booster vorgemerkt
- [x] **Verifiziert:** 106 Tests (inkl. echter PySceneDetect-Pipeline) · ruff/mypy strict clean · `tsc` + `vite build` grün
- **Exit:** ✓ Shots real erkannt & in DB; ASR/Diarize laufen mit Extras, sonst sauber übersprungen.

## Portion 8 — Rough-Cut-UX (transcript-first Operationen)  `[x]`
- [x] `editing/operations.py` — reine, frame-genaue Ops: append_from_words/append_clip/insert/delete/lift, end-exclusive + Ripple + Src-Trim
- [x] `POST /timelines/{id}/operations` — Ops anwenden, `timeline_clips` materialisieren, **OTIO neu schreiben** (kanonisch)
- [x] repos: `get_word`, `replace_timeline_clips`, `update_timeline_otio`
- [x] Renderer: `TimelineBar` (Rough-Cut-Clipstrip, Klick = löschen) · Shots klickbar → anhängen · Transkript-Satz „→" → append_from_words
- [x] **Verifiziert:** 121 Tests (golden Operation-Deltas + API-Roundtrip mit OTIO-Persistenz) · ruff/mypy strict clean · `tsc` + `vite build` grün
- **Exit:** ✓ Operationen erzeugen **deterministische** Timeline-Deltas; Rough Cut aus Shots/Transkript baubar, als OTIO/EDL exportierbar.

## Portion 9 — Playback (libmpv) + Härtung  `[ ]`
- [ ] libmpv-Embedding im Main-Prozess, IPC-Steuerung, frame-genaues Seek gegen CFR-Proxy
- [ ] Crash-Recovery (Reaper, halbe Exporte, Session-Reopen, Pfad-Relink via sha256)
- [ ] Perf-Dashboard (OTel/Prometheus), Timecode-Drift-Testlauf über Korpus
- **Exit:** keine Drifts, Playback stabil, Recovery funktioniert.

## Portion 10 — Release Candidate (Packaging/Signing/Pilot)  `[ ]`
- [ ] Electron-Forge-Build, Windows Signing-Pfad, macOS Notarization
- [ ] Gebündelte Python-Runtime + FFmpeg + libmpv
- [ ] Reproduzierbare Demo-Projekte, Pilot-Docs
- **Exit:** signierte Builds, Pilot-handoff-fähig.

---

### Diese Session — Zielkorridor
Portion **1 → 2 → 3** vollständig & verifiziert, dann **5** (Electron-Shell) anstoßen.
Interchange/Analyse/Rough-Cut/Playback folgen in weiteren Sessions.

### Risiken aktiv beobachten
- `[!]` WhisperX Version-Churn → isolierte uv-Extra-Gruppe, hart gepinnt.
- `[!]` FCPXML-Adapter → nur guarded + Fixtures.
- `[!]` libmpv-Electron-Bindings je OS → Phase „Härtung", früh Spike einplanen.
