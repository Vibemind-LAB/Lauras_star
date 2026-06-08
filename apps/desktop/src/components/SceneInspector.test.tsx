import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { type Asset, type LauraClient, type TimelineClip } from "../api";
import { SceneInspector } from "./SceneInspector";

// Frames and the waveform load lazily via never-resolving stubs so their effects
// don't update state during the test — we exercise the nudge→trim interaction only.
function stubClient(): { client: LauraClient; applyOperation: ReturnType<typeof vi.fn> } {
  const applyOperation = vi.fn().mockResolvedValue({});
  const client = {
    assetFrameUrl: () => new Promise<string>(() => undefined),
    getWaveform: () => new Promise(() => undefined),
    applyOperation,
  } as unknown as LauraClient;
  return { client, applyOperation };
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
    audio_offset_samples: 0,
    ...over,
  };
}

function asset(over: Partial<Asset> = {}): Asset {
  return {
    id: "a1",
    project_id: "p1",
    type: "video",
    display_name: "clip.mp4",
    source_path: "/x/clip.mp4",
    sha256: null,
    duration_frames: 5000,
    rate_num: 25,
    rate_den: 1,
    audio_sample_rate: 48000,
    start_timecode: null,
    width: 1920,
    height: 1080,
    codec_video: "h264",
    codec_audio: "aac",
    is_vfr: false,
    created_at: "",
    files: [],
    ...over,
  };
}

describe("SceneInspector", () => {
  it("applies a +1 frame trim to the IN point at the clip's sequence frame", () => {
    const { client, applyOperation } = stubClient();
    const c = clip();
    const { getByTitle } = render(
      <SceneInspector
        client={client}
        clip={c}
        asset={asset()}
        timelineId="t1"
        onChange={() => undefined}
        onSeek={() => undefined}
      />,
    );
    fireEvent.click(getByTitle("IN +1 Frame"));
    expect(applyOperation).toHaveBeenCalledWith("t1", {
      op: "trim",
      at_seq_frame: c.seq_in_frame,
      new_src_in_frame: c.src_in_frame + 1,
      new_src_out_frame_exclusive: c.src_out_frame_exclusive,
    });
  });
});
