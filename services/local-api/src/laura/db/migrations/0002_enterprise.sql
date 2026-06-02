-- Enterprise: multi-tenancy, RBAC, API keys, and an append-only audit log
-- (docs/14-enterprise.md). Additive — local/desktop mode keeps working with an
-- implicit "owner" principal and NULL org_id.

CREATE TABLE organizations (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE users (
    id            TEXT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    display_name  TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE memberships (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,            -- owner|admin|editor|exporter|reviewer
    created_at  TEXT NOT NULL,
    UNIQUE(org_id, user_id)
);
CREATE INDEX idx_memberships_org ON memberships(org_id);

CREATE TABLE api_keys (
    id            TEXT PRIMARY KEY,
    org_id        TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id       TEXT REFERENCES users(id) ON DELETE SET NULL,
    name          TEXT,
    prefix        TEXT NOT NULL,          -- shown for identification
    key_hash      TEXT NOT NULL UNIQUE,   -- sha256 of the full key (never stored raw)
    role          TEXT NOT NULL,
    revoked       INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    last_used_at  TEXT
);
CREATE INDEX idx_api_keys_prefix ON api_keys(prefix);

CREATE TABLE audit_events (
    id             TEXT PRIMARY KEY,
    org_id         TEXT,
    principal_kind TEXT NOT NULL,         -- local|key
    principal_id   TEXT,
    action         TEXT NOT NULL,         -- e.g. project.create, export.create
    entity_type    TEXT,
    entity_id      TEXT,
    payload_json   TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL
);
CREATE INDEX idx_audit_org_created ON audit_events(org_id, created_at);

ALTER TABLE projects ADD COLUMN org_id TEXT;
