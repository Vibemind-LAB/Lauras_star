ALTER TABLE sequence_items
ADD COLUMN transition_after_kind TEXT NOT NULL DEFAULT 'hard';

ALTER TABLE sequence_items
ADD COLUMN transition_after_frames INTEGER NOT NULL DEFAULT 0;
