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

## Portion 9 — Playback + Härtung  `[~]`  (Proxy-Player live; libmpv-nativ geplant)
- [x] **Frame-genauer Proxy-Player** (`Player.tsx`): `<video>` gegen CFR-all-intra-Proxy, Play/Pause, ±1 Frame, Scrubber, Frame-Anzeige; CSP `media-src blob:`
- [x] Crash-Recovery-Basis bereits in Portion 2 (Job-Reaper, Lease, Idempotenz) + sha256 am Asset für Relink
- [~] **libmpv-nativ** (primärer Pro-Player, ADR-0002) — als nächster nativer Spike geplant; Proxy-Player ist der verifizierte MVP-Pfad
- [ ] Perf-Dashboard (OTel/Prometheus), Drift-Testlauf über Golden-Korpus
- [x] **Verifiziert:** `tsc` strict + `vite build` grün (Player). GUI-Lauf manuell.
- **Exit (MVP):** ✓ frame-genaues Abspielen/Steppen des Proxies; libmpv-Upgrade dokumentiert.

## Portion 10 — Release Candidate (Packaging/Signing/Pilot)  `[~]`  (Config+Doku; signierte Builds brauchen Zertifikate)
- [x] `service.ts` packaged-mode: startet gebündeltes `laura-api`-Binary statt `uv run` (`app.isPackaged`)
- [x] `forge.config.ts`: appBundleId/executableName, Maker (Squirrel/ZIP), extraResource-Pfad dokumentiert
- [x] `docs/13-packaging.md`: Standalone-Service (PyInstaller), FFmpeg/libmpv-Bündelung, Signing/Notarization, Release-Checkliste
- [ ] **Signierte Builds** (`pnpm make` mit Win/macOS-Zertifikaten) — braucht echtes OS/Cert-Setup, nicht headless ausführbar
- [ ] Reproduzierbares Demo-Projekt beilegen
- **Exit (offen):** signierte Installer — Code/Config/Doku stehen, Build mit deinen Zertifikaten ausführen.

## Portion 11 — Enterprise (RBAC, Audit, Observability, Deploy)  `[x]`  (additiv & nicht-brechend)
- [x] Migration `0002_enterprise.sql`: organizations/users/memberships/api_keys/audit_events (+ projects.org_id)
- [x] `auth/` — Principal, API-Keys (sha256), Rollen→Permissions, `require_permission`-Dependency (Annotated)
- [x] Auth: Bearer-API-Key → gescopter Principal; kein Key → lokaler Owner (Desktop unverändert)
- [x] RBAC durchgesetzt auf `POST /projects` (project:write) & `POST /timelines/{id}/exports` (export:create)
- [x] `audit.py` + `audit_events` — org/user/key/project/export protokolliert; `GET /admin/audit`
- [x] Admin-API: orgs/users/keys (Klartext einmalig) + revoke
- [x] `metrics.py` — Prometheus `/metrics` + HTTP-Middleware + `laura_jobs_total` im Runner
- [x] Deploy: `Dockerfile`, `deploy/docker-compose.yml` (PG/Redis/Qdrant/MinIO), `.env.example`
- [x] CI: `.github/workflows/ci.yml` (backend ruff/mypy/pytest + desktop tsc/build)
- [x] `docs/14-enterprise.md`
- [~] Postgres/Celery/Qdrant **Code-Integration** — Infra+Extras+Doku da; Backend-Umschaltung als nächster Schritt
- [x] **Verifiziert:** 126 Tests (5 neu: RBAC/Revoke/Audit/Metrics) · ruff/mypy strict clean · 121 Bestandstests grün (nicht-brechend)
- **Exit:** ✓ Mandanten + RBAC + Audit + Metriken live; On-Prem-Stack + CI vorhanden.

