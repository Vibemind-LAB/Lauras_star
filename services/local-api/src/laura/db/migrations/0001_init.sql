-- Laura schema v1 (docs/02-data-model.md).
-- SQLite runtime store, kept PostgreSQL-compatible:
--   * TEXT ids (uuid hex), ISO-8601 TEXT timestamps
--   * INTEGER frames/samples, end-exclusive ranges
--   * JSON kept as TEXT (SQLite) / JSONB (Postgres)
--   * booleans as INTEGER 0/1
-- The schema_meta table is owned by the migration runner (db/database.py).

CREATE TABLE projects (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    sequence_rate_num   INTEGER NOT NULL,
    sequence_rate_den   INTEGER NOT NULL,
    drop_frame          INTEGER NOT NULL DEFAULT 0,
    workspace_root      TEXT NOT NULL,
    created_at          TEXT NOT NULL
);

CREATE TABLE media_assets (
    id                  TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    type                TEXT NOT NULL,                 -- 'video' | 'audio'
    display_name        TEXT NOT NULL,
    source_path         TEXT NOT NULL,
    sha256              TEXT,
    duration_frames     INTEGER,
    rate_num            INTEGER,
    rate_den            INTEGER,
    audio_sample_rate   INTEGER,
    start_timecode      TEXT,
    width               INTEGER,
    height              INTEGER,
    codec_video         TEXT,
    codec_audio         TEXT,
    is_vfr              INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL
);
CREATE INDEX idx_media_assets_project ON media_assets(project_id);
CREATE INDEX idx_media_assets_sha256 ON media_assets(sha256);

CREATE TABLE asset_files (
    id                  TEXT PRIMARY KEY,
    asset_id            TEXT NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
    kind                TEXT NOT NULL,                 -- original|proxy|audio_mono16k|audio_mix48k|waveform|poster|thumbnail
    path                TEXT NOT NULL,
    size_bytes          INTEGER,
    is_proxy            INTEGER NOT NULL DEFAULT 0,
    is_waveform         INTEGER NOT NULL DEFAULT 0,
    is_audio_extract    INTEGER NOT NULL DEFAULT 0,
    checksum            TEXT
);
CREATE INDEX idx_asset_files_asset_kind ON asset_files(asset_id, kind);

CREATE TABLE analysis_runs (
    id                  TEXT PRIMARY KEY,
    asset_id            TEXT NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
    pipeline_version    TEXT NOT NULL,
    status              TEXT NOT NULL,                 -- queued|running|succeeded|failed
    started_at          TEXT,
    finished_at         TEXT,
    config_json         TEXT NOT NULL DEFAULT '{}',
    diagnostics_json    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_analysis_runs_asset ON analysis_runs(asset_id);

CREATE TABLE shots (
    id                       TEXT PRIMARY KEY,
    asset_id                 TEXT NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
    analysis_run_id          TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    src_in_frame             INTEGER NOT NULL,
    src_out_frame_exclusive  INTEGER NOT NULL,
    confidence               REAL,
    method                   TEXT,                     -- pyscenedetect|transnetv2|manual
    thumbnail_path           TEXT
);
CREATE INDEX idx_shots_asset_run ON shots(asset_id, analysis_run_id, src_in_frame);

CREATE TABLE speakers (
    id                  TEXT PRIMARY KEY,
    asset_id            TEXT NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
    analysis_run_id     TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    label               TEXT NOT NULL,                 -- SPEAKER_00 ...
    display_name        TEXT,
    color               TEXT,
    confidence          REAL
);
CREATE INDEX idx_speakers_asset_run ON speakers(asset_id, analysis_run_id);

CREATE TABLE transcript_segments (
    id                  TEXT PRIMARY KEY,
    asset_id            TEXT NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
    analysis_run_id     TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    speaker_id          TEXT REFERENCES speakers(id) ON DELETE SET NULL,
    start_sample        INTEGER NOT NULL,
    end_sample          INTEGER NOT NULL,
    start_frame         INTEGER NOT NULL,
    end_frame           INTEGER NOT NULL,
    text                TEXT NOT NULL,
    confidence          REAL
);
CREATE INDEX idx_segments_asset_run ON transcript_segments(asset_id, analysis_run_id, start_sample);

CREATE TABLE transcript_words (
    id                  TEXT PRIMARY KEY,
    segment_id          TEXT NOT NULL REFERENCES transcript_segments(id) ON DELETE CASCADE,
    idx                 INTEGER NOT NULL,
    start_sample        INTEGER NOT NULL,
    end_sample          INTEGER NOT NULL,
    start_frame         INTEGER NOT NULL,
    end_frame           INTEGER NOT NULL,
    text                TEXT NOT NULL,
    confidence          REAL,
    is_punctuation      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_words_segment_idx ON transcript_words(segment_id, idx);

CREATE TABLE timelines (
    id                  TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    kind                TEXT NOT NULL,                 -- selects|rough_cut|final
    otio_json           TEXT NOT NULL DEFAULT '{}',    -- canonical source of truth
    created_from        TEXT,
    created_at          TEXT NOT NULL
);
CREATE INDEX idx_timelines_project ON timelines(project_id);

CREATE TABLE timeline_clips (
    id                       TEXT PRIMARY KEY,
    timeline_id              TEXT NOT NULL REFERENCES timelines(id) ON DELETE CASCADE,
    asset_id                 TEXT NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
    src_in_frame             INTEGER NOT NULL,
    src_out_frame_exclusive  INTEGER NOT NULL,
    seq_in_frame             INTEGER NOT NULL,
    seq_out_frame_exclusive  INTEGER NOT NULL,
    lane                     INTEGER NOT NULL DEFAULT 0,
    linked_audio_group       TEXT,
    speaker_id               TEXT REFERENCES speakers(id) ON DELETE SET NULL,
    origin_word_start_id     TEXT,
    origin_word_end_id       TEXT
);
CREATE INDEX idx_timeline_clips_seq ON timeline_clips(timeline_id, seq_in_frame);

CREATE TABLE exports (
    id                  TEXT PRIMARY KEY,
    timeline_id         TEXT NOT NULL REFERENCES timelines(id) ON DELETE CASCADE,
    format              TEXT NOT NULL,                 -- otio|edl|fcp7xml|fcpxml|srt|vtt
    status              TEXT NOT NULL,
    output_path         TEXT,
    options_json        TEXT NOT NULL DEFAULT '{}',
    diagnostics_json    TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL
);
CREATE INDEX idx_exports_timeline ON exports(timeline_id);

CREATE TABLE jobs (
    id                  TEXT PRIMARY KEY,
    queue               TEXT NOT NULL,
    kind                TEXT NOT NULL,
    priority            INTEGER NOT NULL DEFAULT 0,    -- higher = sooner
    payload_json        TEXT NOT NULL DEFAULT '{}',
    status              TEXT NOT NULL DEFAULT 'queued',-- queued|leased|running|succeeded|failed|canceled
    attempt             INTEGER NOT NULL DEFAULT 0,
    max_attempts        INTEGER NOT NULL DEFAULT 3,
    lease_expires_at    TEXT,
    heartbeat_at        TEXT,
    caused_by_job_id    TEXT,
    pipeline_version    TEXT,
    idempotency_key     TEXT UNIQUE,
    worker_id           TEXT,
    result_ref          TEXT,
    result_json         TEXT,
    error_json          TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    finished_at         TEXT
);
CREATE INDEX idx_jobs_claim ON jobs(status, priority, created_at);
CREATE INDEX idx_jobs_lease ON jobs(lease_expires_at);
