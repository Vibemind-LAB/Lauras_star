ALTER TABLE ai_runtimes
ADD COLUMN container_env_json TEXT NOT NULL DEFAULT '{}';
