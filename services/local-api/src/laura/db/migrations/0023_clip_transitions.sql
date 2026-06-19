-- Clip-level video transitions (Plan A): the transition that plays AFTER this clip,
-- mirroring sequence_items (0021) so a smoothness fix (crossfade/fade) applies on
-- rough_cut/scene timelines too, not only on assembled sequences.
-- kind in {'hard','fade','crossfade'}; frames is the transition duration in TIMELINE frames.
ALTER TABLE timeline_clips ADD COLUMN transition_after_kind TEXT NOT NULL DEFAULT 'hard';
ALTER TABLE timeline_clips ADD COLUMN transition_after_frames INTEGER NOT NULL DEFAULT 0;
