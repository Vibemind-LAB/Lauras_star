-- Feinschnitt: link a scene to its materialised editable sub-timeline, plus one music
-- asset + gain per scene (music columns are used by increment 4b).
ALTER TABLE scenes ADD COLUMN scene_timeline_id TEXT;
ALTER TABLE scenes ADD COLUMN music_asset_id TEXT;
ALTER TABLE scenes ADD COLUMN music_gain_percent INTEGER NOT NULL DEFAULT 100;
