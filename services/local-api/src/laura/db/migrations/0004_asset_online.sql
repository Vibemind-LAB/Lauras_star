-- Portion 15.5: explicit online/offline status for media assets.
-- Editorial import creates offline placeholders for clips whose media it could not
-- relink; the user resolves them by importing the real file (matched on source path).
-- Portable ADD COLUMN with a constant default (SQLite + PostgreSQL).
ALTER TABLE media_assets ADD COLUMN online INTEGER NOT NULL DEFAULT 1;
