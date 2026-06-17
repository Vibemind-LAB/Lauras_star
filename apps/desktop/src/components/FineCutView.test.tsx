import { fireEvent, render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { type Asset, type LauraClient, type Scene, type Timeline } from "../api";
import { FineCutView } from "./FineCutView";

vi.mock("./SequencePlayer", () => ({ SequencePlayer: () => <div data-testid="player" /> }));
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
const scenes2: Scene[] = [
  scenes[0],
  { id: "s2", project_id: "p", source_timeline_id: "tl", name: "Szene 2",
    order_index: 1, seq_in_frame: 30, seq_out_frame_exclusive: 60, scene_timeline_id: null,
    music_asset_id: null, music_gain_percent: 100 },
];

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

  it("opens a clicked scene and seeks to its first source frame", async () => {
    const scene2Timeline: Timeline = {
      ...TL,
      id: "stl2",
      clips: [{
        id: "c2",
        asset_id: "a",
        src_in_frame: 123,
        src_out_frame_exclusive: 150,
        seq_in_frame: 0,
        seq_out_frame_exclusive: 27,
        lane: 0,
        role: "base",
        speaker_id: null,
        origin_word_start_id: null,
        origin_word_end_id: null,
        speed_num: 1,
        speed_den: 1,
        audio_offset_samples: 0,
      }],
    };
    const c = client({
      listScenes: vi.fn().mockResolvedValue(scenes2),
      openScene: vi.fn().mockImplementation((sceneId: string) =>
        Promise.resolve(sceneId === "s2" ? scene2Timeline : TL),
      ),
    });
    const onSeek = vi.fn();
    const { findByText } = render(
      <FineCutView client={c} asset={asset} roughCutId="tl" segments={[]}
        currentFrame={0} seek={null} onSeek={onSeek} onFrame={vi.fn()} />);

    fireEvent.click(await findByText("Szene 2"));

    await waitFor(() => expect(c.openScene).toHaveBeenCalledWith("s2"));
    await waitFor(() => expect(onSeek).toHaveBeenCalledWith(123));
  });
});
