// apps/desktop/src/shared/smoothEdge.ts
import type { BoundaryIdentity, SuggestedFix, TimelineClip } from "../api";

/**
 * The lane-0 boundary exactly at `atSeqFrame` that is a contiguous same-source jump
 * (asset_a==asset_b AND src_in_b==src_out_a) — the canonical dead-air cut a delete
 * produces. Mirrors transition_review's `same_source`. Caller MARKS it and offers a
 * one-tap smooth; it is never applied silently (spec §8). Null when no such edge.
 */
export function findSameSourceEdge(
  clips: TimelineClip[],
  atSeqFrame: number,
): BoundaryIdentity | null {
  const lane0 = clips
    .filter((c) => (c.lane ?? 0) === 0)
    .slice()
    .sort((a, b) => a.seq_in_frame - b.seq_in_frame);
  for (let i = 0; i < lane0.length - 1; i += 1) {
    const a = lane0[i]!;
    const b = lane0[i + 1]!;
    if (a.seq_out_frame_exclusive !== atSeqFrame || b.seq_in_frame !== atSeqFrame) continue;
    if (a.asset_id === b.asset_id && b.src_in_frame === a.src_out_frame_exclusive) {
      return {
        asset_a: a.asset_id,
        asset_b: b.asset_id,
        src_out_a: a.src_out_frame_exclusive,
        src_in_b: b.src_in_frame,
      };
    }
  }
  return null;
}

/**
 * Scans ALL lane-0 boundaries and returns the first contiguous same-source
 * jump-cut found (asset_a===asset_b AND src_in_b===src_out_a), independent of
 * the current playhead position. Used by FineCutView so the "Smooth transition"
 * button lights up automatically after any delete that creates such an edge
 * (spec §8 — Fix 3).
 */
export function findFirstSameSourceEdge(
  clips: TimelineClip[],
): BoundaryIdentity | null {
  const lane0 = clips
    .filter((c) => (c.lane ?? 0) === 0)
    .slice()
    .sort((a, b) => a.seq_in_frame - b.seq_in_frame);
  for (let i = 0; i < lane0.length - 1; i += 1) {
    const a = lane0[i]!;
    const b = lane0[i + 1]!;
    if (a.asset_id === b.asset_id && b.src_in_frame === a.src_out_frame_exclusive) {
      return {
        asset_a: a.asset_id,
        asset_b: b.asset_id,
        src_out_a: a.src_out_frame_exclusive,
        src_in_b: b.src_in_frame,
      };
    }
  }
  return null;
}

/** One-tap smooth payload: a short crossfade (spec §8 default). */
export function crossfadeFix(frames = 6): SuggestedFix {
  return { kind: "transition", transition_style: "crossfade", transition_frames: frames };
}
