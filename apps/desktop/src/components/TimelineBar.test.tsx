import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type LauraClient, type Timeline, type TimelineClip } from "../api";
import { TimelineBar } from "./TimelineBar";

// Auto-cleanup is not wired (no globals/setup file), so unmount between tests
// explicitly — otherwise leftover clip nodes make title queries ambiguous.
afterEach(cleanup);

// Thumbnails load lazily in ClipThumb; a never-resolving stub keeps the effect from
// updating state during these tests (we exercise interaction, not thumbnail loading).
function stubClient(over: Partial<LauraClient> = {}): LauraClient {
  return {
    assetFrameUrl: () => new Promise<string>(() => undefined),
    getAsset: () => new Promise(() => undefined),
    ...over,
  } as unknown as LauraClient;
}

function clip(over: Partial<TimelineClip> = {}): TimelineClip {
  return {
    id: "c1",
    asset_id: "a1",
    src_in_frame: 1423,
    src_out_frame_exclusive: 1454,
    seq_in_frame: 0,
    seq_out_frame_exclusive: 31,
    lane: 0,
    speaker_id: null,
    origin_word_start_id: null,
    origin_word_end_id: null,
    speed_num: 1,
    speed_den: 1,
    ...over,
  };
}

function timeline(clips: TimelineClip[]): Timeline {
  return { id: "t1", project_id: "p1", name: "Rough Cut", kind: "rough_cut", created_at: "", clips };
}

describe("TimelineBar", () => {
  it("prompts to pick a project when there is no timeline", () => {
    const { container } = render(
      <TimelineBar client={stubClient()} timeline={null} onChange={() => undefined} />,
    );
    expect(container.textContent ?? "").toContain("wähle ein Projekt");
  });

  it("scrubs the player to a clip's source IN frame when its thumbnail is clicked", () => {
    const onScrub = vi.fn();
    const { getByTitle } = render(
      <TimelineBar
        client={stubClient()}
        timeline={timeline([clip({ asset_id: "vid-42", src_in_frame: 1518 })])}
        onChange={() => undefined}
        onScrub={onScrub}
      />,
    );
    fireEvent.click(getByTitle(/^Clip 1/));
    expect(onScrub).toHaveBeenCalledWith("vid-42", 1518);
  });

  it("reorders clips via drag-and-drop with op:move (drop-before semantics)", () => {
    const a = clip({ id: "A", seq_in_frame: 0, seq_out_frame_exclusive: 31 });
    const b = clip({ id: "B", seq_in_frame: 31, seq_out_frame_exclusive: 62 });
    const tl = timeline([a, b]);
    const applyOperation = vi.fn(() => Promise.resolve(tl));
    const { getByTitle } = render(
      <TimelineBar
        client={stubClient({ applyOperation })}
        timeline={tl}
        onChange={() => undefined}
      />,
    );
    // Drag clip A (seq_in 0) and drop it onto clip B (seq_in 31): move A before B.
    fireEvent.dragStart(getByTitle(/^Clip 1/));
    fireEvent.drop(getByTitle(/^Clip 2/));
    expect(applyOperation).toHaveBeenCalledWith("t1", {
      op: "move",
      at_seq_frame: 0,
      to_seq_frame: 31,
    });
  });
});
