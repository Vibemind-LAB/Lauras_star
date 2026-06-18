CREATE TABLE ai_runtimes (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    effect TEXT NOT NULL,
    display_name TEXT NOT NULL,
    base_url TEXT,
    container_image TEXT,
    container_name TEXT,
    port INTEGER,
    workspace_mount TEXT,
    model_mount TEXT,
    requires_gpu INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    license_status TEXT NOT NULL DEFAULT 'unknown',
    status_cache_json TEXT NOT NULL DEFAULT '{}',
    capabilities_json TEXT NOT NULL DEFAULT '{}',
    last_health_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_ai_runtimes_effect ON ai_runtimes(effect);
CREATE INDEX idx_ai_runtimes_kind ON ai_runtimes(kind);

CREATE TABLE ai_runtime_events (
    id TEXT PRIMARY KEY,
    runtime_id TEXT NOT NULL REFERENCES ai_runtimes(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX idx_ai_runtime_events_runtime_created
    ON ai_runtime_events(runtime_id, created_at DESC);

CREATE TABLE ai_personas (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    consent_id TEXT NOT NULL REFERENCES consent_records(id),
    face_reference_asset_id TEXT REFERENCES media_assets(id),
    voice_reference_asset_id TEXT REFERENCES media_assets(id),
    style_json TEXT NOT NULL DEFAULT '{}',
    allowed_effects_json TEXT NOT NULL DEFAULT '[]',
    preferred_runtimes_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_ai_personas_project ON ai_personas(project_id);
