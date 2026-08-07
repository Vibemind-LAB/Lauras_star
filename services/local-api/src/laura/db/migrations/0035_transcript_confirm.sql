-- Transcript confirmation gate (Transkript-Gates Task 1): a timestamp recording user approval of
-- an asset's transcript, blocking downstream confirmation gates (speech-window matching, etc.)
-- until the user explicitly confirms "this transcript is correct". Initially NULL; set to an
-- ISO 8601 UTC timestamp when confirmed.
ALTER TABLE media_assets ADD COLUMN transcript_confirmed_at TEXT DEFAULT NULL;
