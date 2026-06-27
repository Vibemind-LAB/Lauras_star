import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useRoughCutTranscript } from "./useRoughCutTranscript";
import { type LauraClient } from "../api";
import { queryWrapper } from "../test-utils";

const clip = {
  id: "c1", asset_id: "a1", src_in_frame: 0, src_out_frame_exclusive: 100,
  seq_in_frame: 0, seq_out_frame_exclusive: 100, lane: 0,
  speaker_id: null, origin_word_start_id: null, origin_word_end_id: null,
};
const scenes = [
  { id: "s1", project_id: "p", source_timeline_id: "t", name: "s1",
    order_index: 0, seq_in_frame: 0, seq_out_frame_exclusive: 100 },
];
const segments = [
  { id: "seg1", asset_id: "a1", speaker_id: null, start_frame: 0, end_frame: 100,
    text: "hi there", words: [
      { id: "w1", idx: 0, start_frame: 0, end_frame: 10, text: "hi", is_punctuation: false },
      { id: "w2", idx: 1, start_frame: 20, end_frame: 30, text: "there", is_punctuation: false },
    ] },
];

function makeClient(overrides: Partial<LauraClient> = {}): LauraClient {
  return {
    getTimeline: vi.fn().mockResolvedValue({ id: "t", clips: [clip] }),
    listScenes: vi.fn().mockResolvedValue(scenes),
    deleteWords: vi.fn().mockResolvedValue({ id: "t", clips: [clip] }),
    cutAtFrame: vi.fn().mockResolvedValue({ clips: [clip], scenes }),
    ...overrides,
  } as unknown as LauraClient;
}

describe("useRoughCutTranscript", () => {
  it("loads rough-cut clips, scenes, and projects words", async () => {
    const client = makeClient();
    const { result } = renderHook(() =>
      useRoughCutTranscript(client, "t", segments as never),
      { wrapper: queryWrapper() });
    await waitFor(() => expect(result.current.words.length).toBe(2));
    expect(result.current.scenes).toHaveLength(1);
    expect(result.current.words.map((w) => w.id)).toEqual(["w1", "w2"]);
  });

  it("deleteRange calls deleteWords then reloads scenes", async () => {
    const client = makeClient();
    const { result } = renderHook(() =>
      useRoughCutTranscript(client, "t", segments as never),
      { wrapper: queryWrapper() });
    await waitFor(() => expect(result.current.words.length).toBe(2));
    await act(async () => { await result.current.deleteRange("w1", "w1"); });
    expect(client.deleteWords).toHaveBeenCalledWith("t", "w1", "w1");
    await waitFor(() => expect(client.listScenes).toHaveBeenCalled());
  });

  it("cutAt applies the returned clips and scenes", async () => {
    const client = makeClient();
    const { result } = renderHook(() =>
      useRoughCutTranscript(client, "t", segments as never),
      { wrapper: queryWrapper() });
    await waitFor(() => expect(result.current.scenes.length).toBe(1));
    await act(async () => { await result.current.cutAt(50); });
    expect(client.cutAtFrame).toHaveBeenCalledWith("t", 50);
  });
});

/**
 * Helper: render the hook, wait for the initial load (real timers), then switch to
 * fake timers for debounce testing. Returns the hook result + a flush helper.
 */
async function renderAndLoad(client: LauraClient) {
  const rendered = renderHook(() =>
    useRoughCutTranscript(client, "t", segments as never),
    { wrapper: queryWrapper() });
  // Wait for initial load with real timers.
  await waitFor(() => expect(rendered.result.current.words.length).toBe(2));
  // Switch to fake timers AFTER the initial async setup completes.
  vi.useFakeTimers();
  return rendered;
}

afterEach(() => { vi.useRealTimers(); });

describe("useRoughCutTranscript — replaceSpanText", () => {
  it("calls createVoiceover with span frames, mix_mode=replace_original, ducking 0, voiceId", async () => {
    const createVoiceover = vi.fn().mockResolvedValue({ job_id: "j1" });
    const client = makeClient({ createVoiceover } as Partial<LauraClient>);
    const { result } = await renderAndLoad(client);

    act(() => { result.current.replaceSpanText("w1", "w2", "new text", "Hedda"); });
    // Flush the debounce timer + all resulting promise microtasks.
    await act(async () => { await vi.runAllTimersAsync(); });

    // w1: seqStart=0, seqEnd=10; w2: seqStart=20, seqEnd=30 → span [0, 30)
    expect(createVoiceover).toHaveBeenCalledWith("t", expect.objectContaining({
      text: "new text",
      seqIn: 0,
      seqOut: 30,
      mixMode: "replace_original",
      duckingPercent: 0,
      voiceId: "Hedda",
    }));
  });

  it("debounce coalesces rapid edits: only the last call reaches createVoiceover", async () => {
    const createVoiceover = vi.fn().mockResolvedValue({ job_id: "j2" });
    const client = makeClient({ createVoiceover } as Partial<LauraClient>);
    const { result } = await renderAndLoad(client);

    act(() => {
      result.current.replaceSpanText("w1", "w2", "first", "Hedda");
      result.current.replaceSpanText("w1", "w2", "second", "Hedda");
      result.current.replaceSpanText("w1", "w2", "third", "Hedda");
    });
    await act(async () => { await vi.runAllTimersAsync(); });

    expect(createVoiceover).toHaveBeenCalledTimes(1);
    expect(createVoiceover).toHaveBeenCalledWith("t", expect.objectContaining({ text: "third" }));
  });

  it("reload (getTimeline) is called after createVoiceover resolves", async () => {
    const createVoiceover = vi.fn().mockResolvedValue({ job_id: "j3" });
    const client = makeClient({ createVoiceover } as Partial<LauraClient>);
    const { result } = await renderAndLoad(client);

    const callsBefore = (client.getTimeline as ReturnType<typeof vi.fn>).mock.calls.length;

    act(() => { result.current.replaceSpanText("w1", "w2", "reload test", "Hedda"); });
    await act(async () => { await vi.runAllTimersAsync(); });

    // reload() calls getTimeline; count must have increased by at least 1.
    expect((client.getTimeline as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(callsBefore);
  });
});
