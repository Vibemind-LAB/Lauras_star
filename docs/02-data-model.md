# 02 — Datenmodell (Schema v1)

Runtime-Store: **SQLite**. Logisches Modell: **PostgreSQL-kompatibel**, damit On-Prem/Cloud
keinen Re-Write erzwingen. Konkrete DDL: [`services/local-api/src/laura/db/schema.sql`](../services/local-api/src/laura/db/schema.sql).

## Konventionen

- IDs: `TEXT` UUIDv4 (portabel SQLite↔Postgres). Zeitstempel: ISO-8601 UTC (`TEXT`).
- Frames: `INTEGER` (bigint-Semantik). Ranges **end-exclusive** (`*_out_frame_exclusive`).
- Audio: `INTEGER` Sample-Indizes. JSON-Spalten: `TEXT` mit JSON (SQLite) / `JSONB` (Postgres).
- Raten als `rate_num`/`rate_den` (rational), **nie** Float. Siehe [`03-time-model`](03-time-model.md).

## Kernobjekte

### `projects`
| Spalte | Typ | Notiz |
|---|---|---|
| `id` | TEXT PK | |
| `name` | TEXT | |
| `sequence_rate_num` / `sequence_rate_den` | INT | z. B. 30000/1001 |
| `drop_frame` | BOOL | nur Anzeige-Semantik |
| `workspace_root` | TEXT | absoluter Pfad |
| `created_at` | TEXT | |

### `media_assets`
`id`, `project_id` FK · `type` (video/audio) · `display_name` · `source_path` · `sha256` ·
`duration_frames` · `rate_num`/`rate_den` · `audio_sample_rate` · `start_timecode` (string|null) ·
`width` · `height` · `codec_video` · `codec_audio` · `is_vfr` (bool) · `created_at`.
→ Canonical Asset Registry (eine Zeile je Originalmedium).

### `asset_files`
`id`, `asset_id` FK · `kind` (original/proxy/audio_mono16k/audio_mix48k/waveform/poster/thumbnail) ·
`path` · `size_bytes` · `is_proxy` · `is_waveform` · `is_audio_extract` · `checksum`.
→ Originale, Proxies, Wellenformen, Extracts.

### `analysis_runs`
`id`, `asset_id` FK · `pipeline_version` · `status` · `started_at` · `finished_at` ·
`config_json` · `diagnostics_json`. → reproduzierbare Analyseinstanz (Idempotenz-Anker).

### `shots`
`id`, `asset_id`, `analysis_run_id` FK · `src_in_frame` · `src_out_frame_exclusive` ·
`confidence` · `method` (pyscenedetect/transnetv2/manual) · `thumbnail_path`.

### `speakers`
`id`, `asset_id`, `analysis_run_id` FK · `label` (SPEAKER_00…) · `display_name` · `color` · `confidence`.

### `transcript_segments`
`id`, `asset_id`, `analysis_run_id` FK · `speaker_id` FK · `start_sample` · `end_sample` ·
`start_frame` · `end_frame` · `text` · `confidence`. → Segmentebene.

### `transcript_words`
`id`, `segment_id` FK · `idx` · `start_sample` · `end_sample` · `start_frame` · `end_frame` ·
`text` · `confidence` · `is_punctuation`. → **Wortebene**; Basis für frame-/sample-aware Edits.
Samples sind kanonisch, Frames sind projiziert (siehe [`03-time-model`](03-time-model.md)).

### `timelines`
`id`, `project_id` FK · `name` · `kind` (selects/rough_cut/final) · `otio_json` (kanonisch!) ·
`created_from`. → OTIO ist Source of Truth; `otio_json` ist der maßgebliche Zustand.

### `timeline_clips`
`id`, `timeline_id` FK · `asset_id` FK · `src_in_frame` · `src_out_frame_exclusive` ·
`seq_in_frame` · `seq_out_frame_exclusive` · `lane` · `linked_audio_group` · `speaker_id` ·
`origin_word_start_id` · `origin_word_end_id`. → denormalisierte Sicht auf OTIO-Clips für schnelle
Queries; bei jeder Operation aus `otio_json` neu materialisiert.

### `exports`
`id`, `timeline_id` FK · `format` (otio/edl/fcp7xml/fcpxml/srt/vtt) · `status` ·
`output_path` · `options_json` · `diagnostics_json` · `created_at`. → Export-Historie.

### `jobs`
`id` · `queue` · `kind` · `payload_json` · `status` · `attempt` · `max_attempts` ·
`lease_expires_at` · `heartbeat_at` · `caused_by_job_id` · `pipeline_version` ·
`idempotency_key` · `worker_id` · `result_ref` · `error_json`. → Queue-/Worker-Steuerung.
Lebenszyklus & Semantik: [`05-workers-queue`](05-workers-queue.md).

## Optionale Such-/Kollaborationsobjekte (Phase 2+)

| Tabelle | Felder | Zweck |
|---|---|---|
| `embeddings` | `object_type`, `object_id`, `vector_ref`, `text`, `model`, `lang` | semantische Suche |
| `review_comments` | `timeline_id`, `frame_in`, `frame_out`, `author_id`, `body` | Review |
| `sync_events` | `entity_type`, `entity_id`, `op`, `payload_json`, `synced_at` | Cloud-Sync |

## Indizes (Minimum)

- `media_assets(project_id)`, `media_assets(sha256)` (Dedupe).
- `asset_files(asset_id, kind)`.
- `shots(asset_id, analysis_run_id, src_in_frame)`.
- `transcript_segments(asset_id, analysis_run_id, start_sample)`.
- `transcript_words(segment_id, idx)`.
- `timeline_clips(timeline_id, seq_in_frame)`.
- `jobs(queue, status, lease_expires_at)` (Claim-Query des Runners).
- FTS5 (SQLite) / `tsvector` (Postgres) auf `transcript_segments.text` für lexikalische Suche.

## Migrationen

Versionierte SQL-Migrationen unter `services/local-api/src/laura/db/migrations/` (`0001_init.sql`, …).
Schema-Version in Tabelle `schema_meta(version, applied_at)`. Beim Service-Start: pending Migrationen anwenden.
