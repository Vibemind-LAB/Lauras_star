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
- [ ] Editorial-Import, Timeline-Captions, Speed/Retiming, origin_word-Link, Pagination, Rate-Limiting, Queue-Routing — sequenziert in Portion 15, siehe [`../docs/16-execution-plan.md`](../docs/16-execution-plan.md)

## Portion 15 — Wave-B Backend-Abschluss  `[x]`  (Plan: docs/16 §1)
- [x] 15.1 **Pagination** — `limit/offset` (defensiv geklemmt 1..200) + `X-Total-Count`-Header auf projects/assets/timelines; portabel (SQLite+PG); 5 Tests, **145 grün**
- [x] 15.2 **Rate-Limiting** — Token-Bucket-Middleware pro Key/Host → `429 + Retry-After`; per Default aus (`rpm=0`), Env-konfigurierbar, `/healthz`+`/metrics` exempt; 4 Tests, **149 grün**
- [x] 15.3 **Speed/Retiming** — `set_speed`-Op (Zeitkern-`retimed_seq_length`, Ripple), Migration 0003 (`speed_num/den`, v2→v3), Clip/ClipOut + OTIO `LinearTimeWarp` + exakter Metadaten-Round-Trip; EDL-Preflight flaggt Speed; 9 Tests, **160 grün**
- [x] 15.4 **Timeline-Captions** — `GET /timelines/{id}/captions.{srt,vtt}`: 1 Cue je Transkript-Clip, getimt an **Sequence**-Position (Clip→origin_words→`get_words_in_range`); 4 Tests, **164 grün**
- [x] 15.5 **Editorial-Import** — `POST /projects/{id}/timelines/import` (OTIO lesen, Relink via source_path, Offline-Platzhalter `online=0`, Migration 0004 v3→v4, Speed erhalten); 4 Tests, **168 grün**. EDL/FCP7-Import = Follow-up (kein Reader)
- [x] 15.6 **origin_word-Link (Backend)** — `ClipOut` zeigt `origin_word_*`; `GET /timelines/{id}/clips/{cid}/source` löst Clip → Segment + Quell-Frames auf (Rücksprung); 2 Tests, **151 grün**
- [x] 15.7 **Queue-Routing** — `jobs/queues.py` (CPU/GPU-Gruppen + `queue_for`); Claim ordnet bereits `priority DESC`; `analysis.align/embed`-Stubs (graceful-skip, GPU-Queue); Celery-Beat-Reaper (`laura.reap_expired`); 5 Tests, **173 grün**
- **Exit:** ✓ Backend funktional vollständig; je Position pytest + ruff + mypy strict grün. **Portion 15 komplett.**

## Portion 16 — Wave-B/C Frontend  `[x]`  (Plan: docs/16 §1)
- [x] 16.1 **Shot-Thumbnail-Strip** — `shotThumbnailUrl` (Blob+Token), `ShotThumb` (Object-URL + Cleanup), scrollbarer Vorschau-Strip mit Fallback-Balken; `tsc` strict + `vite build` grün (GUI manuell)
- [x] 16.2 **origin_word-Highlight** — Wort-Level-Transkript: Klick → Player-Sprung (`seekTo`), Playhead hebt aktives Wort/Segment hervor (`onFrame`→`currentFrame`); `tsc`+Build grün (GUI manuell)
- [x] 16.3 **Player JKL/Shuttle** — J/K/L (L vorwärts 1/2/4×, J Rückwärts via Intervall, K Stop), ←/→ Frame, Shift+←/→ Sekunde, Home/End, Leertaste; fokussierbarer Container + Shuttle-Anzeige; `tsc`+Build grün (GUI manuell)
- [x] 16.4 **Undo/Redo + Trim/Split/Insert-UI** — Backend: `split`/`trim`-Ops + `PUT /timelines/{id}/clips` (Snapshot-Restore), 7 Tests, **180 grün**. Frontend: Clip-Selektion, Undo/Redo-Stack (setClips), Split/Trim±/Duplizieren/Löschen; `tsc`+Build grün (GUI manuell)
- [x] 16.5 **Desktop-Tests-Setup** — Vitest v2 (Vite-5-kompatibel) + Testing-Library + jsdom; `vitest.config.ts`, `test`-Script, 4 Tests (hasFile + TimelineBar-Render) grün; CI-Schritt `pnpm test` ergänzt
- **Exit:** ✓ UI bedienbar; `tsc`/`vite build`/Vitest grün; GUI-Sicht manuell. **Portion 16 komplett.**

## Portion 17 — Observability & Betrieb  `[x]`  (Plan: docs/16 §1)
- [x] 17.1 **OpenTelemetry-Tracing** — `telemetry.py` (No-op-sicheres `span()`, `configure_tracing()` für OTLP), Job-Runner `job.execute`-Span (kind/queue/status); optionales `[otel]`-Extra, Backend läuft ohne (Graceful no-op **bewiesen**); In-Memory-Exporter-Test, 2 Tests, **187 grün**; CI `--extra otel`
- [x] 17.2 **Postgres-RLS** — `db/rls.sql` (FORCE RLS auf projects, GUC `app.current_org`), `PostgresDatabase.apply_rls()`/`set_org()`; SQLite unberührt. **Live-PG verifiziert** als Nicht-Superuser-Rolle: Cross-Org-Read = 0 (6 PG-Tests grün). API-Layer-Filter bleibt primärer Guard; RLS = Defense-in-Depth
- [x] 17.3 **Demo-Projekt + Golden-Fixtures** — `fixtures/golden/` (EDL/FCP7-XML/SRT/VTT) byte-genau vs. kanonischer Demo-Schnitt, `LAURA_REGEN_GOLDEN=1` zum Neu-Erzeugen, OTIO per Round-Trip; `.gitattributes` schützt CRLF; 5 Tests, **185 grün**
- **Exit:** ✓ betriebsreif; Live-PG/FFmpeg/OTel-In-Memory-verifiziert. **Portion 17 komplett — alle headless-baubaren Portionen (15–17) abgeschlossen.**

