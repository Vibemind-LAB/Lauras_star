import { describe, expect, it } from "vitest";
import type { TimelineAudioClip } from "../api";
import {
  clipActiveAt,
  clipGainAt,
  clipSourceTimeSeconds,
  seqFrameToSeconds,
  videoDuckGainAt,
} from "./audioMix";

function clip(over: Partial<TimelineAudioClip> = {}): TimelineAudioClip {
  return {
    id: "a1",
    timeline_id: "t1",
    asset_id: "vo1",
    seq_in_frame: 30,
    seq_out_frame_exclusive: 90,
    asset_in_frame: 0,
    gain_percent: 100,
    fade_in_frames: 0,
    fade_out_frames: 0,
    mix_mode: "mix",
    ducking_percent: 100,
    label: null,
    created_at: "",
    ...over,
  };
}

describe("audioMix mapping (mirrors mp4.py export semantics)", () => {
  it("converts seq frames to seconds at 30/1", () => {
    expect(seqFrameToSeconds(30, 30, 1)).toBeCloseTo(1, 6);
    expect(seqFrameToSeconds(0, 30, 1)).toBe(0);
  });

  it("clipActiveAt is end-exclusive", () => {
    expect(clipActiveAt(clip(), 29)).toBe(false);
    expect(clipActiveAt(clip(), 30)).toBe(true);
    expect(clipActiveAt(clip(), 89)).toBe(true);
    expect(clipActiveAt(clip(), 90)).toBe(false);
  });

  it("source time accounts for asset_in_frame and intra-clip offset", () => {
    // asset_in 15, seq_in 30, query frame 45 -> src frame 30 -> 1.0s @30fps
    expect(clipSourceTimeSeconds(clip({ asset_in_frame: 15 }), 45, 30, 1)).toBeCloseTo(1, 6);
  });

  it("gain applies gain_percent and linear fades", () => {
    expect(clipGainAt(clip({ gain_percent: 50 }), 60, 30, 1)).toBeCloseTo(0.5, 6);
    // fade_in 30 frames from seq_in 30: at frame 45 (halfway) -> 0.5 * gain(1.0)
    expect(clipGainAt(clip({ fade_in_frames: 30 }), 45, 30, 1)).toBeCloseTo(0.5, 6);
    // fade_out 30 frames ending at 90: at frame 75 (halfway through fade) -> 0.5
    expect(clipGainAt(clip({ fade_out_frames: 30 }), 75, 30, 1)).toBeCloseTo(0.5, 6);
    expect(clipGainAt(clip(), 10, 30, 1)).toBe(0); // outside span
  });

  it("video duck gain mirrors export ducking (replace/mute -> 0)", () => {
    expect(videoDuckGainAt([clip({ mix_mode: "mix", ducking_percent: 30 })], 60)).toBeCloseTo(0.3, 6);
    expect(videoDuckGainAt([clip({ mix_mode: "replace_original" })], 60)).toBe(0);
    expect(videoDuckGainAt([clip({ mix_mode: "mute_original" })], 60)).toBe(0);
    expect(videoDuckGainAt([clip()], 200)).toBe(1); // none active
    expect(videoDuckGainAt([], 60)).toBe(1);
  });
});
