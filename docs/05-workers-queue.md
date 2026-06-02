# 05 — Worker-Pipeline & Queue-Modell

Pipeline = **kleine, idempotente Stufen**. FastAPI-Background-Tasks reichen nur für Kleinkram,
**nicht** für stabile Heavy Jobs. Desktop: **DB-basierter lokaler Job-Runner** (keine Broker-Pflicht).
Server/On-Prem: später auf **Celery + Redis** mit Queue-Routing umschaltbar. Gleiche Job-Semantik.

## Queues (logisch)

| Queue | Typ | Priorität | Worker |
|---|---|---|---|
| `ingest.io` | Probe, Checksums, Poster Frames | hoch | CPU |
| `proxy.cpu` | Proxying, Audio-Extract, Waveform | mittel | CPU |
| `analysis.scene` | Shot Detection | hoch | CPU/GPU optional |
| `analysis.asr` | Transkription | hoch | GPU bevorzugt |
| `analysis.align` | Forced Alignment | hoch | CPU/GPU |
| `analysis.diarize` | Sprechertrennung | mittel | GPU bevorzugt |
| `analysis.embed` | Embeddings | niedrig | CPU/GPU |
| `export.interchange` | OTIO, XML, EDL, Captions | hoch | CPU |
| `maintenance.gc` | Cache, Cleanup, Compaction | niedrig | CPU |

## Job-Lebenszyklus

```
queued -> leased -> running -> succeeded | failed | canceled
```

Jeder Job trägt: `attempt`, `max_attempts`, `lease_expires_at`, `heartbeat_at`,
`caused_by_job_id`, `pipeline_version`, `idempotency_key`. Damit sind Desktop-Abstürze,
Worker-Neustarts und Doppelausführung beherrschbar.

## DB-Runner — Claim-Protokoll (Desktop)

1. **Claim:** atomar `UPDATE jobs SET status='leased', worker_id=?, lease_expires_at=now+T
   WHERE id = (SELECT id FROM jobs WHERE status='queued' AND queue IN (...) ORDER BY priority, created_at
   LIMIT 1)` (SQLite: `BEGIN IMMEDIATE`).
2. **Run:** `status='running'`, periodischer `heartbeat_at`. Stufenarbeit idempotent (FS-Writes
   atomar: temp + rename).
3. **Finish:** `succeeded` (+ `result_ref`) oder `failed` (+ `error_json`).
4. **Reaper:** Jobs mit `lease_expires_at < now` und `status IN (leased,running)` → `queued`
   (`attempt++`); bei `attempt >= max_attempts` → `failed`.
5. **Idempotenz:** vor Enqueue prüfen, ob `(idempotency_key, pipeline_version)` schon `succeeded` —
   dann Ergebnis wiederverwenden statt neu rechnen.

## Verkettung (DAG je Asset)

```
ingest.io ──> proxy.cpu ──> analysis.scene
                        └──> analysis.asr ──> analysis.align ──> analysis.diarize ──> analysis.embed
```

`caused_by_job_id` bildet die Kausalkette; Folge-Jobs werden beim Erfolg des Vorgängers enqueued.

## Worker-Ausführung

- Worker laufen als eigene Prozesse/Threads, pollen Queues nach Priorität.
- **Modell-Worker** (ASR/diarize) sind teuer → eigener Prozess, geladene Modelle cachen, serielle GPU-Nutzung.
- **Cancel:** Job auf `canceled` setzen; Worker prüft Flag an Stufengrenzen.
- **Observability:** OpenTelemetry (FastAPI/Celery), Prometheus-Metriken für Queue-Tiefe/Joblaufzeit
  (siehe [`10-testing-observability`](10-testing-observability.md)).
