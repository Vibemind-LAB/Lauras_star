-- Persist resumable visual-selection work independently of Electron process state.
ALTER TABLE production_sessions ADD COLUMN conversation_id TEXT
    REFERENCES conversations(id) ON DELETE SET NULL;
ALTER TABLE production_sessions ADD COLUMN brief_text TEXT NOT NULL DEFAULT '';
ALTER TABLE production_sessions ADD COLUMN updated_utc TEXT NOT NULL DEFAULT '';

UPDATE production_sessions SET updated_utc = created_utc WHERE updated_utc = '';

CREATE INDEX idx_production_sessions_updated
    ON production_sessions(updated_utc DESC, session_id);
CREATE INDEX idx_production_sessions_conversation
    ON production_sessions(conversation_id);

CREATE TABLE visual_selection_drafts (
    session_id TEXT PRIMARY KEY
        REFERENCES production_sessions(session_id) ON DELETE CASCADE,
    proposal_hash TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    selections_json TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    updated_utc TEXT NOT NULL
);
