-- Per-asset resolved policy (one row per asset, upsert on re-resolve).
-- policy_source records which tier of the precedence chain won.
-- ON DELETE CASCADE mirrors the transition_reviews / timeline_quality idiom so rows vanish with the asset.
CREATE TABLE asset_policies (
    asset_id TEXT PRIMARY KEY REFERENCES media_assets(id) ON DELETE CASCADE,
    policy TEXT NOT NULL,
    policy_source TEXT NOT NULL CHECK (policy_source IN ('row', 'pattern', 'env', 'default')),
    resolved_at TEXT NOT NULL
);
