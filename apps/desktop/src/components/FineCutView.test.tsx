import { fireEvent, render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { type Asset, type LauraClient, type Scene, type Timeline } from "../api";
import { FineCutView } from "./FineCutView";

vi.mock("./Player", () => ({ Player: () => <div data-testid="player" /> }));
vi.mock("./TimelineBar", () => ({ TimelineBar: () => <div data-testid="timeline" /> }));
vi.mock("./SceneInspector", () => ({ SceneInspector: () => <div data-testid="inspector" /> }));
vi.mock("./SceneMusicControls", () => ({ SceneMusicControls: () => null }));
vi.mock("./TranscriptBar", () => ({
  TranscriptBar: (p: { onDeleteWords?: (a: string, b: string) => void }) => (
    <button type="button" onClick={() => p.onDeleteWords?.("w0", "w1")}>cut-word</button>
  ),
}));

const asset = { id: "a", rate_num: 30, rate_den: 1, display_name: "a" } as unknown as Asset;
const TL: Timeline = { id: "stl", project_id: "p", name: "Szene 1", kind: "scene", created_at: "", clips: [] };
const scenes: Scene[] = [{ id: "s1", project_id: "p", source_timeline_id: "tl", name: "Szene 1",
  order_index: 0, seq_in_frame: 0, seq_out_frame_exclusive: 30, scene_timeline_id: null,
  music_asset_id: null, music_gain_percent: 100 }];

function client(over: Partial<LauraClient>): LauraClient {
  return { listScenes: vi.fn().mockResolvedValue(scenes), openScene: vi.fn().mockResolvedValue(TL),
    deleteWords: vi.fn().mockResolvedValue(TL), getTranscript: vi.fn().mockResolvedValue([]),
    ...over } as unknown as LauraClient;
}

describe("FineCutView", () => {
  it("opens the first scene and routes a transcript cut to deleteWords", async () => {
    const c = client({});
    const { getByText } = render(
      <FineCutView client={c} asset={asset} roughCutId="tl" segments={[]}
        currentFrame={0} seek={null} onSeek={vi.fn()} onFrame={vi.fn()} />);
    await waitFor(() => expect(c.openScene).toHaveBeenCalledWith("s1"));
    fireEvent.click(getByText("cut-word"));
    await waitFor(() => expect(c.deleteWords).toHaveBeenCalledWith("stl", "w0", "w1"));
  });
});
