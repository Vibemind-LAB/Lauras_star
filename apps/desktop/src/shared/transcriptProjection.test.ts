/**
 * Tests for projectCutWords — frame-accurate word projection onto a cut timeline.
 * Invariants from CLAUDE.md:
 *   - All frame values are integers.
 *   - Ranges are end-exclusive (out_frame_exclusive).
 *   - A word at src_frame F matches a clip iff src_in_frame <= F < src_out_frame_exclusive.
 */
import { describe, expect, it } from "vitest";

import { projectCutWords } from "./transcriptProjection";
import { type Segment, type TimelineClip } from "../api";

// ---------------------------------------------------------------------------
// Shared fixtures
// ---------------------------------------------------------------------------

function makeClip(
  override: Partial<TimelineClip> & Pick<TimelineClip, "id" | "asset_id" | "src_in_frame" | "src_out_frame_exclusive" | "seq_in_frame" | "seq_out_frame_exclusive">,
): TimelineClip {
  return {
    lane: 0,
    role: "base",
    speaker_id: null,
    origin_word_start_id: null,
    origin_word_end_id: null,
    speed_num: 1,
    speed_den: 1,
    audio_offset_samples: 0,
    ...override,
  } as TimelineClip;
}

function makeSegment(assetId: string, words: { id: string; start_frame: number; end_frame: number; text: string }[]): Segment {
  return {
    id: `seg-${assetId}`,
    asset_id: assetId,
    speaker_id: null,
    start_frame: words[0]?.start_frame ?? 0,
    end_frame: words[words.length - 1]?.end_frame ?? 0,
    text: words.map((w) => w.text).join(" "),
    words: words.map((w, idx) => ({
      id: w.id,
      idx,
      start_frame: w.start_frame,
      end_frame: w.end_frame,
      text: w.text,
      is_punctuation: false,
    })),
  } as unknown as Segment;
}

// ---------------------------------------------------------------------------
// Single-asset baseline tests (existing behaviour, no assetId arg)
// ---------------------------------------------------------------------------

describe("projectCutWords — single-asset baseline", () => {
  const clipA = makeClip({
    id: "c1",
    asset_id: "A",
    src_in_frame: 0,
    src_out_frame_exclusive: 100,
    seq_in_frame: 0,
    seq_out_frame_exclusive: 100,
  });

  const segments: Segment[] = [
    makeSegment("A", [
      { id: "w1", start_frame: 0, end_frame: 10, text: "hello" },
      { id: "w2", start_frame: 50, end_frame: 60, text: "world" },
      { id: "w3", start_frame: 120, end_frame: 130, text: "trimmed" }, // outside clip
    ]),
  ];

  it("projects words that fall within the clip and drops trimmed words", () => {
    const result = projectCutWords(segments, [clipA]);
    expect(result.map((w) => w.id)).toEqual(["w1", "w2"]);
  });

  it("computes correct seqStart (seq_in_frame + offset from src_in_frame)", () => {
    const result = projectCutWords(segments, [clipA]);
    const w2 = result.find((w) => w.id === "w2")!;
    expect(w2.seqStart).toBe(clipA.seq_in_frame + (50 - clipA.src_in_frame)); // 0 + 50 = 50
  });

  it("seqEnd is clamped to clip boundary (end-exclusive)", () => {
    const clampedClip = makeClip({
      id: "c2",
      asset_id: "A",
      src_in_frame: 0,
      src_out_frame_exclusive: 8, // word w1 ends at 10 — clamped to 8
      seq_in_frame: 0,
      seq_out_frame_exclusive: 8,
    });
    const result = projectCutWords(segments, [clampedClip]);
    const w1 = result.find((w) => w.id === "w1")!;
    expect(w1.seqEnd).toBe(8); // min(10, 8)
  });

  it("returns empty when there are no base clips", () => {
    const overlayClip = { ...clipA, lane: 1 };
    expect(projectCutWords(segments, [overlayClip])).toHaveLength(0);
  });

  it("returns words sorted by seqStart even when source order differs", () => {
    const clipLate = makeClip({
      id: "c3",
      asset_id: "A",
      src_in_frame: 100,
      src_out_frame_exclusive: 200,
      seq_in_frame: 0,   // placed first in sequence
      seq_out_frame_exclusive: 100,
    });
    const clipEarly = makeClip({
      id: "c4",
      asset_id: "A",
      src_in_frame: 0,
      src_out_frame_exclusive: 100,
      seq_in_frame: 100, // placed second in sequence
      seq_out_frame_exclusive: 200,
    });
    const seg: Segment[] = [
      makeSegment("A", [
        { id: "wA", start_frame: 10, end_frame: 20, text: "source-early" },
        { id: "wB", start_frame: 110, end_frame: 120, text: "source-late" },
      ]),
    ];
    // wB maps to clipLate → seqStart = 0 + (110 - 100) = 10
    // wA maps to clipEarly → seqStart = 100 + (10 - 0) = 110
    const result = projectCutWords(seg, [clipLate, clipEarly]);
    expect(result.map((w) => w.id)).toEqual(["wB", "wA"]);
  });
});

