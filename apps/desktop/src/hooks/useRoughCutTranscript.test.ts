import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useRoughCutTranscript } from "./useRoughCutTranscript";
import { type LauraClient } from "../api";

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
      useRoughCutTranscript(client, "t", segments as never));
    await waitFor(() => expect(result.current.words.length).toBe(2));
    expect(result.current.scenes).toHaveLength(1);
    expect(result.current.words.map((w) => w.id)).toEqual(["w1", "w2"]);
  });

  it("deleteRange calls deleteWords then reloads scenes", async () => {
    const client = makeClient();
    const { result } = renderHook(() =>
      useRoughCutTranscript(client, "t", segments as never));
    await waitFor(() => expect(result.current.words.length).toBe(2));
    await act(async () => { await result.current.deleteRange("w1", "w1"); });
    expect(client.deleteWords).toHaveBeenCalledWith("t", "w1", "w1");
    expect(client.listScenes).toHaveBeenCalled();
  });

  it("cutAt applies the returned clips and scenes", async () => {
    const client = makeClient();
    const { result } = renderHook(() =>
      useRoughCutTranscript(client, "t", segments as never));
    await waitFor(() => expect(result.current.scenes.length).toBe(1));
    await act(async () => { await result.current.cutAt(50); });
    expect(client.cutAtFrame).toHaveBeenCalledWith("t", 50);
  });
});
