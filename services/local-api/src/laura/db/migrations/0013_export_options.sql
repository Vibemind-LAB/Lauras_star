-- Per-export render options (e.g. reel/vertical/hook flags) stored as JSON.
ALTER TABLE exports ADD COLUMN options TEXT;
