-- Chat-first (spec 2026-08-03): a global conversation list (ChatGPT-style). A conversation
-- "stands" on an active project (switchable per command); messages carry their variance in
-- content_json (kind: text | approval_request | action). seq gives gapless per-thread order.
CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    active_project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE conversation_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    kind TEXT NOT NULL CHECK (kind IN ('text', 'approval_request', 'action')),
    content_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (conversation_id, seq)
);

CREATE INDEX idx_conversation_messages_thread
    ON conversation_messages(conversation_id, seq);
