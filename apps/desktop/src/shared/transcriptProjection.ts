/**
 * Pure helper: project source-asset transcript words onto the cut timeline.
 *
 * Words that fall outside every clip (trimmed out) are dropped.
 * The result is sorted by seqStart (cut order), NOT source order.
 *
 * All frame values are integers; no floats are stored or returned.
 */
import { type Segment, type TimelineClip } from "../api";

export interface CutWord {
  /** Original word id (stable across reloads). */
  id: string;
  text: string;
  /** Source frame at which this word starts (used for seek & highlight). */
  srcFrame: number;
  /** Source frame at which this word ends (exclusive). */
  srcEndFrame: number;
  /** Sequence frame at which this word starts (cut order). */
  seqStart: number;
  /** Sequence frame at which this word ends (exclusive). */
  seqEnd: number;
}

/**
 * Project every word in `segments` onto the cut defined by `clips`.
 * Only base clips (lane 0) are considered, matching TimelineBar's behaviour.
 *
 * @param segments - Raw source-asset transcript segments (all words, source order).
 * @param clips    - The scene's timeline clips (any lane).
 * @param assetId  - When provided, restrict base-clip matching to clips whose
 *                   `asset_id` equals this value. This prevents words from one
 *                   asset being mis-projected onto a different asset's clip whose
 *                   source-frame range happens to overlap on a multi-asset rough-cut.
 *                   Omit (or pass `undefined`) for single-asset callers — behaviour
 *                   is identical to the pre-filter version.
 * @returns        Words surviving the cut, sorted by seqStart.
 */
export function projectCutWords(
  segments: Segment[],
  clips: TimelineClip[],
  assetId?: string,
): CutWord[] {
  const baseClips = clips.filter(
    (c) => (c.lane ?? 0) === 0 && (assetId === undefined || c.asset_id === assetId),
  );
  if (baseClips.length === 0) return [];

  const result: CutWord[] = [];

  for (const seg of segments) {
    for (const w of seg.words) {
      const clip = baseClips.find(
        (c) =>
          c.src_in_frame <= w.start_frame &&
          w.start_frame < c.src_out_frame_exclusive,
      );
      if (!clip) continue; // trimmed out

      const seqStart = clip.seq_in_frame + (w.start_frame - clip.src_in_frame);
      const srcEnd = Math.min(w.end_frame, clip.src_out_frame_exclusive);
      const seqEnd = clip.seq_in_frame + (srcEnd - clip.src_in_frame);

      result.push({
        id: w.id,
        text: w.text,
        srcFrame: w.start_frame,
        srcEndFrame: w.end_frame,
        seqStart,
        seqEnd,
      });
    }
  }

  return result.sort((a, b) => a.seqStart - b.seqStart);
}
