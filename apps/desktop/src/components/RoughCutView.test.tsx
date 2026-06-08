import { fireEvent, render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { type Asset, type LauraClient, type Scene, type Timeline } from "../api";
import { RoughCutView } from "./RoughCutView";

vi.mock("./Player", () => ({ Player: () => <div data-testid="player" /> }));
vi.mock("./SceneStrip", () => ({ SceneStrip: () => <div data-testid="scene-strip" /> }));

const asset = { id: "a", rate_num: 30, rate_den: 1, display_name: "a" } as unknown as Asset;
const emptyRc: Timeline = { id: "tl", project_id: "p", name: "rc", kind: "rough_cut", created_at: "", clips: [] };
const SCENE: Scene = { id: "s1", project_id: "p", source_timeline_id: "tl", name: "Szene 1",
  order_index: 0, seq_in_frame: 0, seq_out_frame_exclusive: 30 };

function client(over: Partial<LauraClient>): LauraClient {
  return {
    listScenes: vi.fn().mockResolvedValue([]),
    generateScenes: vi.fn().mockResolvedValue([SCENE]),
    buildRoughCutFromShots: vi.fn().mockResolvedValue({}),
    splitScene: vi.fn(), mergeScenes: vi.fn(), renameScene: vi.fn(),
    assetFrameUrl: vi.fn().mockResolvedValue("blob:x"),
    ...over,
  } as unknown as LauraClient;
}

describe("RoughCutView", () => {
  it("builds the rough cut then generates scenes when clips are empty", async () => {
    const c = client({});
    const onRoughCutChange = vi.fn().mockResolvedValue(undefined);
    const { getByText } = render(
      <RoughCutView client={c} projectId="p" asset={asset} roughCut={emptyRc}
        segments={[]} onRoughCutChange={onRoughCutChange}
        seek={null} currentFrame={0} onSeek={vi.fn()} onFrame={vi.fn()} />,
    );
    fireEvent.click(getByText("Szenen erzeugen"));
    await waitFor(() => expect(c.generateScenes).toHaveBeenCalledWith("tl", "a"));
    expect(c.buildRoughCutFromShots).toHaveBeenCalledWith("p", "a", "tl");
    expect(onRoughCutChange).toHaveBeenCalled();
  });

  it("skips the build when the rough cut already has clips", async () => {
    const c = client({});
    const rc: Timeline = { ...emptyRc, clips: [
      { id: "c1", asset_id: "a", src_in_frame: 0, src_out_frame_exclusive: 30, seq_in_frame: 0,
        seq_out_frame_exclusive: 30, lane: 0, speaker_id: null, origin_word_start_id: null,
        origin_word_end_id: null, speed_num: 1, speed_den: 1 }] };
    const { getByText } = render(
      <RoughCutView client={c} projectId="p" asset={asset} roughCut={rc}
        segments={[]} onRoughCutChange={vi.fn().mockResolvedValue(undefined)}
        seek={null} currentFrame={0} onSeek={vi.fn()} onFrame={vi.fn()} />,
    );
    fireEvent.click(getByText("Szenen erzeugen"));
    await waitFor(() => expect(c.generateScenes).toHaveBeenCalled());
    expect(c.buildRoughCutFromShots).not.toHaveBeenCalled();
  });
});
