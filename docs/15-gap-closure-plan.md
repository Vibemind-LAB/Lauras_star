# 15 — Gap-Closure-Plan (alle offenen Lücken)

Ergebnis des Gap-Audits (zwei Runden). Priorisiert in drei Wellen. Wave A wird in
dieser Iteration gebaut; B/C sind die Roadmap danach. Jede Position mit Ansatz + grobem
Aufwand (S/M/L).

## Wave A — höchster Produktwert (jetzt)

| # | Lücke | Ansatz | Aufwand |
|---|---|---|---|
| A1 | **FCP7-XML-Export** (Premiere-Interop, „Pflicht") | `interchange/fcp7_xml.py`: xmeml v5 deterministisch aus kanonischem Timeline-Modell; `_EXT += fcp7xml`; Preflight | M |
| A2 | **Suche** (`POST /search`) | portable lexikalische Suche (`LOWER(text) LIKE`) über Transkripte, projektscoped; FTS5/Qdrant als spätere Optimierung; Frontend-Suchbox | M |
| A3 | **Transkript-Korrektur** (`PATCH /transcript/segments/{id}`) | Text + Speaker-Reassignment; `repos.update_segment` | S |
| A4 | **CRUD: Delete/Rename** | `DELETE /projects|assets|timelines/{id}` (FK-Cascade), `PATCH …/{id}` (Rename); RBAC `project:delete` | M |
| A5 | **Multi-Tenancy durchsetzen** | `org_id` bei Projekt-Create aus Principal setzen; List/Get nach Principal-Org filtern; konsistente `require_permission` | M |

## Wave B — Vollständigkeit / Skalierung

| Lücke | Ansatz | Aufwand |
|---|---|---|
| FCPXML (FCPX) | `interchange/fcpx_xml.py` — guarded + Fixture-Bank (Adapter-Risiko, docs/07) | M |
| **Editorial-Import** (OTIO/EDL/FCP-XML lesen) | `POST /projects/{id}/timelines/import`; `otio_string_to_timeline` ist da | M |
| Timeline-Captions | SRT/VTT aus dem geschnittenen Rough Cut (Clip→origin_words) | M |
| Speed-Changes/Retiming | Editing-Ops um `speed_num/den` erweitern (Zeitkern kann es schon) | M |
| Shot-Thumbnails | `thumbnail_path` befüllen (ffmpeg-Frame je Shot); UI-Strip mit Vorschau | S |
| Clip↔Transkript-Link | `origin_word_*` auswerten → Highlight/Rücksprung | S |
| Worker-Queue-Routing | CPU/GPU-Queues + Prioritäten; `analysis.align/embed`-Handler; Celery-Beat-Reaper | L |
| Pagination/Limits | `limit/offset` auf List-Endpoints | S |
| Rate-Limiting / Größenlimits | Middleware | S |
| Captions-Qualität | Zeilenumbruch/CPS-Cap, VTT-Cue-Settings | S |

## Wave C — Pro/Betrieb/Polish

| Lücke | Ansatz | Aufwand |
|---|---|---|
| **libmpv-nativ** | Node-Bindings/`--wid`-Embedding; Proxy-Player ist MVP | L |
| **Signierte Builds** | PyInstaller-Service-Bundle + Win-Signing/macOS-Notarization | L |
| OpenTelemetry-Tracing + volle Metriken | OTel FastAPI/Celery; Stage-Timings, Queue-Tiefe, GPU/VRAM | M |
| Qdrant semantische Suche | Embeddings + `embeddings`-Tabelle + Qdrant | L |
| Postgres-RLS | Row-Level-Security pro `org_id` | M |
| Auto-Update + safeStorage | Electron Forge Auto-Update; Secrets via safeStorage | M |
| **Desktop-Tests** | Vitest (Komponenten) + Playwright (e2e) | M |
| Player JKL/Shuttle + Shortcuts | Transport-Hardening | S |
| Undo/Redo + Trim/Split/Insert-UI | Editing-History; Backend-`insert` schon da | M |
| WhisperX-Alignment / TransNetV2 | Optionale Quality-Booster (`[align]`-Extra aktivieren) | M |
| Reproduzierbares Demo-Projekt + Golden-Fixtures auf Disk | `fixtures/` befüllen | S |

## Fortschritt

- Wave A: siehe `tasks/todo.md` Portion 13.
- Wave B/C: nach Bedarf priorisieren.
