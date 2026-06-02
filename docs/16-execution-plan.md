# 16 — Ausführungsplan: „Alles machen" (Roadmap bis Vollständigkeit)

Konsolidierter, **sequenzierter** Plan für den Rest der Implementierung. Er löst den
Gap-Closure-Plan ([`15-gap-closure-plan.md`](15-gap-closure-plan.md)) in eine konkrete
Reihenfolge ausführbarer **Teile** auf und trennt ehrlich, was **headless gebaut +
verifiziert** werden kann von dem, was **echte externe Ressourcen** (Hardware, Zertifikate,
Accounts) braucht.

Quelle der Wahrheit für Reihenfolge/Stand bleibt [`../tasks/todo.md`](../tasks/todo.md).
Dieses Dokument ist die *Begründung und Detailtiefe* hinter den Portionen 15+.

---

## 0. Baseline — was bereits steht (verifiziert & gepusht)

- **Zeitkern** (Portion 1): Integer-Frames, end-exclusive, Samples, DF/NDF, deterministische Rundung.
- **Backend** (2–4, 11–12): FastAPI, pluggbares **SQLite↔Postgres**, Job-Runner (Claim/Lease/Reaper/Idempotenz), Celery-Scaffold, RBAC/Audit/Metrics, **live gegen Postgres getestet**.
- **Ingest** (3): ffprobe → CFR-Proxy → Audio → Waveform, end-to-end gegen reales FFmpeg.
- **Interchange** (4, 13–14): OTIO (kanonisch) + EDL + **FCP7-XML** + **FCPXML** + SRT/VTT, Preflight.
- **Analyse** (7, 14): PySceneDetect real; ASR/Diarize als Extras (graceful-skip); **Shot-Thumbnails**.
- **Rough-Cut** (8): reine frame-genaue Ops, OTIO neu geschrieben je Op.
- **Electron/React** (5–6, 9): gehärtete Shell, Import-Flow, AnalysisPanel, **Proxy-Player** (frame-genau), Export-Menü, Suche, CRUD-Buttons.
- **Gap-Closure** (13 = Wave A komplett; 14 = Wave B teilweise).

Stand: **140 Tests grün**, ruff/mypy strict clean (86 Dateien), App `tsc`/`vite build` grün, 13 Commits auf `main`.

---

## 1. Was ich headless bauen + verifizieren kann (sequenziert)

Jede Portion ist in sich abschließbar und endet mit `uv run pytest` (+ ruff + mypy strict)
bzw. `tsc --noEmit` + `vite build` (+ Vitest), dann Commit + Push.

### Portion 15 — Wave-B Backend-Abschluss
| # | Lücke | Ansatz | Verifikation | Aufwand |
|---|---|---|---|---|
| 15.1 | **Pagination** | `limit`/`offset` (+ `total`) auf `list_projects/list_assets/list_timelines` und den GET-Endpoints; defensives Clamping | pytest: Grenzfälle (offset über Ende, limit-Cap) | S |
| 15.2 | **Rate-Limiting** | Token-Bucket-Middleware pro API-Key/Loopback, konfigurierbar; `429` + `Retry-After` | pytest: Burst → 429, Refill | S |
| 15.3 | **Speed/Retiming** | Editing-Ops um `speed_num/den` erweitern (Zeitkern kann Speed-Mapping bereits); OTIO mit `LinearTimeWarp` | pytest: golden Delta + OTIO-Roundtrip | M |
| 15.4 | **Timeline-Captions** | SRT/VTT aus dem **geschnittenen** Rough Cut (Clip→`origin_word_*`→Sequence-Frames); neuer Writer-Pfad | pytest: golden gegen bekannten Cut | M |
| 15.5 | **Editorial-Import** | `POST /projects/{id}/timelines/import` — OTIO/EDL lesen (`otio_string_to_timeline` ist da), Clips per `source_path`/sha256 **relinken**, fehlende Medien als „offline" markieren | pytest: Roundtrip Export→Import, Relink-Treffer/Miss | M |
| 15.6 | **origin_word-Link (Backend)** | Ops schreiben `origin_word_start/end` konsistent; `GET …/clips` liefert Rücksprung-Anker | pytest: Anker stimmen frame-genau | S |
| 15.7 | **Queue-Routing** | CPU/GPU-Queues + Prioritäten im Claim; Handler-Stubs `analysis.align`/`analysis.embed`; Celery-Beat-Reaper | pytest: Routing wählt richtige Queue; Reaper-Lease | L |