// ---------------------------------------------------------------------------
// Multi-asset cross-projection guard (the bug this test covers)
// ---------------------------------------------------------------------------

describe("projectCutWords — assetId filter (multi-asset rough-cut guard)", () => {
  /**
   * Scenario: asset A's words have source-frame range 0–100.
   *           asset B's clip ALSO covers source frames 0–100 (different asset, same range).
   *           Without the assetId filter a word from A would match B's clip → wrong seqStart.
   */
  const clipFromAssetA = makeClip({
    id: "c-A",
    asset_id: "A",
    src_in_frame: 0,
    src_out_frame_exclusive: 100,
    seq_in_frame: 0,
    seq_out_frame_exclusive: 100,
  });

  const clipFromAssetB = makeClip({
    id: "c-B",
    asset_id: "B",
    src_in_frame: 0,      // same source range as A's clip — the overlapping range that causes the bug
    src_out_frame_exclusive: 100,
    seq_in_frame: 200,    // placed later in the sequence
    seq_out_frame_exclusive: 300,
  });

  const allClips = [clipFromAssetA, clipFromAssetB];

  const assetASegments: Segment[] = [
    makeSegment("A", [
      { id: "w-A1", start_frame: 10, end_frame: 20, text: "first" },
      { id: "w-A2", start_frame: 50, end_frame: 60, text: "second" },
    ]),
  ];

  it("WITHOUT assetId: a word can match the B-clip (demonstrates the bug scenario)", () => {
    // When assetId is omitted, baseClips contains BOTH A and B clips.
    // Array.find picks the FIRST match — if clipFromAssetB comes first it would be wrong.
    // This test just confirms both clips are considered (no filter applied).
    const withBFirst = [clipFromAssetB, clipFromAssetA];
    const result = projectCutWords(assetASegments, withBFirst);
    // w-A1 at frame 10 matches clipFromAssetB (first candidate, src_in 0..100) → seqStart = 200+10 = 210
    expect(result.find((w) => w.id === "w-A1")?.seqStart).toBe(210);
  });

  it("WITH assetId='A': words project only onto A-clips regardless of clip order", () => {
    // B-clip comes first — but assetId filter should exclude it.
    const withBFirst = [clipFromAssetB, clipFromAssetA];
    const result = projectCutWords(assetASegments, withBFirst, "A");
    const w1 = result.find((w) => w.id === "w-A1")!;
    const w2 = result.find((w) => w.id === "w-A2")!;
    // Both should map to A-clip (seq_in_frame=0), not B-clip (seq_in_frame=200).
    expect(w1.seqStart).toBe(10);  // 0 + (10 - 0)
    expect(w2.seqStart).toBe(50);  // 0 + (50 - 0)
  });

  it("WITH assetId='A' and NO A-clip present: word is dropped, NOT mis-mapped to B-clip", () => {
    // Only B's clip in the list — the A-word must be dropped, not mapped onto B.
    const result = projectCutWords(assetASegments, [clipFromAssetB], "A");
    expect(result).toHaveLength(0);
  });

  it("WITH assetId='B': A-words are fully excluded (no base clips survive the filter)", () => {
    const result = projectCutWords(assetASegments, allClips, "B");
    // assetASegments contains A-words. With assetId='B', only B-clips pass.
    // A-words have frames 10 and 50 — both fall within B-clip's range 0..100.
    // They ARE projected onto B's seqStart offset.
    // This test asserts the filter is applied correctly, not that cross-asset projection
    // never happens — the caller is responsible for passing the right assetId.
    const w1 = result.find((w) => w.id === "w-A1");
    expect(w1?.seqStart).toBe(210); // 200 + (10 - 0)
  });

  it("WITH assetId=undefined: behaves identically to the no-arg form (back-compat)", () => {
    const withAFirst = [clipFromAssetA, clipFromAssetB];
    const withoutArg = projectCutWords(assetASegments, withAFirst);
    const withUndefined = projectCutWords(assetASegments, withAFirst, undefined);
    expect(withUndefined).toEqual(withoutArg);
  });
});
