import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  type Asset,
  type BuildFromShotsResult,
  type LauraClient,
  type Scene,
  type Timeline,
} from "../api";
import { DEFAULT_CUT_BIAS } from "./BiasSlider";
import { RoughCutView } from "./RoughCutView";

vi.mock("./Player", () => ({ Player: () => <div data-testid="player" /> }));
vi.mock("./SceneStrip", () => ({ SceneStrip: () => <div data-testid="scene-strip" /> }));

afterEach(cleanup);

const asset = { id: "a", rate_num: 30, rate_den: 1, display_name: "a" } as unknown as Asset;
const emptyRc: Timeline = { id: "tl", project_id: "p", name: "rc", kind: "rough_cut", created_at: "", clips: [] };
const SCENE: Scene = { id: "s1", project_id: "p", source_timeline_id: "tl", name: "Szene 1",
  order_index: 0, seq_in_frame: 0, seq_out_frame_exclusive: 30 };

function buildResult(over: Partial<BuildFromShotsResult> = {}): BuildFromShotsResult {
  return {
    timeline: emptyRc,
    dropped: [],
    split_cuts: [],
    quality: null,
    ...over,
  };
}

function client(over: Partial<LauraClient>): LauraClient {
  return {
    listScenes: vi.fn().mockResolvedValue([]),
    generateScenes: vi.fn().mockResolvedValue([SCENE]),
    buildRoughCutFromShots: vi.fn().mockResolvedValue(buildResult()),
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
    // Builds with the default cut_bias forwarded as the 4th argument.
    expect(c.buildRoughCutFromShots).toHaveBeenCalledWith("p", "a", "tl", {
      cutBias: DEFAULT_CUT_BIAS,
    });
    expect(onRoughCutChange).toHaveBeenCalled();
  });

  it("skips the build when the rough cut already has clips", async () => {
    const c = client({});
    const rc: Timeline = { ...emptyRc, clips: [
      { id: "c1", asset_id: "a", src_in_frame: 0, src_out_frame_exclusive: 30, seq_in_frame: 0,
        seq_out_frame_exclusive: 30, lane: 0, speaker_id: null, origin_word_start_id: null,
        origin_word_end_id: null, speed_num: 1, speed_den: 1, audio_offset_samples: 0 }] };
    const { getByText } = render(
      <RoughCutView client={c} projectId="p" asset={asset} roughCut={rc}
        segments={[]} onRoughCutChange={vi.fn().mockResolvedValue(undefined)}
        seek={null} currentFrame={0} onSeek={vi.fn()} onFrame={vi.fn()} />,
    );
    fireEvent.click(getByText("Szenen erzeugen"));
    await waitFor(() => expect(c.generateScenes).toHaveBeenCalled());
    expect(c.buildRoughCutFromShots).not.toHaveBeenCalled();
  });

  it("rebuilds with the chosen cut_bias when the slider moves", async () => {
    const c = client({});
    const { getByLabelText } = render(
      <RoughCutView client={c} projectId="p" asset={asset} roughCut={emptyRc}
        segments={[]} onRoughCutChange={vi.fn().mockResolvedValue(undefined)}
        seek={null} currentFrame={0} onSeek={vi.fn()} onFrame={vi.fn()} />,
    );
    const slider = getByLabelText(/Schnitt-Bias/);
    fireEvent.change(slider, { target: { value: "0.8" } });
    await waitFor(() =>
      // A fresh timeline (timelineId omitted) at the new bias.
      expect(c.buildRoughCutFromShots).toHaveBeenCalledWith("p", "a", undefined, { cutBias: 0.8 }),
    );
  });

  it("renders the quality panel after a build returns a score", async () => {
    const c = client({
      buildRoughCutFromShots: vi.fn().mockResolvedValue(
        buildResult({
          quality: {
            overall: 0.82,
            visual_exactness: 0.9,
            editorial_cleanliness: 0.7,
            n_cuts: 4,
            n_split_cuts: 2,
          },
          split_cuts: [
            { seq_cut: 50, video_frame: 50, audio_frame: 53, offset: 3, kind: "L" },
            { seq_cut: 90, video_frame: 90, audio_frame: 87, offset: -3, kind: "J" },
          ],
        }),
      ),
    });
    const { getByText, getByTestId } = render(
      <RoughCutView client={c} projectId="p" asset={asset} roughCut={emptyRc}
        segments={[]} onRoughCutChange={vi.fn().mockResolvedValue(undefined)}
        seek={null} currentFrame={0} onSeek={vi.fn()} onFrame={vi.fn()} />,
    );
    fireEvent.click(getByText("Szenen erzeugen"));
    await waitFor(() => expect(getByTestId("quality-panel")).toBeTruthy());
    expect(getByTestId("quality-panel").textContent).toContain("82%");
    expect(getByTestId("quality-panel").textContent).toContain("2 Split-Cuts empfohlen");
  });
});
