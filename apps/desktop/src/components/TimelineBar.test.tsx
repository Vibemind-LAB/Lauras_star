import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  type Asset,
  type LauraClient,
  type Timeline,
  type TimelineAudioClip,
  type TimelineClip,
} from "../api";
import { TimelineBar } from "./TimelineBar";

// Auto-cleanup is not wired (no globals/setup file), so unmount between tests
// explicitly — otherwise leftover clip nodes make title queries ambiguous.
afterEach(cleanup);

// Thumbnails load lazily in ClipThumb; a never-resolving stub keeps the effect from
// updating state during these tests (we exercise interaction, not thumbnail loading).
function stubClient(over: Partial<LauraClient> = {}): LauraClient {
  return {
    assetFrameUrl: () => new Promise<string>(() => undefined),
    getAsset: () => new Promise(() => undefined),
    ...over,
  } as unknown as LauraClient;
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

function timeline(clips: TimelineClip[]): Timeline {
  return { id: "t1", project_id: "p1", name: "Rough Cut", kind: "rough_cut", created_at: "", clips };
}

function audioClip(over: Partial<TimelineAudioClip> = {}): TimelineAudioClip {
  return {
    id: "ac1",
    timeline_id: "t1",
    asset_id: "a1",
    seq_in_frame: 10,
    seq_out_frame_exclusive: 50,
    asset_in_frame: 0,
    gain_percent: 80,
    fade_in_frames: 3,
    fade_out_frames: 4,
    mix_mode: "mix",
    ducking_percent: 100,
    label: "VO",
    created_at: "",
    ...over,
  };
}

describe("TimelineBar", () => {
  it("prompts to pick a project when there is no timeline", () => {
    const { container } = render(
      <TimelineBar client={stubClient()} timeline={null} onChange={() => undefined} />,
    );
    expect(container.textContent ?? "").toContain("wähle ein Projekt");
  });

  it("scrubs the player to a clip's source IN frame when its thumbnail is clicked", () => {
    const onScrub = vi.fn();
    const { getByTitle } = render(
      <TimelineBar
        client={stubClient()}
        timeline={timeline([clip({ asset_id: "vid-42", src_in_frame: 1518 })])}
        onChange={() => undefined}
        onScrub={onScrub}
      />,
    );
    fireEvent.click(getByTitle(/^Clip 1/));
    expect(onScrub).toHaveBeenCalledWith("vid-42", 1518);
  });

  it("reorders clips via drag-and-drop with op:move (drop-before semantics)", () => {
    const a = clip({ id: "A", seq_in_frame: 0, seq_out_frame_exclusive: 31 });
    const b = clip({ id: "B", seq_in_frame: 31, seq_out_frame_exclusive: 62 });
    const tl = timeline([a, b]);
    const applyOperation = vi.fn(() => Promise.resolve(tl));
    const { getByTitle } = render(
      <TimelineBar
        client={stubClient({ applyOperation })}
        timeline={tl}
        onChange={() => undefined}
      />,
    );
    // Drag clip A (seq_in 0) and drop it onto clip B (seq_in 31): move A before B.
    fireEvent.dragStart(getByTitle(/^Clip 1/));
    fireEvent.drop(getByTitle(/^Clip 2/));
    expect(applyOperation).toHaveBeenCalledWith("t1", {
      op: "move",
      at_seq_frame: 0,
      to_seq_frame: 31,
    });
  });

  // --- A1 audio lane / set_audio_offset (m3) ---------------------------------------------------

  // The audio rate the component reads (via getAsset) to project samples<->frames: 48 kHz @ 30 fps
  // => 1600 samples per frame, so a 5-frame L-cut is 8000 samples.
  const AUDIO_ASSET = {
    audio_sample_rate: 48000,
    rate_num: 30,
    rate_den: 1,
    duration_frames: null,
  } as unknown as Asset;

  function twoClipTimeline(over1: Partial<TimelineClip> = {}): Timeline {
    const a = clip({ id: "A", seq_in_frame: 0, seq_out_frame_exclusive: 30, ...over1 });
    const b = clip({ id: "B", seq_in_frame: 30, seq_out_frame_exclusive: 60, src_in_frame: 200 });
    return timeline([a, b]);
  }

  // Give the strip a measurable width so a pixel delta maps to a frame delta. With total=60 frames
  // over 600px, framesPerPx = 0.1, so a +50px drag is +5 frames.
  function stubStripWidth(): void {
    vi.spyOn(HTMLDivElement.prototype, "getBoundingClientRect").mockReturnValue({
      width: 600,
      height: 12,
      top: 0,
      left: 0,
      right: 600,
      bottom: 12,
      x: 0,
      y: 0,
      toJSON: () => undefined,
    } as DOMRect);
  }

  it("dragging a non-first clip's audio handle issues set_audio_offset (frames)", () => {
    stubStripWidth();
    const tl = twoClipTimeline();
    const applyOperation = vi.fn(() => Promise.resolve(tl));
    const getAsset = vi.fn(() => Promise.resolve(AUDIO_ASSET));
    const { getByLabelText } = render(
      <TimelineBar
        client={stubClient({ applyOperation, getAsset }) as unknown as LauraClient}
        timeline={tl}
        onChange={() => undefined}
      />,
    );
    const handle = getByLabelText("Ton-Versatz Clip 2");
    // +50px at 0.1 frames/px = +5 frames (an L-cut). Element.setPointerCapture is absent in jsdom.
    handle.setPointerCapture = () => undefined;
    fireEvent.pointerDown(handle, { clientX: 100, pointerId: 1 });
    fireEvent.pointerMove(handle, { clientX: 150, pointerId: 1 });
    fireEvent.pointerUp(handle, { clientX: 150, pointerId: 1 });
    expect(applyOperation).toHaveBeenCalledWith("t1", {
      op: "set_audio_offset",
      at_seq_frame: 30,
      audio_offset_frames: 5,
    });
  });

  it("does not render an audio handle for the first clip (head is a hard cut)", () => {
    stubStripWidth();
    const { queryByLabelText } = render(
      <TimelineBar
        client={stubClient({ getAsset: () => Promise.resolve(AUDIO_ASSET) }) as unknown as LauraClient}
        timeline={twoClipTimeline()}
        onChange={() => undefined}
      />,
    );
    expect(queryByLabelText("Ton-Versatz Clip 1")).toBeNull();
    expect(queryByLabelText("Ton-Versatz Clip 2")).not.toBeNull();
  });

  it("renders the A1 lane offset for an accepted (non-zero) split", async () => {
    stubStripWidth();
    // B carries an accepted L-cut: 8000 samples = +5 frames at 48k/30. Its A1 block must label it.
    const tl = twoClipTimeline();
    tl.clips[1] = { ...tl.clips[1], audio_offset_samples: 8000 };
    const { findByLabelText } = render(
      <TimelineBar
        client={stubClient({ getAsset: () => Promise.resolve(AUDIO_ASSET) }) as unknown as LauraClient}
        timeline={tl}
        onChange={() => undefined}
      />,
    );
    // The audio block's accessible label reflects the projected offset (L-cut at +5 frames).
    const block = await findByLabelText(/Audio Clip 2 · Ton \+5f → L-Cut/);
    expect(block).not.toBeNull();
  });

  it("renders timeline audio clips on an A2 lane", () => {
    const { getByLabelText } = render(
      <TimelineBar
        client={stubClient()}
        timeline={twoClipTimeline()}
        audioClips={[audioClip()]}
        onChange={() => undefined}
      />,
    );
    expect(getByLabelText("Audio-Lane A2")).not.toBeNull();
    expect(getByLabelText("A2 Clip VO · seq 10–50")).not.toBeNull();
  });
});
