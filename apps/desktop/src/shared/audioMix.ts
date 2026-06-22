import type { TimelineAudioClip } from "../api";

/** Sequence frame -> seconds, identical to mp4.py `_seconds(frame * rate_den / rate_num)`. */
export function seqFrameToSeconds(frame: number, rateNum: number, rateDen: number): number {
  return (frame * rateDen) / rateNum;
}

/** End-exclusive activity test: seq_in_frame <= seqFrame < seq_out_frame_exclusive. */
export function clipActiveAt(clip: TimelineAudioClip, seqFrame: number): boolean {
  return seqFrame >= clip.seq_in_frame && seqFrame < clip.seq_out_frame_exclusive;
}

/**
 * Source-file time (seconds) for this clip at `seqFrame`. Mirrors the export `atrim`
 * start: asset_in_frame + intra-clip offset, in seconds.
 */
export function clipSourceTimeSeconds(
  clip: TimelineAudioClip,
  seqFrame: number,
  rateNum: number,
  rateDen: number,
): number {
  const srcFrame = clip.asset_in_frame + (seqFrame - clip.seq_in_frame);
  return seqFrameToSeconds(Math.max(0, srcFrame), rateNum, rateDen);
}

/**
 * Effective gain (0..n) at `seqFrame`: gain_percent/100 * linear fade envelope.
 * Mirrors the export `volume` + `afade=t=in/out`. Zero outside the span.
 */
export function clipGainAt(
  clip: TimelineAudioClip,
  seqFrame: number,
  rateNum: number,
  rateDen: number,
): number {
  if (!clipActiveAt(clip, seqFrame)) return 0;
  const base = clip.gain_percent / 100;
  let env = 1;
  if (clip.fade_in_frames > 0) {
    const into = seqFrame - clip.seq_in_frame;
    if (into < clip.fade_in_frames) env = Math.min(env, into / clip.fade_in_frames);
  }
  if (clip.fade_out_frames > 0) {
    const untilEnd = clip.seq_out_frame_exclusive - seqFrame;
    if (untilEnd < clip.fade_out_frames) env = Math.min(env, untilEnd / clip.fade_out_frames);
  }
  // rateNum/rateDen reserved for sub-frame envelope precision; frame-granular here.
  void rateNum;
  void rateDen;
  return base * Math.max(0, env);
}

/**
 * Video-track duck factor (0..1) at `seqFrame`, mirroring mp4.py:
 * replace_original/mute_original -> 0; mix -> ducking_percent/100; 1 when no clip active.
 * Multiple overlapping clips take the strongest duck (lowest factor).
 */
export function videoDuckGainAt(clips: TimelineAudioClip[], seqFrame: number): number {
  let factor = 1;
  for (const c of clips) {
    if (!clipActiveAt(c, seqFrame)) continue;
    const f =
      c.mix_mode === "replace_original" || c.mix_mode === "mute_original"
        ? 0
        : c.ducking_percent / 100;
    if (f < factor) factor = f;
  }
  return factor;
}
