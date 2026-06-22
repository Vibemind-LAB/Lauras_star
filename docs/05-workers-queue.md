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

## Automatische Pipeline (zero clicks)

Nach dem Import läuft die Kette **ohne Nutzereingriff** durch — weder „Analysieren" noch „Rough Cut
bauen" muss geklickt werden:

```
ingest.fetch? ─> ingest.probe ─> proxy.build ─> audio.extract ─> waveform.build
                                                                     └─(auto)─> analysis.run
                                                                                   └─(auto, bei scene=ok)─> Rough-Cut + Szenen + Dead-Air-Trim
```

- **`waveform.build` → `analysis.run`:** `_maybe_auto_analyze` (`ingest/handlers.py`) enqueued am Ende
  der Ingest-Kette einen `analysis.run` (idempotent, `max_attempts=2`, `model="base"`). Opt-out
  `LAURA_AUTO_ANALYZE=0`.
- **`analysis.run` → edit-ready:** bei erfolgreicher Scene-Stage baut `handle_analysis_run` via
  `autobuild_asset_edit_ready` (`scenes/build.py`) den per-Asset-Rough-Cut aus den *kept* Shots,
  entfernt Dead-Air (transkript-gated — nur Clips mit Sprache, B-Roll/Musik unberührt) und gruppiert
  in Szenen. Idempotent: überschreibt vorhandene User-Szenen nie. Opt-out `LAURA_AUTO_ROUGH_CUT=0`.

### Betriebs-Schalter (Env)

| Variable | Default | Wirkung |
|---|---|---|
| `LAURA_WORKERS` | `3` | Größe des Worker-Thread-Pools im Runner |
| `LAURA_JOB_MAX_RUNTIME` | `3600` | harte Laufzeit-Obergrenze (s) je Job → danach `failed` (Schutz gegen Wedge/Runaway) |
| `LAURA_AUTO_ANALYZE` | `1` | Auto-`analysis.run` nach Import |
| `LAURA_AUTO_ROUGH_CUT` | `1` | Auto-Rough-Cut + Szenen nach erfolgreicher Analyse |
| `LAURA_AUTO_TIGHTEN` | `1` | Dead-Air-Trim im Auto-Rough-Cut |
| `LAURA_TIGHTEN_PAD_MS` / `LAURA_TIGHTEN_MIN_GAP_MS` | `300` / `900` | Polster um Sprache bzw. minimale zu schneidende Stille (ms) |
| `LAURA_SCENE_DETECT_HEIGHT` | `720` | Shot-Detection auf herunterskaliertem Proxy ab dieser Quellhöhe |
| `LAURA_ASR_DEVICE` | auto | `cuda`/`cpu`-Override für Whisper (sonst GPU wenn verfügbar) |

### Performance bei langen Videos

Shot-Qualitätsmetriken werden in **einem** Decode-Durchlauf über alle Shots berechnet
(`batch_shot_metrics`, `analysis/quality.py`) statt per Shot — O(N) statt O(N²), weil ffmpegs
Frame-`select` ab Frame 0 dekodiert. Damit analysiert ein ~57-min-Video (566 Shots) in ~4 min statt
>20 min. Scene-Detection läuft bei Quellen über `LAURA_SCENE_DETECT_HEIGHT` auf einem
herunterskalierten Detektions-Proxy (gleiche Frame-Indizes, schnellerer Decode).

## Worker-Ausführung

- Worker laufen als eigene Prozesse/Threads, pollen Queues nach Priorität.
- **Modell-Worker** (ASR/diarize) sind teuer → eigener Prozess, geladene Modelle cachen, serielle GPU-Nutzung.
- **Cancel:** Job auf `canceled` setzen; Worker prüft Flag an Stufengrenzen.
- **Observability:** OpenTelemetry (FastAPI/Celery), Prometheus-Metriken für Queue-Tiefe/Joblaufzeit
  (siehe [`10-testing-observability`](10-testing-observability.md)).
