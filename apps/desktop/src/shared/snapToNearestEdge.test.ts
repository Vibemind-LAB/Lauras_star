import { describe, expect, it } from "vitest";

import { snapToNearestEdge } from "./snapToNearestEdge";

describe("snapToNearestEdge", () => {
  // -------------------------------------------------------------------------
  // Edge-case: empty edges list
  // -------------------------------------------------------------------------
  it("returns candidate unchanged when edges is empty", () => {
    expect(snapToNearestEdge(42, [], 5)).toBe(42);
  });

  it("returns candidate unchanged when edges is empty (frame=0)", () => {
    expect(snapToNearestEdge(0, [], 10)).toBe(0);
  });

  // -------------------------------------------------------------------------
  // No edge within threshold → unchanged
  // -------------------------------------------------------------------------
  it("returns candidate unchanged when no edge is within the threshold", () => {
    // candidate=50, nearest edge=40 → dist=10 > threshold=8 → no snap
    expect(snapToNearestEdge(50, [0, 40, 100], 8)).toBe(50);
  });

  it("returns candidate unchanged when nearest edge is exactly one frame beyond threshold", () => {
    // threshold=5, dist=6 → no snap
    expect(snapToNearestEdge(20, [14], 5)).toBe(20);
  });

  // -------------------------------------------------------------------------
  // Snaps to nearest edge within threshold
  // -------------------------------------------------------------------------
  it("snaps to the only edge when within threshold", () => {
    expect(snapToNearestEdge(10, [13], 5)).toBe(13);
  });

  it("snaps to the closest edge when multiple are within threshold", () => {
    // candidate=50, edges=[45, 53], threshold=8
    // dist(45)=5, dist(53)=3 → snap to 53
    expect(snapToNearestEdge(50, [45, 53], 8)).toBe(53);
  });

  it("snaps to the closer of two edges", () => {
    // candidate=20, edges=[15, 24], threshold=10
    // dist(15)=5, dist(24)=4 → snap to 24
    expect(snapToNearestEdge(20, [15, 24], 10)).toBe(24);
  });

  it("snaps when candidate is exactly at threshold distance", () => {
    // threshold=8, dist exactly 8 → should snap (≤ threshold)
    expect(snapToNearestEdge(10, [18], 8)).toBe(18);
    expect(snapToNearestEdge(10, [2], 8)).toBe(2);
  });

  it("does not snap when candidate is one frame beyond threshold", () => {
    expect(snapToNearestEdge(10, [19], 8)).toBe(10);
    expect(snapToNearestEdge(10, [1], 8)).toBe(10);
  });

  // -------------------------------------------------------------------------
  // Tie-breaking: equidistant edges → smaller frame index wins
  // -------------------------------------------------------------------------
  it("breaks ties by returning the smaller frame index", () => {
    // candidate=10, edges=[5, 15], threshold=10 — both at dist=5
    expect(snapToNearestEdge(10, [5, 15], 10)).toBe(5);
  });

  it("breaks ties by returning the smaller frame index (order independent)", () => {
    // same as above but edges in reverse order
    expect(snapToNearestEdge(10, [15, 5], 10)).toBe(5);
  });

  // -------------------------------------------------------------------------
  // Edge is exactly at the candidate → dist=0, always snaps
  // -------------------------------------------------------------------------
  it("returns the edge itself when an edge is at the candidate position", () => {
    expect(snapToNearestEdge(25, [10, 25, 40], 0)).toBe(25);
  });

  // -------------------------------------------------------------------------
  // Threshold=0 — only snaps when edge equals candidate exactly
  // -------------------------------------------------------------------------
  it("with threshold=0 snaps only when edge equals candidate", () => {
    expect(snapToNearestEdge(10, [10], 0)).toBe(10);
    expect(snapToNearestEdge(10, [11], 0)).toBe(10);
    expect(snapToNearestEdge(10, [9], 0)).toBe(10);
  });

  // -------------------------------------------------------------------------
  // Integer-frame contract: results must be integers from the edges array
  // -------------------------------------------------------------------------
  it("always returns an integer (edge value, not a float interpolation)", () => {
    const result = snapToNearestEdge(7, [0, 10, 20], 5);
    expect(Number.isInteger(result)).toBe(true);
  });

  // -------------------------------------------------------------------------
  // Cross-lane alignment scenario (spec §6 §3): candidate on V2, snapping to V1 boundary
  // -------------------------------------------------------------------------
  it("cross-lane alignment: snaps V2 drop to V1 boundary that is within threshold", () => {
    // V1 clips produce edges [0, 30, 30, 60, 60, 90]  (seq_in and seq_out for 3 clips)
    // Candidate from V2 drag-drop: 28 (2 frames from V1 edge at 30)
    // threshold = 5
    const v1Edges = [0, 30, 60, 90];
    expect(snapToNearestEdge(28, v1Edges, 5)).toBe(30);
  });

  it("cross-lane alignment: no snap when V2 candidate is far from any V1 boundary", () => {
    const v1Edges = [0, 30, 60, 90];
    expect(snapToNearestEdge(45, v1Edges, 5)).toBe(45);
  });
});
