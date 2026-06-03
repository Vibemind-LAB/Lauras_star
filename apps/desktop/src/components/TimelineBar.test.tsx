import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { type LauraClient, type Timeline, type TimelineClip } from "../api";
import { TimelineBar } from "./TimelineBar";

// Thumbnails load lazily in ClipThumb; a never-resolving stub keeps the effect from
// updating state during these tests (we exercise interaction, not thumbnail loading).
function stubClient(): LauraClient {
  return { assetFrameUrl: () => new Promise<string>(() => undefined) } as unknown as LauraClient;
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
});
