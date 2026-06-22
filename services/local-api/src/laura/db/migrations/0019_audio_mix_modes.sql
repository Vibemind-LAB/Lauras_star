-- AV audio pro controls: allow A2 clips to control original-audio treatment.
CREATE TABLE timeline_audio_clips_v2 (
  id TEXT PRIMARY KEY,
  timeline_id TEXT NOT NULL REFERENCES timelines(id) ON DELETE CASCADE,
  asset_id TEXT NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
  seq_in_frame INTEGER NOT NULL,
  seq_out_frame_exclusive INTEGER NOT NULL,
  asset_in_frame INTEGER NOT NULL DEFAULT 0,
  gain_percent INTEGER NOT NULL DEFAULT 100,
  fade_in_frames INTEGER NOT NULL DEFAULT 0,
  fade_out_frames INTEGER NOT NULL DEFAULT 0,
  mix_mode TEXT NOT NULL DEFAULT 'mix',
  ducking_percent INTEGER NOT NULL DEFAULT 100,
  label TEXT,
  created_at TEXT NOT NULL,
  CHECK (seq_in_frame >= 0),
  CHECK (seq_out_frame_exclusive > seq_in_frame),
  CHECK (asset_in_frame >= 0),
  CHECK (gain_percent >= 0 AND gain_percent <= 400),
  CHECK (fade_in_frames >= 0),
  CHECK (fade_out_frames >= 0),
  CHECK (mix_mode IN ('mix', 'replace_original', 'mute_original')),
  CHECK (ducking_percent >= 0 AND ducking_percent <= 100)
);

INSERT INTO timeline_audio_clips_v2 (
  id, timeline_id, asset_id, seq_in_frame, seq_out_frame_exclusive, asset_in_frame,
  gain_percent, fade_in_frames, fade_out_frames, mix_mode, ducking_percent, label, created_at
)
SELECT
  id, timeline_id, asset_id, seq_in_frame, seq_out_frame_exclusive, asset_in_frame,
  gain_percent, fade_in_frames, fade_out_frames, mix_mode, 100, label, created_at
FROM timeline_audio_clips;

DROP TABLE timeline_audio_clips;
ALTER TABLE timeline_audio_clips_v2 RENAME TO timeline_audio_clips;

CREATE INDEX idx_timeline_audio_clips_timeline_order
  ON timeline_audio_clips (timeline_id, seq_in_frame, id);

CREATE INDEX idx_timeline_audio_clips_asset
  ON timeline_audio_clips (asset_id);