### Portion 16 — Wave-B/C Frontend
| # | Lücke | Ansatz | Verifikation | Aufwand |
|---|---|---|---|---|
| 16.1 | **Shot-Thumbnail-Strip** | UI-Strip nutzt `GET /shots/{id}/thumbnail` (Blob+Token) statt Platzhalter | `tsc`/`vite build`; Vitest-Komponententest | S |
| 16.2 | **origin_word-Highlight** | Transkript-Klick → Player-Sprung; aktiver Clip hebt Quellwörter hervor | Vitest (Mapping-Logik) | S |
| 16.3 | **Player JKL/Shuttle** | J/K/L + Shuttle-Stufen, ←/→ Frame, Shift+Pfeil = Sekunde, Home/End | Vitest (Keymap→Transport) | S |
| 16.4 | **Undo/Redo + Trim/Split/Insert-UI** | Editing-History (Op-Stack) im Renderer; Trim/Split/Insert-Buttons auf bestehende Backend-Ops | Vitest (History invertierbar) | M |
| 16.5 | **Desktop-Tests-Setup** | Vitest + Testing-Library in `apps/desktop`; CI-Schritt ergänzen | `pnpm test` grün in CI | M |

### Portion 17 — Observability & Betrieb
| # | Lücke | Ansatz | Verifikation | Aufwand |
|---|---|---|---|---|
| 17.1 | **OpenTelemetry-Tracing** | OTel-FastAPI/Job-Runner-Instrumentierung; Stage-Timings/Queue-Tiefe als Spans+Metriken; OTLP optional, sonst No-op-Exporter | pytest: Spans um Job-Lauf (In-Memory-Exporter) | M |
| 17.2 | **Postgres-RLS** | Row-Level-Security pro `org_id` (Policies in Migration, `SET app.current_org`); SQLite unberührt | Live-PG-Test: Cross-Org-Read = 0 Zeilen | M |
| 17.3 | **Demo-Projekt + Golden-Fixtures** | `fixtures/` mit reproduzierbarem Mini-Projekt (FFmpeg-`testsrc`/`sine`) + Golden-Exporte (OTIO/EDL/SRT) | pytest: Golden-Vergleich byte-genau | S |

**Reihenfolge-Logik:** 15 schließt das Backend funktional ab → 16 macht es im UI bedienbar →
17 macht es betriebsreif. Innerhalb 15: erst die kleinen risikoarmen (15.1/15.2/15.6),
dann die mittleren Feature-Pfade (15.3/15.4/15.5), zuletzt das große Queue-Routing (15.7).

---

## 2. Extern blockiert — braucht **deine** Ressourcen

Code/Config/Doku stehen jeweils so weit wie ohne die Ressource möglich. Sobald du das
Genannte bereitstellst, ist der Verifikationsschritt klar definiert.

| Thema | Schon vorbereitet | Du musst bereitstellen | Verifikation sobald da |
|---|---|---|---|
| **libmpv nativ** (ADR-0002, primärer Pro-Player) | Proxy-Player als verifizierter MVP; ADR + Embedding-Plan (`--wid`) | Nativer Build-Toolchain je OS + GUI-Session zum Sicht-Test | Frame-Step gegen libmpv vs. Proxy frame-genau gegenprüfen |
| **Signierte Builds** (Portion 10) | `forge.config.ts` Maker, packaged-mode-Service, `docs/13-packaging.md` | Win-Code-Signing-Cert (EV/OV) + Apple-Developer-ID + Notarization-Account | `pnpm make` → signierter Installer, der ohne SmartScreen/Gatekeeper-Warnung startet |
| **Qdrant semantische Suche** | `embeddings`-Tabelle/ADR; lexikalische Suche live; compose hat Qdrant | Qdrant-Instanz + Embedding-Modell (lokal/API) | Semantik-Query liefert sinnvolle Nachbarn; Recall gegen lexikalisch |
| **WhisperX / pyannote / TransNetV2** | `[asr]/[diarize]/[align]`-Extras, lazy-Import, graceful-skip; Mapping über Zeitkern | **GPU** + Modell-Downloads + HF-Token (pyannote-Gate) | Realer Lauf auf Fixture: Wort-Timestamps frame-genau, Diarisierung plausibel |
| **Auto-Update** | Electron-Forge-Maker vorhanden | Release-/Update-Server (S3/GitHub Releases) **+ signierte Builds** | App zieht Update, verifiziert Signatur, startet neu |

Diese fünf Punkte sind **nicht** Faulheit der Implementierung — sie brauchen physisch
Hardware, kryptografische Identität oder Hosting, die in einer headless-Session nicht
existieren. Alles drumherum (Schalter, Konfig, Doku, Tests-mit-Mocks) ist vorbereitet.

---

## 3. Definition of Done je Teil

1. `uv run pytest` grün (neue Tests decken den Pfad ab) · `ruff` clean · `mypy --strict` clean.
2. Bei Frontend zusätzlich `tsc --noEmit` (strict) + `vite build` grün, wo sinnvoll Vitest.
3. Reale Verifikation wo möglich (FFmpeg/Postgres-Container); UI-Sicht-Tests ausdrücklich als „manuell zu prüfen" markiert.
4. `tasks/todo.md` aktualisiert, Conventional-Commit, Push.
5. Reale Korrekturen des Users → `lessons.md`.

## 4. Fortschritt

- Portionen 1–13: `[x]` · Portion 14 (Wave B): `[~]` (FCPXML/Captions/Thumbnails fertig).
- Portionen 15–17: dieser Plan, in Reihenfolge abzuarbeiten.
- Extern blockiert: Abschnitt 2 — wartet auf deine Ressourcen.
