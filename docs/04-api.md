# 04 — Lokale API (FastAPI)

Klein, explizit, workflow-orientiert. **Keine** generischen „do-everything"-Endpoints.
Service-Adresse (Desktop): `http://127.0.0.1:8765`. Implementierung: `services/local-api/src/laura/api/`.

## Endpoints

| Bereich | Methode + Pfad | Zweck |
|---|---|---|
| Health | `GET /healthz` | Liveness/Version/Pipeline-Version |
| Projekte | `POST /projects` | Projekt anlegen |
| Projekte | `GET /projects/{id}` | Projekt lesen |
| Projekte | `GET /projects` | Projekte auflisten |
| Assets | `POST /projects/{id}/assets/import` | Medien ingestieren (startet `ingest`-Job) |
| Assets | `GET /assets/{id}` | Asset-Metadaten |
| Assets | `POST /assets/{id}/proxies` | Proxy-Erzeugung anstoßen |
| Analyse | `POST /assets/{id}/analysis` | Analysejob starten |
| Analyse | `GET /assets/{id}/analysis/latest` | letzten Analysezustand holen |
| Shots | `GET /assets/{id}/shots` | Shot-Liste |
| Transcript | `GET /assets/{id}/transcript` | Segmente + Wörter |
| Transcript | `PATCH /transcript/segments/{id}` | Speaker/Text korrigieren |
| Rough Cut | `POST /projects/{id}/timelines` | Timeline/Rough-Cut anlegen |
| Rough Cut | `POST /timelines/{id}/operations` | insert/delete/lift/ripple Ops |
| Rough Cut | `GET /timelines/{id}` | Timeline lesen (OTIO + materialisierte Clips) |
| Suche | `POST /search` | lexikalische (+ semantische) Suche |
| Export | `POST /timelines/{id}/exports` | Export starten |
| Export | `GET /exports/{id}` | Status + Diagnostics |
| Jobs | `GET /jobs/{id}` | Jobstatus |
| Interchange | `POST /interop/validate` | OTIO/XML/EDL Preflight (Capability-Check) |
| Integrationen | `POST /integrations/frameio/publish` | optionales Publish (Phase 2+) |

## Quer­schnitt

- **Schemas:** Pydantic-Modelle, OpenAPI automatisch. Zeitwerte als rationale Frames/Samples
  (nie nackte Sekunden-Floats) — Request/Response spiegeln das kanonische Modell.
- **Async, aber kurz:** Endpoints orchestrieren nur (DB-Write + Job-Enqueue). Heavy Lifts laufen
  in Workern; Clients pollen `GET /jobs/{id}` oder abonnieren ein SSE-/WebSocket-Event (Phase 1: Polling).
- **Idempotenz:** Mutierende Importe/Analysen akzeptieren `idempotency_key`; gleicher Key → gleicher Job.
- **Fehler:** einheitliches Problem-JSON (`type`, `title`, `status`, `detail`, `diagnostics`).
- **Lokal-only Default:** Bindung an `127.0.0.1`, kein offener Port nach außen. Auth im Desktopmodus
  via lokalem Token (Main-Prozess setzt Header), nicht via Login.

## Beispiel-Flows

**Import → Status:**
```
POST /projects/{id}/assets/import { source_path }      -> { asset_id, job_id }
GET  /jobs/{job_id}                                     -> { status: "running"|"succeeded"|... }
GET  /assets/{asset_id}                                 -> { metadata, files[] }
```

**Transcript-Operation erzeugt Timeline-Delta (deterministisch):**
```
POST /timelines/{id}/operations
  { op: "append_from_words", asset_id, word_start_id, word_end_id }
-> { timeline: { otio_json, clips[] }, delta: { added_clips[], seq_len_frames } }
```

**Export mit Preflight:**
```
POST /interop/validate { timeline_id, format: "edl" }  -> { ok, warnings[], lossy: true, drops[] }
POST /timelines/{id}/exports { format, options }        -> { export_id, job_id }
GET  /exports/{export_id}                               -> { status, output_path, diagnostics }
```