## Portion 12 — Server-Mode: pluggbares DB-Backend (SQLite↔Postgres) + Celery  `[x]`
- [x] `db/base.py` — Backend-ABC (`Database`) + portable `migrate()` (statementweise) + `to_pyformat` (?→%s) + `split_statements`
- [x] `db/sqlite.py` `SqliteDatabase` (WAL, BEGIN IMMEDIATE-Claim) + `db/postgres.py` `PostgresDatabase` (psycopg, `FOR UPDATE SKIP LOCKED`)
- [x] `db/database.py` `create_database(settings)` — Backend per `DATABASE_URL`
- [x] Job-Claim aus dem Runner ins Backend verlagert (dialekt-spezifisch); Reaper portabel (kein `json_object`)
- [x] `jobs/celery_app.py` — Celery-Worker-Scaffold (`drain_jobs`, nutzt Runner gegen das konfigurierte Backend)
- [x] **Verifiziert:** 131 Tests · ruff/mypy strict clean (79 Dateien) · echter uvicorn-Boot grün (Schema v2) · SQLite-Pfad unverändert
- [x] **Live-Postgres-Test ✓** — gegen echtes Postgres (Docker `postgres:16`): Migrationen (Schema v2), CRUD über `?→%s`, `SKIP LOCKED`-Claim, Idempotenz, RBAC/Audit — **5 Tests grün, 136 gesamt**
- **Exit:** ✓ Backend per Env umschaltbar; Desktop = SQLite, Server-Mode = Postgres + Celery — **real gegen Postgres verifiziert**.

## Portion 13 — Gap-Closure Wave A (Top 5)  `[x]`  (Plan: docs/15)
- [x] **A1 FCP7-XML-Export** (`interchange/fcp7_xml.py`, xmeml v5) → `_EXT += fcp7xml`, Preflight; Premiere-Interop
- [x] **A2 Suche** — `POST /search`, portable `LOWER(text) LIKE` über Transkripte, projektscoped (FTS5/Qdrant später)
- [x] **A3 Transkript-Korrektur** — `PATCH /transcript/segments/{id}` (Text + Speaker)
- [x] **A4 CRUD** — `DELETE` Projekte/Assets/Timelines + `PATCH` Rename (Projekte/Timelines), RBAC `project:delete`, Audit
- [x] **A5 Multi-Tenancy durchgesetzt** — Projekt-`org_id` aus Principal; List/Get nach Org gefiltert; Cross-Tenant → 404
- [x] **Verifiziert:** 138 Tests (+7: FCP7, Suche, PATCH, CRUD, Tenant-Isolation) · ruff/mypy strict clean (84 Dateien)
- [x] Frontend-Anbindung: Suchbox (Transkript), Delete-Buttons (Projekt/Asset), Export-Menü (OTIO/EDL/FCP7-XML/FCPXML)
- **Exit:** ✓ Top-5 Backend geschlossen + getestet; Frontend angebunden.

## Portion 14 — Gap-Closure Wave B (laufend)  `[~]`
- [x] **FCPXML-Export** (`interchange/fcpx_xml.py`, guarded + Preflight-Warnung) → `_EXT/_WRITERS += fcpxml`
- [x] **Captions-Qualität** — Zeilenumbruch (≤42 Zeichen) in SRT/VTT
- [x] **Shot-Thumbnails** — `thumbnail_path` befüllt (ffmpeg-Frame je Shot) + `GET /shots/{id}/thumbnail`; real verifiziert
- [x] **Verifiziert:** 140 Tests (+ Thumbnail-Assert) · ruff/mypy strict clean (86 Dateien) · App `tsc`/`vite build` grün
- [ ] Editorial-Import, Timeline-Captions, Speed/Retiming, origin_word-Link, Pagination, Rate-Limiting, Queue-Routing — siehe docs/15

---

### Diese Session — Zielkorridor
Portion **1 → 2 → 3** vollständig & verifiziert, dann **5** (Electron-Shell) anstoßen.
Interchange/Analyse/Rough-Cut/Playback folgen in weiteren Sessions.

### Risiken aktiv beobachten
- `[!]` WhisperX Version-Churn → isolierte uv-Extra-Gruppe, hart gepinnt.
- `[!]` FCPXML-Adapter → nur guarded + Fixtures.
- `[!]` libmpv-Electron-Bindings je OS → Phase „Härtung", früh Spike einplanen.
