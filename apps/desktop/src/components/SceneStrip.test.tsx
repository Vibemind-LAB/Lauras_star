// apps/desktop/src/components/SceneStrip.test.tsx
import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { type Asset, type LauraClient, type Scene, type Segment, type TimelineClip } from "../api";
import { SceneStrip } from "./SceneStrip";

const asset = { id: "a", rate_num: 30, rate_den: 1 } as unknown as Asset;
const clip = (id: string, sin: number, sout: number): TimelineClip =>
  ({ id, asset_id: "a", src_in_frame: sin, src_out_frame_exclusive: sout,
     seq_in_frame: sin, seq_out_frame_exclusive: sout, lane: 0, speaker_id: null,
     origin_word_start_id: null, origin_word_end_id: null, speed_num: 1, speed_den: 1,
     audio_offset_samples: 0 });
const scenes: Scene[] = [
  { id: "s1", project_id: "p", source_timeline_id: "tl", name: "Szene 1",
    order_index: 0, seq_in_frame: 0, seq_out_frame_exclusive: 60 },
  { id: "s2", project_id: "p", source_timeline_id: "tl", name: "Szene 2",
    order_index: 1, seq_in_frame: 60, seq_out_frame_exclusive: 90 },
];
const clips = [clip("c1", 0, 30), clip("c2", 30, 60), clip("c3", 60, 90)];
const segments: Segment[] = [];

function client(): LauraClient {
  // Use a never-resolving promise so URL.revokeObjectURL is never called during
  // cleanup (jsdom does not implement it). Mirrors the SceneInspector test pattern.
  return { assetFrameUrl: vi.fn().mockReturnValue(new Promise<string>(() => undefined)) } as unknown as LauraClient;
}

describe("SceneStrip", () => {
  it("renders one card per scene with its name", () => {
    const { getByText } = render(
      <SceneStrip client={client()} asset={asset} scenes={scenes} clips={clips}
        segments={segments} onSplit={vi.fn()} onMerge={vi.fn()} onRename={vi.fn()} onSeek={vi.fn()} />,
    );
    expect(getByText("Szene 1")).toBeTruthy();
    expect(getByText("Szene 2")).toBeTruthy();
  });

  it("seeks to a scene's start frame on card click", () => {
    const onSeek = vi.fn();
    const { getByText } = render(
      <SceneStrip client={client()} asset={asset} scenes={scenes} clips={clips}
        segments={segments} onSplit={vi.fn()} onMerge={vi.fn()} onRename={vi.fn()}
        onSeek={onSeek} />,
    );
    fireEvent.click(getByText("Szene 2"));
    expect(onSeek).toHaveBeenCalledWith(60);
  });

  it("merge button is hidden on the last scene", () => {
    const onMerge = vi.fn();
    const { getAllByTitle } = render(
      <SceneStrip client={client()} asset={asset} scenes={scenes} clips={clips}
        segments={segments} onSplit={vi.fn()} onMerge={onMerge} onRename={vi.fn()} onSeek={vi.fn()} />,
    );
    // only the first scene (has a successor) shows a merge button
    expect(getAllByTitle("Mit nächster Szene zusammenführen").length).toBe(1);
  });

  it("split at the middle clip boundary of a multi-clip scene", () => {
    const onSplit = vi.fn();
    const { getAllByTitle } = render(
      <SceneStrip client={client()} asset={asset} scenes={scenes} clips={clips}
        segments={segments} onSplit={onSplit} onMerge={vi.fn()} onRename={vi.fn()} onSeek={vi.fn()} />,
    );
    fireEvent.click(getAllByTitle("Szene teilen")[0]); // scene 1 spans clips c1,c2 -> boundary 30
    expect(onSplit).toHaveBeenCalledWith("s1", 30);
  });
});
