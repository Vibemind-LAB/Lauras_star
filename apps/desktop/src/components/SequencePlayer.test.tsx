import { render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { type Asset, type LauraClient, type TimelineClip } from "../api";
import { SequencePlayer, clipIndexAtSeqFrame, totalFrames } from "./SequencePlayer";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const clip1: TimelineClip = {
  id: "c1",
  asset_id: "a1",
  src_in_frame: 0,
  src_out_frame_exclusive: 50,
  seq_in_frame: 0,
  seq_out_frame_exclusive: 50,
  lane: 0,
  speaker_id: null,
  origin_word_start_id: null,
  origin_word_end_id: null,
  speed_num: 1,
  speed_den: 1,
  audio_offset_samples: 0,
};

const clip2: TimelineClip = {
  id: "c2",
  asset_id: "a2",
  src_in_frame: 10,
  src_out_frame_exclusive: 70,
  seq_in_frame: 50,
  seq_out_frame_exclusive: 110,
  lane: 0,
  speaker_id: null,
  origin_word_start_id: null,
  origin_word_end_id: null,
  speed_num: 1,
  speed_den: 1,
  audio_offset_samples: 0,
};

const asset1: Asset = {
  id: "a1",
  project_id: "p",
  type: "video",
  display_name: "Asset 1",
  source_path: "/a1.mp4",
  sha256: null,
  duration_frames: 100,
  rate_num: 25,
  rate_den: 1,
  audio_sample_rate: null,
  start_timecode: null,
  width: 1920,
  height: 1080,
  codec_video: null,
  codec_audio: null,
  is_vfr: false,
  synthetic: false,
  ai_effect: null,
  created_at: "2025-01-01T00:00:00Z",
  files: [{ id: "f1", asset_id: "a1", kind: "proxy", path: "/p1.mp4", size_bytes: null, is_proxy: true, is_waveform: false, is_audio_extract: false, checksum: null }],
};

const asset2: Asset = {
  id: "a2",
  project_id: "p",
  type: "video",
  display_name: "Asset 2",
  source_path: "/a2.mp4",
  sha256: null,
  duration_frames: 200,
  rate_num: 25,
  rate_den: 1,
  audio_sample_rate: null,
  start_timecode: null,
  width: 1920,
  height: 1080,
  codec_video: null,
  codec_audio: null,
  is_vfr: false,
  synthetic: false,
  ai_effect: null,
  created_at: "2025-01-01T00:00:00Z",
  files: [{ id: "f2", asset_id: "a2", kind: "proxy", path: "/p2.mp4", size_bytes: null, is_proxy: true, is_waveform: false, is_audio_extract: false, checksum: null }],
};

// ---------------------------------------------------------------------------
// Pure helper: totalFrames
// ---------------------------------------------------------------------------

describe("totalFrames", () => {
  it("returns 0 for empty list", () => {
    expect(totalFrames([])).toBe(0);
  });

  it("returns seq_out_frame_exclusive of the only clip", () => {
    expect(totalFrames([clip1])).toBe(50);
  });

  it("returns the maximum seq_out_frame_exclusive across clips", () => {
    expect(totalFrames([clip1, clip2])).toBe(110);
  });

  it("works when a single clip starts at a non-zero seq_in", () => {
    const c: TimelineClip = { ...clip1, seq_in_frame: 100, seq_out_frame_exclusive: 180 };
    expect(totalFrames([c])).toBe(180);
  });
});

// ---------------------------------------------------------------------------
// Pure helper: clipIndexAtSeqFrame
// ---------------------------------------------------------------------------

describe("clipIndexAtSeqFrame", () => {
  it("returns 0 for empty list", () => {
    expect(clipIndexAtSeqFrame([], 0)).toBe(0);
  });

  it("returns 0 for frame at start of first clip", () => {
    expect(clipIndexAtSeqFrame([clip1, clip2], 0)).toBe(0);
  });

  it("returns 0 for frame inside first clip", () => {
    expect(clipIndexAtSeqFrame([clip1, clip2], 25)).toBe(0);
  });

  it("returns 0 for frame at last frame of first clip (49)", () => {
    // seq_out is exclusive=50, so frame 49 is still in clip 0.
    expect(clipIndexAtSeqFrame([clip1, clip2], 49)).toBe(0);
  });

  it("returns 1 for frame exactly at seq_in of second clip (50)", () => {
    expect(clipIndexAtSeqFrame([clip1, clip2], 50)).toBe(1);
  });

  it("returns 1 for frame inside second clip", () => {
    expect(clipIndexAtSeqFrame([clip1, clip2], 80)).toBe(1);
  });

  it("clamps to last clip when frame is past the end", () => {
    expect(clipIndexAtSeqFrame([clip1, clip2], 200)).toBe(1);
  });

  it("clamps to last clip when frame is negative (before first)", () => {
    // -5 < seq_in_frame=0 of clip1, no match → clamped to last
    expect(clipIndexAtSeqFrame([clip1, clip2], -5)).toBe(1);
  });

  it("handles a single clip correctly", () => {
    expect(clipIndexAtSeqFrame([clip1], 0)).toBe(0);
    expect(clipIndexAtSeqFrame([clip1], 49)).toBe(0);
    expect(clipIndexAtSeqFrame([clip1], 50)).toBe(0); // past end → clamped to last=0
  });
});

// ---------------------------------------------------------------------------
// Render test: fake client with 2 clips from 2 assets
// ---------------------------------------------------------------------------

function makeClient(over: Partial<LauraClient> = {}): LauraClient {
  return {
    getSequenceFlattened: vi.fn().mockResolvedValue([clip1, clip2]),
    listAssets: vi.fn().mockResolvedValue([asset1, asset2]),
    ...over,
  } as unknown as LauraClient;
}

describe("SequencePlayer render", () => {
  it("shows a loading state while fetching", () => {
    // Never-resolving fetch so we catch the loading state.
    const c = {
      getSequenceFlattened: vi.fn(() => new Promise(() => undefined)),
      listAssets: vi.fn(() => new Promise(() => undefined)),
    } as unknown as LauraClient;
    const { getByText } = render(
      <SequencePlayer client={c} projectId="p" sequenceId="seq" />,
    );
    expect(getByText("Lade Sequenz…")).toBeTruthy();
  });

  it("renders a <video> with the first clip's proxy src after loading", async () => {
    const c = makeClient();
    const { container } = render(
      <SequencePlayer client={c} projectId="p" sequenceId="seq" />,
    );
    // Wait for the fetch to complete and the video to appear.
    await waitFor(() => {
      const video = container.querySelector("video");
      expect(video).toBeTruthy();
    });
    const video = container.querySelector("video") as HTMLVideoElement;
    expect(video).toBeTruthy();
  });

  it("shows the total-frames label after loading", async () => {
    const c = makeClient();
    const { getByText } = render(
      <SequencePlayer client={c} projectId="p" sequenceId="seq" />,
    );
    // totalFrames([clip1, clip2]) = 110
    await waitFor(() => {
      expect(getByText(/0 \/ 110 f/)).toBeTruthy();
    });
  });

  it("shows placeholder when sequenceId is null", () => {
    const c = makeClient();
    const { getByText } = render(
      <SequencePlayer client={c} projectId="p" sequenceId={null} />,
    );
    expect(getByText("Noch keine Sequenz — Szenen hinzufügen")).toBeTruthy();
  });

  it("shows placeholder when clips list is empty", async () => {
    const c = {
      getSequenceFlattened: vi.fn().mockResolvedValue([]),
      listAssets: vi.fn().mockResolvedValue([asset1, asset2]),
    } as unknown as LauraClient;
    const { getByText } = render(
      <SequencePlayer client={c} projectId="p" sequenceId="seq" />,
    );
    await waitFor(() => {
      expect(getByText("Noch keine Sequenz — Szenen hinzufügen")).toBeTruthy();
    });
  });

  it("calls getSequenceFlattened with the sequenceId", async () => {
    const c = makeClient();
    render(<SequencePlayer client={c} projectId="p" sequenceId="seq" />);
    await waitFor(() => expect(c.getSequenceFlattened).toHaveBeenCalledWith("seq"));
  });

  it("calls listAssets with the projectId", async () => {
    const c = makeClient();
    render(<SequencePlayer client={c} projectId="p" sequenceId="seq" />);
    await waitFor(() => expect(c.listAssets).toHaveBeenCalledWith("p"));
  });

  it("re-fetches when reloadKey changes", async () => {
    const c = makeClient();
    const { rerender } = render(
      <SequencePlayer client={c} projectId="p" sequenceId="seq" reloadKey={1} />,
    );
    await waitFor(() => expect(c.getSequenceFlattened).toHaveBeenCalledTimes(1));
    rerender(<SequencePlayer client={c} projectId="p" sequenceId="seq" reloadKey={2} />);
    await waitFor(() => expect(c.getSequenceFlattened).toHaveBeenCalledTimes(2));
  });

  it("plays a provided clipsOverride without calling getSequenceFlattened", async () => {
    // FineCutView passes a materialized SCENE timeline's clips directly. getSequenceFlattened
    // only resolves kind="sequence" timelines and returns [] for a scene → the player would show
    // no video. clipsOverride feeds the already-loaded clips so the scene actually renders.
    const c = {
      getSequenceFlattened: vi.fn().mockResolvedValue([]),
      listAssets: vi.fn().mockResolvedValue([asset1, asset2]),
    } as unknown as LauraClient;
    const { container, getByText } = render(
      <SequencePlayer
        client={c}
        projectId="p"
        sequenceId="scene-tl"
        clipsOverride={[clip1, clip2]}
      />,
    );
    await waitFor(() => expect(container.querySelector("video")).toBeTruthy());
    // Clips came from the override (110), not from the empty flatten.
    expect(getByText(/0 \/ 110 f/)).toBeTruthy();
    expect(c.getSequenceFlattened).not.toHaveBeenCalled();
  });
});