## Portion R0 — Reel-Render-Skelett (vertikal 9:16 + Hook + Kennzeichnung)  `[x]`  (Plan: docs/superpowers/plans/2026-06-09-R0-reel-render-skeleton.md)
> Erstes fertiges, gekennzeichnetes Reel — **null KI, null neue schwere Deps**. Additive Erweiterung des Render-Pfads.
- [x] **R0.1** `render/reel.py` — reiner Filter-Builder `reel_video_chain` (crop=ih*9/16:ih,scale=1080:1920 + drawtext Hook/Disclosure, escaped) + `resolve_font` (Drive-Colon doppelt-escapen). 4 Unit-Tests. `b4fd258`
- [x] **R0.2** `render_clips_mp4` Reel-Kwargs (`vertical`/`hook_text`/`disclosure_text`, Concat→`[vcat]`→Reel-Chain→`[out]`); Default off = byte-identisch. ffprobe-Test 1080×1920. `1524143`
- [x] **R0.3** Migration `0013_export_options.sql` (`exports.options TEXT`, additiv) + `create_export(options=)` json.dumps + `get_export`/`list_exports` json.loads→`{}`. `6678675`
- [x] **R0.4** `handle_render` liest `exp["options"]` → reicht Reel-Params an `render_clips_mp4` durch (leer = unverändert). Monkeypatch-Test. `d5144f2`
- [x] **R0.5** `POST /timelines/{id}/render-reel` in **eigenem Router** `api/reels.py` (in `main.py` gemountet, `timelines.py` unberührt) + `ReelRenderRequest`. API-Test (202 + options round-trip + 404). `30cbbd9`
- [x] **R0.6** Frontend: `api.ts renderReel` (mirror renderTimeline) + ExportView „Reel 9:16"-Preset (Hook-Input + Kennzeichnungs-Toggle). tsc clean. `e04bd36`
- [x] **R0.7** E2E `test_reel_e2e.py`: Endpoint→Job→`handle_render`→echtes ffmpeg → **ffprobe 1080×1920** + options round-trip. Volle Suite grün, tsc clean. `1b25572`
- [x] **R0.8** **Härtung (adversarial entdeckt):** Inline `drawtext text='…'` zerbrach an Apostroph/Quote (z. B. „Geht's los" → Render-Fail) + war Filtergraph-Injection-Vektor. Fix: Overlay-Text über `drawtext textfile=` (Basename) + `run_ffmpeg(cwd=dest.parent)` — Text wird wörtlich gelesen, **null Content/Path-Escaping**, Injection unmöglich. Volle Matrix (Apostroph/Doppelpunkt/Komma/%/Unicode/Backslash) gegen echtes ffmpeg grün; `_escape_drawtext` entfernt; Temp-Textdateien im `finally` aufgeräumt. Regressions-Tests (`test_reel_render`/`test_reel_e2e` mit „Geht's los, jetzt: 100%!"). Plain-Concat-Pfad byte-identisch (cwd nur im Reel-Pfad).
- **Exit:** ✓ Backend+Render End-to-End mit echtem ffmpeg verifiziert (1080×1920, beliebiger Hook-Text inkl. Apostroph/Sonderzeichen, Hook/Disclosure fehlerfrei). **Manuell zu prüfen (nur noch):** Live-Klick auf „Reel 9:16" in ExportView (Renderer→Endpoint identisch mit getestetem Pfad) + visuelle Textplatzierung im Output-Frame.

## Portion R1 — Reel-Captions (eingebrannte Karaoke-Wort-Captions)  `[x]`  (Plan: docs/superpowers/plans/2026-06-09-R1-reel-captions.md)
> Wort-genaue, eingebrannte Captions (Karaoke-`\kf`) via ASS/libass im 9:16-Filtergraph. GPU-frei, **keine neue schwere Dep** — Wort-Timings aus den vorhandenen Transcript-`segments`.
- [x] **R1.1** Reiner ASS-Karaoke-Builder `render/captions.py` (`build_ass`): PlayRes 1080×1920, pro Zeile Dialogue mit `\kf`-Karaoke; Integer-Frames durchgängig, Centisekunden nur am Format-Rand (reine Integer-Arithmetik, kein Float-Drift); ASS-Escaping. 16 Unit-Tests. `4a78ce5`
- [x] **R1.2** `render_clips_mp4` optionales `caption_ass` → ASS neben dest schreiben + `ass=<basename>` an Post-Concat-Chain (wiederverwendet R0.8 cwd/Basename+`finally`-Cleanup); `[vcat]`-Zweig greift auch captions-only. Echter `ass=`-Render (skippt ohne libass) → 1080×1920. `af5a42b`
- [x] **R1.3a** `captions_source.timeline_caption_words` (Transcript→Sequenz-Frames via Clip-src→seq-Affin, verlustfrei; out-of-clip droppen; Punktuation in Vorwort mergen; Run wie analysis.py) + reine `group_caption_lines` (max Wörter/Zeile + Gap-Break). 7 Tests. `981264d`
- [x] **R1.3b** `ReelRenderRequest.captions` → Endpoint→options; `handle_render` baut bei `captions=true` die ASS (`timeline_caption_words`→`group_caption_lines`→`build_ass`@Sequenz-Rate) → `caption_ass`. Leer ⇒ None ⇒ unverändert. `45a39b3`
- [x] **R1.3c** Frontend: `api.ts renderReel`+`captions`; ExportView „Untertitel (Captions) einbrennen"-Toggle (Default **an**) + Transcript-Hinweis. tsc clean. `f6d8e79`
- [x] **R1.4** E2E `test_caption_e2e.py`: Endpoint→Job→`handle_render`→ASS aus **echtem** Transcript→`ass=`-Burn → ffprobe 1080×1920 + options round-trip + Cleanup. Volle Suite + tsc grün. (lokaler Commit folgt)
- **Caption-Stil-Entscheidung (User):** Karaoke-Highlight. Umfang: Voll (Auto aus Transcript + Toggle).
- **Exit:** ✓ Auto-Karaoke-Captions End-to-End (UI→Endpoint→Job→eingebranntes MP4), gegen echtes ffmpeg+libass verifiziert.

## Portion RL — Replacement-Lane (Source-Replace-Primitive)  `[x]`  (Spec/Plan: docs/superpowers/2026-06-09-replacement-lane-{design,plan})
> Erstes Bau-Teil des **R3-Programms** (Identitäts-Ebene): nicht-destruktive Source-Replace-Primitive, auf die **R3-C Reenact** (LivePortrait) als Adapter aufsetzt. Brainstorming-Entscheidungen: Reenact zuerst · Sidecar-HTTP · timeline-range-getrieben · Replacement-Lane+Render-Vorrang · opak · beide Tabs.
- [x] **RL1** Migration `0014_clip_role.sql` (`timeline_clips.role` 'base'|'replace', additiv) + Repos (`add_timeline_clip(lane,role)`, `update_timeline_clip_role`, `delete_timeline_clip`). `35d5ff4`
- [x] **RL2** Reine `apply_overlay_precedence` (zeit-aligned opaker Replace: Base an Overlay-Grenzen splitten, 1:1-src-Mapping, verdeckte Frames übersprungen, nach seq_in sortiert). 7 Tests. `8b2bbda`
- [x] **RL3** `resolve_clip_rows` am **einzigen Choke-Point** verdrahtet (Szene: role-Split; Sequenz: flatten-Base + Sequenz-Overlays) → Präzedenz; keine Overlays ⇒ byte-identisch. Echter ffmpeg-Farbprobe-Test (grün→rot→grün) + Regression. `adb851f`
- [x] **RL4** Overlay-API `POST/DELETE /timelines/{id}/overlays` (eigener Router, `main.py` gemountet, `timelines.py` unberührt) + Validierung (Range/Asset-Länge). 10 Tests, volle Suite grün. `35dd7fa`
- [x] **RL5** Frontend: `api.ts setOverlay/removeOverlay` + additive **V2-Lane** in geteilter TimelineBar (beide Tabs) + `OverlayControls` in AssembleView. Adversarialer Review fand 3 echte Bugs (stale assetId, still-geschluckter Fehler, Nicht-Integer-Frames) → alle gefixt. tsc clean. `3c9626e`
- **Exit:** ✓ Nicht-destruktive opake Source-Replace in beiden Tabs, real per ffmpeg-Farbprobe verifiziert; volle Suite + tsc grün; geteilte TimelineBar additiv (alte Lanes/Playhead intakt). **Limit v1:** Speed≠1-Base unter Overlay nicht gesplittet (dokumentiert); transparente B-Roll-Overlays + Übergänge = spätere Multi-Lane-Phasen.

## Portion R3-C — Reenact-Skelett (konsentiert, gekennzeichnet)  `[~]`  (Spec/Plan: docs/superpowers/2026-06-09-r3c-reenact-{design,plan}) — **Adapter fertig; LivePortrait-Sidecar = User-Install**
> Konsentierter, gekennzeichneter Reenact über die Replace-Overlay-Primitive. Dep-freies Skelett mit **Stub-Backend** (kein echtes Modell, sichtbar markiert) — voll testbar; echtes LivePortrait läuft als externer Sidecar, damit Laura GPU-frei startbar bleibt.
- [x] **RC1** Migration `0015` (`media_assets.synthetic`/`ai_effect` + `consent_records`) + Repos (consent CRUD, `set_asset_synthetic`). `e172397`
- [x] **RC2** Pluggbares `ReenactBackend`-Protocol + `StubReenactBackend` (ffmpeg-Platzhalter „REENACT (stub)", längen-erhaltend) + `resolve_reenact_backend` (env/Default `stub`; null schwere Imports). `8b3a012`
- [x] **RC3** `ai.reenact`-Job: **Consent-Gate zuerst** (verweigert fehlend/unbekannt/**widerrufen**, legt nichts an) → Driving aus **Original-Base** (nie aus früheren synthetischen Overlays) → Stub → `synthetic`-Asset → Replace-Overlay. Adversarial-Review-Härtung (4 Befunde: Migration `0016` Consent-Widerruf, Provenienz, kein verwaistes synthetic-File). `6db9366`
- [x] **RC4** Consent-/Reenact-API (eigener Router): Consent create/list/**revoke**; `POST /timelines/{id}/reenact` mit Pflicht-`consent_id` + Revoke-Ablehnung am API-Layer (Defense-in-Depth). `b18166f`
- [x] **RC5** Frontend `ReenactPanel` in AssembleView: Consent-Bestätigung (Schritt 1) → Reenact-Button **erst nach Consent aktiv**; Integer-Frames (≥0 geklemmt); Stub-/synthetic-Hinweis. Review fand 0 Bypass; 2 Hygiene-Fixes. tsc clean. `5cd2833`
- [x] **RC6** Gesamt-Verifikation: volle pytest-Suite + tsc grün; Consent-Gate adversarial bestätigt (kein Bypass, Backend + UI).
- [x] **RC7** `LivePortraitBackend` als HTTP-Sidecar-Adapter: `GET /healthz`, `POST /reenact` multipart (`driving`, `portrait`, `fps_num`, `fps_den`) → MP4-Bytes; `LAURA_LIVEPORTRAIT_URL`/Timeout; UI-Backend-Auswahl Stub/LivePortrait. Tests: Sidecar-Fake + Renderer-Select grün.
- **Exit:** ✓ Konsentierter, gekennzeichneter Reenact-Pfad End-to-End mit Stub **oder LivePortrait-Sidecar** (UI→API→Job→synthetic-Asset→Overlay), 2× adversarial reviewed + gehärtet. **⛔ WAND (braucht dich):** Sidecar-Prozess + Modell-Download/Gewichte auf der RTX 3060 starten. Laura importiert weiterhin keine schweren Modelle. **Weitere R3-Teile danach:** Face-Probe · 2. Kennzeichnungs-Ebene (Video Seal/C2PA) · Swap-Backend · Qualität/Eval (LSE/ArcFace/LPIPS).

## Portion AV2 — Assemble Workspace v2: Transcript-first Editing  `[x]`
> `Zusammenfügen` als leichter 3-Spalten-Arbeitsbereich: links Szenen-Bin, Mitte Sequenz-Player/Timeline/Storyboard, rechts Transkript + Tools. Kein Rewrite-LLM in v1; manuelle Original-Transcript-Korrektur zuerst.
- [x] **AV2.1 Sequence-Transcript API** — `GET /sequences/{id}/transcript` liefert Transcript-Blöcke in Sequenz-Reihenfolge inkl. Source-/Sequence-Frames und Wort-Mapping; pure Mapper-Tests.
- [x] **AV2.2 Transcript-Edit + Re-Align Job** — Frontend-Client für `PATCH /transcript/segments/{id}`; `POST /assets/{id}/transcript:realign` validiert Segment-Zugehörigkeit und routet `transcript.realign` auf die GPU-Queue; Handler ersetzt Wörter via vorhandenes WhisperX-Alignment, mit klaren Fehlern bei fehlendem Audio/WhisperX.
- [x] **AV2.3 Workspace UI** — `AssembleView` auf 3 Spalten umgebaut; Replace/Reenact liegen im rechten `Tools`-Tab; `Transkript`-Tab editiert Sequenz-Blöcke inline und startet Re-Alignment; Preview zeigt aktive Caption als Overlay.
- [x] **Verifiziert:** Backend full `uv run pytest -q` grün; scoped Backend `ruff`/`mypy` grün; Desktop full `pnpm test`, `tsc --noEmit`, `build:renderer` grün. Full `ruff check` hat bestehende Alt-Test-Lints außerhalb dieser Scheibe.
- **Exit:** ✓ Transcript-first Zusammenfügen ist bedienbar und testgedeckt; Export nutzt weiterhin den bestehenden Captions-/Transcript-Pfad aus den gespeicherten Segmenten. **Follow-ups:** Sprache nicht hart `en`, Re-Align-Status persistent anzeigen, Caption-Stil-Auswahl, Rewrite-Modell als separater Anschluss.

## Portion AV3 — Assemble Produktreife 1–5  `[x]`
> Erste integrierte Reife-Scheibe über die priorisierten Punkte 1–5: UX-Polish, Re-Align-Status, Caption-Kontrolle, AI-Sichtbarkeit und Editorial-Hilfen.
- [x] **AV3.1 Transcript-UX** — Raw-API-Fehler (z. B. 404 JSON) werden im rechten Panel in klare Editor-Zustände übersetzt; aktive Sequenz-Caption markiert den passenden Transcript-Block.
- [x] **AV3.2 Re-Align-Status** — `api.ts getJob` nutzt den bestehenden `/jobs/{id}`-Endpoint; nach `Speichern + neu ausrichten` zeigt das Panel Job-/Abschlussstatus statt nur fire-and-forget.
- [x] **AV3.3 Caption-Preview-Kontrolle** — Caption-Overlay im Sequence-Player lässt sich im Workspace ein-/ausblenden, ohne Transcript-Editing zu deaktivieren.
- [x] **AV3.4 AI/LivePortrait-Sichtbarkeit** — `Tools`-Tab zeigt einen KI-Statushinweis: Stub lokal verfügbar, LivePortrait nur bei laufendem Sidecar/Gewichten.
- [x] **AV3.5 Editorial-Hilfen** — Sequenz-Gesamtdauer in Frames sichtbar; Storyboard-DnD bekommt eine klare Einfügemarke beim Drag-over.
- [x] **Verifiziert:** TDD rot→grün (`AssembleView.test.tsx`, `api.test.ts`), `pnpm --dir apps/desktop test` 116 grün, `pnpm --dir apps/desktop exec tsc --noEmit` grün, `pnpm --dir apps/desktop run build:renderer` grün.
- **Exit:** ✓ `Zusammenfügen` leakt weniger Technik, zeigt Bearbeitungs-/AI-/Caption-Zustände sichtbarer und gibt Editor:innen erste Sequenz-Metadaten.

## Portion AV4 — Persistenter Re-Align-Zustand + Sprache  `[x]`
> Transcript-Edits haben nun einen dauerhaften Alignment-Lifecycle am Segment; die UI kann nach Neustart zeigen, ob Timing alt ist, läuft oder fehlgeschlagen ist.
- [x] **AV4.1 Persistenter Segment-State** — Migration `0017_transcript_alignment_state.sql` ergänzt `alignment_status`, `alignment_job_id`, `alignment_language`, `alignment_error`, `alignment_updated_at`; Text-PATCH setzt Segmente auf `stale`.
- [x] **AV4.2 Re-Align-Lifecycle** — `POST /assets/{id}/transcript:realign` setzt betroffene Segmente auf `aligning`; Worker setzt nach WhisperX-Erfolg `aligned` und bei fehlendem Audio/WhisperX/Runtime-Fehler `failed` mit Ursache.
- [x] **AV4.3 Sprache smarter** — Re-Align nutzt `body.language` nur als Override; ohne Override kommt die Sprache aus der letzten Analysis-Config, Fallback `en`.
- [x] **AV4.4 UI-Status** — Sequence-Transcript-Blöcke zeigen persistent `Nicht neu aligned`, `Alignment läuft` oder `Alignment fehlgeschlagen`, inkl. Sprache und Fehlerursache; Frontend sendet keinen harten `en`-Override mehr.
- [x] **Verifiziert:** `uv run pytest tests/test_sequence_transcript.py -q`; scoped `ruff`/`mypy`; `pnpm --dir apps/desktop test -- src/components/AssembleView.test.tsx src/api.test.ts`; `pnpm --dir apps/desktop exec tsc --noEmit`; `pnpm --dir apps/desktop run build:renderer`.
- **Exit:** ✓ Transcript-Korrektur ist restart-fähig sichtbar: Text bleibt gespeichert, Timing-Zustand bleibt am Segment. **Follow-ups:** Caption-Export-Regie, Job-Zentrale, Audio-Spuren/Ducking, Transitions.

## Extern blockiert  `[!]`  (Plan: docs/16 §2 — braucht deine Ressourcen)
- [x] **GPU (CUDA)** — **aktiviert & verifiziert** auf RTX 3060: torch/torchaudio via PyTorch-`cu128`-Index (`[tool.uv.sources]`, persistent über `uv sync`), `analysis/device.py` wählt Device (`LAURA_ASR_DEVICE` überschreibt); ASR+Align+Diarisierung laufen auf CUDA, CPU-Pfad bleibt grün. cuBLAS/cuDNN kamen über torch-cu128-Deps
- [x] **ASR (faster-whisper)** — **real freigeschaltet & verifiziert**: `transcribe` mit `device`-Param + **CPU-Fallback** bei fehlendem cuBLAS; gated Test (ffmpeg-`flite`-Sprache → korrekte Wörter + Zeitkern-Mapping), **188 grün**
- [x] **Diarisierung (pyannote)** — **real freigeschaltet & verifiziert** (HF-Token via Browser, 3 gated Repos akzeptiert): `token=`-API, vor-dekodierte Waveform (umgeht torchcodec), `DiarizeOutput.speaker_diarization` (pyannote 4.x); gated Test erkennt **2 Sprecher** + `assign_speakers`. Modell-Cache auf `E:`
- [x] **WhisperX-Alignment** — **real freigeschaltet & verifiziert** (whisperx 3.8.6, kein torch-Konflikt): `align_words` (wav2vec2-Forced-Alignment, CPU, stdlib-`wave`-Loader), optionale `stages.align`-Stufe; gated Test (9 Wörter, tight/monoton)
- [x] **Qdrant-Semantik** — **real freigeschaltet & verifiziert**: `[semantic]`-Extra (qdrant-client + fastembed, In-Memory/`QDRANT_URL`), Auto-Index der Transkript-Segmente, `POST /search` `mode=semantic` (Vektor-Ranking + Score, Fallback lexikalisch); 3 Tests
- [!] **libmpv nativ** — nativer Build-Toolchain + GUI (Proxy-Player ist verifizierter MVP)
- [!] **Signierte Builds** — Win-Code-Signing-Cert + Apple-Developer-ID/Notarization
- [!] **Auto-Update** — Release-/Update-Server (+ signierte Builds)
- [!] **VibeVideo-Repos als optionale Sidecars** — `Flissel/vibevideo` (MIT: Pipeline,
  Sora/Vision, Product-Demo, TTS/STT) und `Flissel/vibevideo-deepfake` (proprietär laut Repo:
  Voice cloning + Lipsync/Deepfake) sind als externe Integrationsquellen in
  `docs/superpowers/specs/2026-06-09-ai-effects-integration-plan.md` verankert; Feature-Audit:
  `docs/superpowers/specs/2026-06-14-vibevideo-feature-audit.md`; akzeptiertes Integrationsdesign:
  `docs/superpowers/specs/2026-06-14-vibevideo-laura-integration-design.md`. Kein Code-Copy in
  Lauras Kern; Deepfake nur nach Lizenz-/Consent-Gate.

## VibeVideo-Integration  `[~]`  (Design: docs/superpowers/specs/2026-06-14-vibevideo-laura-integration-design.md)
- [x] **VV1 Audio-Lane v1** — `timeline_audio_clips` (Migration 0018) + CRUD-API,
  strukturierte `AudioOverlay`s im MP4-Renderpfad, Gain + einfache Fade-in/out,
  Assemble-Tools-Control und sichtbare A2-Spur in `TimelineBar`. Verifiziert:
  `test_timeline_audio_clips`, `test_render_audio_overlays`, `test_render_handler_options`,
  `test_render_music`, Desktop `api`/`AudioLaneControls`/`TimelineBar`/`AssembleView`, `tsc`.
  **AV-Audio-Pro Update:** MP4-Render bewahrt Source-Originalton, A2-Clips koennen
  `mix`/`replace_original`/`mute_original` und Ducking-Prozent setzen; UI zeigt Modus und
  Ducking. Verifiziert: fokussierte Render-/Audio-API-/UI-Tests, scoped ruff/mypy, `tsc`.
  **Follow-up:** Keyframes und Spur-level-Presets.
- [x] **VV2 Voiceover/TTS v1** — `ai.voiceover` Job + `VoiceoverBackend` (`stub` + HTTP-Sidecar),
  Button `Stimme erzeugen` im Transcript-Panel, synthetisches WAV-Asset (`ai_effect=voiceover`)
  wird framegenau als A2-Clip platziert. Verifiziert: `test_voiceover`, scoped ruff/mypy,
  Desktop `api`/`AssembleView`, `tsc`. **Follow-up:** echte VibeVideo/Chatterbox/Fish-Sidecar-
  Runtime anschliessen, Voice-ID/Reference-Audio/Consent fuer Personenstimmen.
- [x] **Job-/Export-Zentrale v1** — `GET /jobs`, `POST /jobs/{id}/cancel`,
  `POST /jobs/{id}/retry`; Header-Drawer `JobCenter` zeigt laufende/fehlgeschlagene Jobs,
  Fehlerursachen, Retry und Cancel. Verifiziert: `test_jobs_api`, scoped ruff/mypy,
  Desktop `api`/`JobCenter`, `tsc`.
- [x] **Caption-Export-Regie v1** — Reel-Export-Optionen fuer Presets (`reels`/`tiktok`/
  `shorts`/`wide`), Normal/Karaoke, Position, Groesse und Safe-Zone; `build_ass` rendert
  Preview-nahe ASS-Parameter und `ExportView` sendet sie explizit. Verifiziert:
  `test_captions_ass`, `test_reel_render_api`, `test_render_handler_options`,
  Desktop `api`/`ExportView`, scoped ruff/mypy, `tsc`.
- [x] **Transitions v1** — Storyboard-Boundary-State auf `sequence_items`
  (`hard`/`dip_black`/`fade_black`/`crossfade`), PATCH-API und kompakter Chip zwischen
  Szenen im Assemble-Storyboard. MP4-Export uebergibt Boundary-Transitions an den Renderer;
  Dip/Fade-to-black werden als finale Fade-Filter gerendert. Verifiziert:
  `test_sequences_api`, `test_render_handler_options`, `test_render_mp4_filter`,
  Desktop `api`/`AssembleView`, scoped ruff/mypy, `tsc`. **Follow-up:** echter xfade-
  Crossfade mit ueberlappenden Scene-Streams und Audio-Crossfade.
- [x] **Demo-Projekt + Version-Guard v1** — `POST /projects/demo` erzeugt ein lokales
  Demo-Projekt mit synthetischen Clips, Assets, Rough-Cut, Szenen, Sequenz und Dip-Transition;
  Header-Button `Demo` legt es per One-click an. `HealthBadge` vergleicht Backend-Schema mit
  der erwarteten Renderer-Schema-Version und meldet Backend-/Frontend-Mismatch sichtbar.
  Verifiziert: `test_api_projects`, Desktop `App`/`api`, full `uv run pytest -q`,
  full `uv run ruff check .`, `uv run mypy src`, full Desktop `pnpm test`,
  `tsc --noEmit`, `build:renderer`. Full `mypy src tests` bleibt durch bestehende
  Test-Typisierungsschulden rot.
- [x] **VV3 Sync Guard** — Framecount-basierte A/V-Drift-Pruefung + optionaler
  Duration-Fix fuer Exporte und Sidecar-Outputs. `render/sync.py` prueft Video-Frames
  und Audio-Dauer gegen die erwartete Sequence-Laenge; Export-, Reenact- und Voiceover-
  Jobs normalisieren bei Drift einmal per ffmpeg und pruefen danach erneut, bevor Assets/
  Exporte als gueltig markiert werden. Verifiziert: `test_sync_guard`, Render/Reel/Caption-
  E2E, Reenact-Job, Voiceover-Job, full ruff, `mypy src`.
- [x] **VV4 Product-Demo Assistant** — Screenrecording analysieren, Szenen/Labels/Voiceovertext als
  editierbaren Laura-Sequenz-Draft uebernehmen. Backend `demo_drafts` speichert Draft-Items,
  `demo.analyze` erzeugt Shot-/Transcript-basierte Vorschlaege, PATCH editiert Labels/
  Voiceovertexte, Apply baut Szenen + Sequenz. Desktop `DemoAssistantPanel` sitzt im
  Assemble-Tools-Rail und aktualisiert Sequenz/Storyboard/Transcript nach Apply. Verifiziert:
  `test_demo_drafts`, full `uv run pytest -q`, full `uv run ruff check .`, `uv run mypy src`,
  Desktop `api`/`DemoAssistantPanel`/`AssembleView`, full `pnpm --dir apps/desktop test`,
  `pnpm --dir apps/desktop exec tsc --noEmit`, `pnpm --dir apps/desktop run build:renderer`.
  **Follow-up:** Draft-Voiceovertexte optional direkt als A2-Voiceover-Jobs materialisieren.
- [x] **VV5 Lipsync/Deepfake** — Consent-/Lizenz-gated Sidecar mit Face-/Mouth-Probe,
  synthetischer Kennzeichnung und Quality-Gate. Backend `ai.lipsync` rendert die gewaehlte
  Sequenz-Range, nimmt ein Audio-/Voiceover-Asset, prueft `license_accepted`, nicht widerrufenen
  Consent, Sidecar-Verfuegbarkeit, Probe (`face_detected`, `mouth_visible`, `audio_present`) und
  Quality-Metriken (`sync_score`, `mouth_score`, `temporal_score`) vor Asset-Registrierung.
  Output wird als `synthetic`/`ai_effect=lipsync` registriert und als Replace-Overlay platziert;
  Stub bleibt sichtbar markiert, echter VibeVideo/MuseTalk/Wav2Lip-Pfad laeuft nur als optionaler
  HTTP-Sidecar. Desktop `LipsyncPanel` sitzt im Assemble-Tools-Rail mit Consent-Schritt,
  Lizenz-Checkbox, Audio-Auswahl und Backend-Wahl. Verifiziert: `test_lipsync_job`,
  `test_lipsync_api`, full `uv run pytest -q`, full `uv run ruff check .`, `uv run mypy src`,
  Desktop `api`/`LipsyncPanel`/`AssembleView`, full `pnpm --dir apps/desktop test`,
  `pnpm --dir apps/desktop exec tsc --noEmit`, `pnpm --dir apps/desktop run build:renderer`.
  **Follow-ups:** echten Sidecar samt Modellgewichten installieren, staerkere Mouth-/Identity-
  Quality-Metriken (SyncNet/ArcFace/LPIPS), zweite Kennzeichnungs-Ebene (C2PA/Video Seal).
- [x] **VV6 AI Provenance Manifest v1** — Dep-freie zweite Kennzeichnungsebene fuer
  synthetische Medien: Voiceover/Reenact/Lipsync schreiben neben dem erzeugten Media-File ein
  `.laura-provenance.json` mit Schema, Asset-/Projekt-ID, `synthetic`, `ai_effect`, SHA-256,
  Quelle/Range und Job-Kontext. Die Asset-API liefert `synthetic`/`ai_effect` bis in den Renderer;
  die Medienliste markiert KI-Assets sichtbar. Verifiziert: `test_ai_provenance`,
  Voiceover/Reenact/Lipsync-Jobtests, full `uv run pytest -q`, full `uv run ruff check .`,
  `uv run mypy src`, full `pnpm --dir apps/desktop test`,
  `pnpm --dir apps/desktop exec tsc --noEmit`, `pnpm --dir apps/desktop run build:renderer`.
  **Follow-up:** echtes C2PA/Video-Seal-Embedding als optionaler Signatur-/Manifest-Adapter.
- [x] **VV7 AI Provenance Inspector v1** — `GET /assets/{id}/provenance` liest das
  `.laura-provenance.json` Sidecar sicher aus, validiert die `asset_id` gegen das angefragte
  Asset und liefert das Manifest fuer lokale UI-Inspektion. `LauraClient.getAssetProvenance`
  verdrahtet den Endpoint; die Medienliste laedt Provenance nur fuer das ausgewaehlte
  synthetische Asset und zeigt Schema, Effekt und kurzen SHA-256-Fingerprint. Verifiziert:
  `test_api_assets.py::test_get_asset_provenance_returns_manifest`, `test_ai_provenance`,
  Desktop `api`/`MediaSidebar`, scoped ruff/mypy, `tsc`.
  **Follow-up:** Provenance in Export-Reports aufnehmen und spaeter C2PA/Video-Seal einbetten.
- [x] **VV8 Echte AI-Runtime-Sidecars** — Voice/Piper, LivePortrait und MuseTalk laufen als
  optionale HTTP-Sidecars ausserhalb des Laura-Core. `services/ai-runtimes` kapselt den
  Runtime-Server und Provider-Runner; `deploy/ai-runtimes` und `scripts/ai-runtimes.ps1`
  starten Smoke- oder Model-Mode. Modellroot/Caches sind auf `E:\Laura\models` verlegt
  (Fallback `workspace/models`), die lokalen Gewichte liegen dort. Backend-Runtime-Registry
  (`/ai/runtimes`) ist additiv mit Migration `0025`; bestehende `backend`-Felder bleiben
  Fallback, `runtime_id` routet Voice/Reenact/Lipsync in den normalen Job-Flow. Verifiziert:
  `services/ai-runtimes/tests` 18 gruen, Runtime-API/Repo/Manager/Routing 21 gruen,
  Compose-Config gruen, Skript-Parse/WhatIf gruen, Registry-Smoke gegen lokale API registriert
  drei Model-Runtimes mit E:-Mounts. Runtime-`/healthz`/`/capabilities` pruefen im Model-Mode
  jetzt auch runtime-spezifische Pflichtartefakte (`piper`, LivePortrait-Weights, vollstaendige
  MuseTalk-Weights inkl. DWPose/Face-Parse/SD-VAE/Whisper) und melden `missing_model_paths`;
  Compose-Healthchecks sowie `ai-runtimes` `health` werten `ready`/`ok` aus und schlagen bei
  fehlenden Modellartefakten fehl. MuseTalk-Image patcht den UNet-Checkpoint-Load auf CPU und
  linkt `/opt/MuseTalk/models` auf den gemounteten Weight-Root, damit Code aus dem Image und
  Gewichte von `E:` zusammenlaufen; die MuseTalk-`inference.py`-FFmpeg-Finalisierung laeuft
  ueber `subprocess.run(..., stdin=DEVNULL, timeout=LAURA_MUSETALK_FFMPEG_TIMEOUT)` statt
  blindem `os.system`, damit haengende Finalisierung nicht mehr den Laura-HTTP-Job blockiert.
  `check-ai-runtime-prereqs.ps1` prueft Modellroot, Docker, WSL und Sidecar-Ports.
  **Live-Stand:** Docker Desktop/WSL recovered; Sidecars laufen im GPU-Model-Mode healthy.
  Piper direkt und ueber Laura-Voiceover-Job gruen;
  LivePortrait direkter Model-Sidecar gruen; MuseTalk direkter Model-Sidecar gruen
  (`E:\Laura\ai-runtime\live-tests\musetalk-direct-linked-models.mp4`, 576x768, 22.49s).
  Laura-JobRunner-Lipsync ueber `runtime_id` auf den MuseTalk-Sidecar ist live gruen:
  `E:\Laura\ai-runtime\laura-live-lipsync-api-20260622075050\project\synthetic\c916b971aa7e45d191574125f795cc96.lipsync.mp4`
  (576x768, 50 Frames, 2.000s, H.264 + AAC, Provenance-Sidecar). Rebuild:
  `laura-runtime-musetalk-model:local`; Health: Voice 8898, LivePortrait 8899, VibeVideo 8901
  jeweils `ok=true`, `ready=true`.

---

### Diese Session — Zielkorridor
Portion **1 → 2 → 3** vollständig & verifiziert, dann **5** (Electron-Shell) anstoßen.
Interchange/Analyse/Rough-Cut/Playback folgen in weiteren Sessions.

### Risiken aktiv beobachten
- `[!]` WhisperX Version-Churn → isolierte uv-Extra-Gruppe, hart gepinnt.
- `[!]` FCPXML-Adapter → nur guarded + Fixtures.
- `[!]` libmpv-Electron-Bindings je OS → Phase „Härtung", früh Spike einplanen.

---

## Portion 20 — Kohärenz der Produktionskette  `[x]`  ← **Kern-Risiko, aus Live-Befunden; alle 4 Säulen shipped**

**Abschluss-Review (adversarial, 6 Perspektiven, jede Erkenntnis widerlegt bevor geglaubt —
9 Befunde, 5 überlebt, 4 widerlegt):** fand 2 echte Fehler *in den Fixes selbst*, beide behoben
(`3eb195d`): (1) Staleness-Check hashte die Speicher- statt der Storyline-Reihenfolge →
`lines_in_storyline_order` lebt jetzt in board_models neben `script_hash`; (2) der 200-statt-404-
Vertrag am Status-Endpunkt crashte ChatPanels SessionChips im Pre-Board-Fenster →
`ProductionStatus` ist eine diskriminierte Union auf `board_ready` (Compiler erzwingt Narrowing),
vitest 360/360 + tsc grün. **Merke:** Ein während eines Laufs laufendes Backend hat den Stand
seines Starts — Lauf F lief auf `a7580a5`, dessen `stale`-Feld kann bei umsortierten Szenen
falsch-True zeigen.

**Der gemeinsame Nenner aller Fehler vom 18.07.2026:** Die Kette prüft nie, ob ihre Glieder
*zueinander* gehören. Jedes Artefakt wird einzeln gespeichert, die Kettenposition entscheidet
allein die Dateipräsenz — und was ein Artefakt woraus abgeleitet wurde, hält nichts fest.

Live gemessen auf Board `094f92a8`:

| Artefakt | Version | Stimme | gehört zu |
|---|---|---|---|
| `script` | v39 | — | 82 Worte, 2 von 6 Kapiteln |
| `voice` | v3 | 29.4s | Skript v39 |
| `render_report` | v2 | 108.8s | **Skript v14** (262 Worte) |

Das `voice_fits: OK` darauf war wahr — für eine Paarung, die es nicht mehr gibt. Vier von sechs
Storyline-Kapiteln hatten null Worte, und niemand hat es bemerkt. `voice` hält mit `script_hash`
bereits das richtige Muster; es ist nur nirgends verallgemeinert und wird nie geprüft.

### 20.A — Provenienz & Kohärenz  `[ ]`
- [ ] Jedes abgeleitete Artefakt hält fest, woraus es gebaut wurde (`voice.script_hash` als Vorbild:
      `cutlist` → voice+script, `render_report` → cutlist+voice)
- [ ] `Board.status()` meldet pro Kettenglied `stale: true|false` statt nur Präsenz
- [ ] Der Render-Cap-Guard (`b511639`) darf einen Render, der zum aktuellen Skript nicht mehr passt,
      **nicht** als `final` ausgeben — er muss „letzter Render ist veraltet" melden
- **Exit:** ein Board mit Skript v39 + Render aus v14 meldet das, statt Erfolg zu behaupten

### 20.B — Vollständigkeit des Skripts  `[ ]`
- [x] `get_script` meldet Storyline-Kapitel ohne Zeilen (live: Kapitel 3–6 stumm) + `chapters_written`
- [x] Der `scene_author`-Prompt nennt `silent_chapters` und die eine legitime Ausnahme
- [~] ~~Ein Skript mit leeren Kapiteln gilt nicht als fertiges Kettenglied~~ — **revidiert.**
      Ein Kapitel ohne Sprechertext ist eine legitime filmische Entscheidung (Bildbeat mit Musik).
      Ein harter Block würde Handwerk verbieten, um einen Fehler zu fangen. Also melden, nicht
      blockieren — dieselbe Schlussfolgerung wie beim `resume_point`, aber aus besserem Grund.
- **Exit:** ein 2-von-6-Kapitel-Skript ist benannt, wo der Autor seine Arbeit prüft ✓

### 20.C — Preflight statt „Connection error."  `[x]` (`ad8f957`)
- [x] Provider vor dem Enqueue prüfen (`config_problems` an allen 4 Enqueue-Endpunkten, 503)
- [x] Konfigurationsfehler von Transportfehler unterscheiden — strukturell: kein Modellaufruf
      ohne erreichbaren Provider, also heißt „Connection error." danach wirklich Transport
- [x] Unbekannter `LAURA_AGENT_PROVIDER` wird gemeldet statt still zu ersetzen (Resolver merkt
      sich den Rohwert)
- **Exit:** ✓ live gegen die echte `.env` verifiziert — fehlender Key UND Tippfehler `openai` gefangen

### 20.D — Lebenszeichen  `[x]` (`0033`-Migration)
- [x] Session hält `latest_job_id`; `GET /production/{sid}` gibt `job` (status/attempt/
      updated_at/lease_expires_at) aus — hängender Lauf = laufender Job mit abgelaufenem Lease
- [x] Lebenszeichen wird VOR dem Board geholt: ein Lauf, der vor dem Board stirbt, liefert
      `{"job": …, "board_ready": false}` statt 404 — genau der Vorfall
- [ ] `review_scene` ohne konfiguriertes VLM scheitert einmal laut, statt pro Szene still zu
      degradieren — **verschoben nach 20.A-Muster:** `degraded_count` macht es bereits sichtbar;
      lautes Scheitern wäre eine Verhaltensänderung, die einen eigenen Lauf braucht
- **Exit:** ✓ ein hängender/toter Lauf ist am Endpunkt erkennbar, auch ohne Board

### 20.E — Bekannt, belegt, noch offen  `[ ]`
- [ ] `hook_score` belohnt Bewegung → gehaltene Screens bekommen 1-Sekunden-Fenster (Reel-Logik)
- [ ] Skript-Thrashing unbegrenzt außer durch `max_turns` (live: v39 in einem Lauf)

**Erledigt aus diesem Befundkreis:** Job-Ergebnis-Kontrakt (`35361d6`), `BoardMeta.status` mit
Schreiber, `degraded_count`/`checks_ok` in `Board.status()`, Kapazitäts-Budget (`56abc5b`).
