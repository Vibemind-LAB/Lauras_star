import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { type Asset, type LauraClient, type Scene, type Timeline, type TimelineClip } from "../api";
import { FineCutView } from "./FineCutView";

vi.mock("./SequencePlayer", () => ({ SequencePlayer: () => <div data-testid="player" /> }));
vi.mock("./TimelineBar", () => ({ TimelineBar: () => <div data-testid="timeline" /> }));
vi.mock("./ContinuousTranscript", () => ({
  ContinuousTranscript: (p: { onDeleteSelection?: (a: string, b: string) => void }) => (
    <button type="button" onClick={() => p.onDeleteSelection?.("w0", "w1")}>
      cut-word
    </button>
  ),
}));

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const asset: Asset = {
  id: "a",
  rate_num: 30,
  rate_den: 1,
  display_name: "a",
} as unknown as Asset;

const baseClip: TimelineClip = {
  id: "c1",
  asset_id: "a",
  src_in_frame: 0,
  src_out_frame_exclusive: 30,
  seq_in_frame: 0,
  seq_out_frame_exclusive: 30,
  lane: 0,
  role: "base",
  speaker_id: null,
  origin_word_start_id: null,
  origin_word_end_id: null,
  speed_num: 1,
  speed_den: 1,
  audio_offset_samples: 0,
};

const sceneA: Scene = {
  id: "sA",
  project_id: "p",
  source_timeline_id: "rc1",
  name: "Szene 1",
  order_index: 0,
  seq_in_frame: 0,
  seq_out_frame_exclusive: 30,
  scene_timeline_id: null,
  music_asset_id: null,
  music_gain_percent: 100,
};

const sceneB: Scene = {
  id: "sB",
  project_id: "p",
  source_timeline_id: "rc1",
  name: "Szene 2",
  order_index: 1,
  seq_in_frame: 30,
  seq_out_frame_exclusive: 60,
  scene_timeline_id: null,
  music_asset_id: null,
  music_gain_percent: 100,
};

const segments = [] as never[];

const rcTimeline: Timeline = {
  id: "rc1",
  project_id: "p",
  name: "Rough Cut",
  kind: "rough_cut",
  created_at: "",
  clips: [baseClip],
};

function makeClient(over: Partial<LauraClient> = {}): LauraClient {
  return {
    getTimeline: vi.fn().mockResolvedValue(rcTimeline),
    listScenes: vi.fn().mockResolvedValue([sceneA, sceneB]),
    deleteWords: vi.fn().mockResolvedValue(rcTimeline),
    cutAtFrame: vi.fn().mockResolvedValue({ clips: [baseClip], scenes: [sceneA] }),
    getTranscript: vi.fn().mockResolvedValue([]),
    openScene: vi.fn(), // must NOT be called in the new edit path
    ...over,
  } as unknown as LauraClient;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("FineCutView", () => {
  it("loads the rough-cut timeline directly (no openScene) and renders scene jump buttons", async () => {
    const getTimeline = vi.fn().mockResolvedValue({ ...rcTimeline, clips: [baseClip] });
    const listScenes = vi.fn().mockResolvedValue([sceneA, sceneB]);
    const openScene = vi.fn();
    const c = makeClient({ getTimeline, listScenes, openScene });

    render(
      <FineCutView
        client={c}
        asset={asset}
        roughCutId="rc1"
        segments={segments}
        currentFrame={0}
        seek={null}
        onSeek={vi.fn()}
        onFrame={vi.fn()}
      />,
    );

    await screen.findByText("Szene 1");
    expect(getTimeline).toHaveBeenCalledWith("rc1");
    expect(openScene).not.toHaveBeenCalled();
  });

  it("clicking a scene in the jump list seeks to its seq_in_frame", async () => {
    const onSeek = vi.fn();
    const c = makeClient({
      getTimeline: vi.fn().mockResolvedValue({ ...rcTimeline, clips: [baseClip] }),
      listScenes: vi.fn().mockResolvedValue([sceneA, sceneB]),
    });

    render(
      <FineCutView
        client={c}
        asset={asset}
        roughCutId="rc1"
        segments={segments}
        currentFrame={0}
        seek={null}
        onSeek={onSeek}
        onFrame={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Szene 2" }));
    expect(onSeek).toHaveBeenCalledWith(sceneB.seq_in_frame);
    // The seek object emitted by onSeek is forwarded to App.tsx which converts it
    // to a new { frame } object (setSeek) that is then passed back as `seek` prop
    // → SequencePlayer seekTo={seek} triggers the seek effect.
    // Full player-seek (video scrub) is manuell zu prüfen (live CDP 9222) — jsdom cannot
    // assert HTMLVideoElement.currentTime changes.
  });

  it("routes a transcript delete selection to deleteWords on the rough-cut", async () => {
    const deleteWords = vi.fn().mockResolvedValue(rcTimeline);
    const c = makeClient({ deleteWords });

    render(
      <FineCutView
        client={c}
        asset={asset}
        roughCutId="rc1"
        segments={segments}
        currentFrame={0}
        seek={null}
        onSeek={vi.fn()}
        onFrame={vi.fn()}
      />,
    );

    // Wait for the component to finish loading so ContinuousTranscript is present.
    await screen.findByText("Szene 1");
    fireEvent.click(screen.getByText("cut-word"));

    // deleteWords should be called on the rough-cut id with the word range from the mock.
    await vi.waitFor(() => expect(deleteWords).toHaveBeenCalledWith("rc1", "w0", "w1"));
  });

  it("shows the empty-state message when roughCutId is null", () => {
    const c = makeClient();
    const { getByText } = render(
      <FineCutView
        client={c}
        asset={asset}
        roughCutId={null}
        segments={segments}
        currentFrame={0}
        seek={null}
        onSeek={vi.fn()}
        onFrame={vi.fn()}
      />,
    );
    expect(getByText(/Noch keine Szenen/)).toBeTruthy();
  });
});
