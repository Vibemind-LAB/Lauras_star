/**
 * snapToNearestEdge — pure, DOM-free frame-domain snap helper (spec §6, P4).
 *
 * Given a candidate sequence frame position and a list of edge frames (e.g. all
 * seq_in / seq_out_frame_exclusive values across ALL lanes, plus 0 and total), snaps
 * the candidate to the nearest edge that lies within `thresholdFrames`.  When no edge
 * is within the threshold the candidate is returned unchanged.
 *
 * Contract:
 * - All values are integers (whole frames, invariant #1).
 * - Ranges are end-exclusive (invariant #2) — callers include both seq_in AND
 *   seq_out_frame_exclusive of every clip as candidate snap targets.
 * - The function is pure (no side effects, no DOM reads).
 * - Tie-breaking: when two edges are equidistant, the smaller frame index wins
 *   (stable, deterministic).
 * - Empty `edges` → `frame` returned unchanged.
 *
 * Usage (cross-lane drag drop, spec §6 §3):
 *   const snapped = snapToNearestEdge(
 *     candidateSeqIn,
 *     allEdges,          // collect from tl.clips across all lanes + [0, total]
 *     thresholdFrames,   // convert SNAP_PX to frames at the current scale: Math.ceil(SNAP_PX * framesPerPx)
 *   );
 */
export function snapToNearestEdge(
  frame: number,
  edges: readonly number[],
  thresholdFrames: number,
): number {
  if (edges.length === 0) return frame;

  let bestFrame = frame;
  // Use thresholdFrames + 1 so exact-threshold edges snap (≤ threshold).
  let bestDist = thresholdFrames + 1;

  for (const edge of edges) {
    const dist = Math.abs(edge - frame);
    if (dist < bestDist || (dist === bestDist && edge < bestFrame)) {
      bestDist = dist;
      bestFrame = edge;
    }
  }

  // Only snap when within threshold (bestDist was initialised to threshold+1).
  return bestDist <= thresholdFrames ? bestFrame : frame;
}
