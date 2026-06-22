import { describe, expect, it } from "vitest";
import type { TimelineAudioClip } from "../api";
import { AudioMixer, type MixerAudioEl } from "./AudioMixer";

class FakeEl implements MixerAudioEl {
  currentTime = 0;
  volume = 1;
  paused = true;
  src: string;
  playCount = 0;
  pauseCount = 0;
  loadCount = 0;
  constructor(src: string) {
    this.src = src;
  }
  play(): Promise<void> {
    this.paused = false;
    this.playCount += 1;
    return Promise.resolve();
  }
  pause(): void {
    this.paused = true;
    this.pauseCount += 1;
  }
  load(): void {
    this.loadCount += 1;
  }
}

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
    ducking_percent: 30,
    label: null,
    created_at: "",
    ...over,
  };
}

describe("AudioMixer sync", () => {
  function makeMixer(): { mixer: AudioMixer; els: Map<string, FakeEl> } {
    const els = new Map<string, FakeEl>();
    const mixer = new AudioMixer({
      rateNum: 30,
      rateDen: 1,
      makeEl: (src) => {
        const el = new FakeEl(src);
        els.set(src, el);
        return el;
      },
    });
    return { mixer, els };
  }

  it("creates one audio element per clip pointing at the asset proxy", () => {
    const { mixer, els } = makeMixer();
    mixer.setClips([clip({ id: "a1", asset_id: "vo1" })]);
    expect(els.has("laura-media://media/vo1/proxy")).toBe(true);
  });

  it("plays a clip only while inside its span and seeks to source time", () => {
    const { mixer, els } = makeMixer();
    mixer.setClips([clip()]);
    const el = els.get("laura-media://media/vo1/proxy")!;

    mixer.syncTo(10, true); // before span
    expect(el.paused).toBe(true);

    mixer.syncTo(60, true); // mid span (frame 60 -> src 1.0s)
    expect(el.paused).toBe(false);
    expect(el.currentTime).toBeCloseTo(1, 2);
    expect(el.volume).toBeCloseTo(1, 6);

    mixer.syncTo(95, true); // past span
    expect(el.paused).toBe(true);
  });

  it("re-seeks only when drift exceeds the threshold (avoids stutter)", () => {
    const { mixer, els } = makeMixer();
    mixer.setClips([clip()]);
    const el = els.get("laura-media://media/vo1/proxy")!;
    mixer.syncTo(60, true);
    el.currentTime = 1.02; // tiny drift, within threshold
    mixer.syncTo(60, true);
    expect(el.currentTime).toBeCloseTo(1.02, 3); // not re-seeked
    el.currentTime = 5; // large drift
    mixer.syncTo(60, true);
    expect(el.currentTime).toBeCloseTo(1, 2); // re-seeked back
  });

  it("pauseAll pauses every element", () => {
    const { mixer, els } = makeMixer();
    mixer.setClips([clip()]);
    mixer.syncTo(60, true);
    mixer.pauseAll();
    expect(els.get("laura-media://media/vo1/proxy")!.paused).toBe(true);
  });
});
