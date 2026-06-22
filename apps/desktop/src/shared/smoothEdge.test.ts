// apps/desktop/src/shared/smoothEdge.test.ts
import { describe, expect, it } from "vitest";
import { crossfadeFix, findSameSourceEdge } from "./smoothEdge";

const clip = (o: Partial<Record<string, unknown>>) => ({
  id: "x", asset_id: "A", lane: 0,
  src_in_frame: 0, src_out_frame_exclusive: 10,
  seq_in_frame: 0, seq_out_frame_exclusive: 10, ...o,
}) as never;

describe("findSameSourceEdge", () => {
  it("flags a contiguous same-source jump-cut at the boundary", () => {
    const clips = [
      clip({ id: "a", asset_id: "A", src_in_frame: 0, src_out_frame_exclusive: 10,
             seq_in_frame: 0, seq_out_frame_exclusive: 10 }),
      clip({ id: "b", asset_id: "A", src_in_frame: 10, src_out_frame_exclusive: 25,
             seq_in_frame: 10, seq_out_frame_exclusive: 25 }),
    ];
    const id = findSameSourceEdge(clips, 10);
    expect(id).toEqual({ asset_a: "A", asset_b: "A", src_out_a: 10, src_in_b: 10 });
  });

  it("returns null across distinct assets (clean cut)", () => {
    const clips = [
      clip({ id: "a", asset_id: "A", src_out_frame_exclusive: 10, seq_out_frame_exclusive: 10 }),
      clip({ id: "b", asset_id: "B", src_in_frame: 0, src_out_frame_exclusive: 15,
             seq_in_frame: 10, seq_out_frame_exclusive: 25 }),
    ];
    expect(findSameSourceEdge(clips, 10)).toBeNull();
  });

  it("returns null when no boundary sits at the frame", () => {
    const clips = [clip({ id: "a", asset_id: "A" })];
    expect(findSameSourceEdge(clips, 10)).toBeNull();
  });
});

describe("crossfadeFix", () => {
  it("defaults to a 6-frame crossfade", () => {
    expect(crossfadeFix()).toEqual({
      kind: "transition", transition_style: "crossfade", transition_frames: 6,
    });
  });
});
