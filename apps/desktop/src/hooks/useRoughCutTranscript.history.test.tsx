import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useRoughCutTranscript } from "./useRoughCutTranscript";
import { type LauraClient } from "../api";

afterEach(() => vi.restoreAllMocks());

const clip = {
  id: "c1", asset_id: "a1", src_in_frame: 0, src_out_frame_exclusive: 30,
  seq_in_frame: 0, seq_out_frame_exclusive: 30, lane: 0,
  speaker_id: null, origin_word_start_id: null, origin_word_end_id: null,
  speed_num: 1, speed_den: 1, audio_offset_samples: 0, role: "base",
};

const segments = [] as never[];

function makeClient(overrides: Partial<LauraClient> = {}): LauraClient {
  return {
    getTimeline: vi.fn().mockResolvedValue({ id: "t", clips: [clip] }),
    listScenes: vi.fn().mockResolvedValue([]),
    deleteWords: vi.fn().mockResolvedValue({ id: "t", clips: [clip] }),
    cutAtFrame: vi.fn().mockResolvedValue({ clips: [clip], scenes: [] }),
    getHistory: vi.fn().mockResolvedValue({ can_undo: false, can_redo: false, undo_label: null, redo_label: null }),
    undo: vi.fn().mockResolvedValue({ clips: [clip], scenes: [] }),
    redo: vi.fn().mockResolvedValue({ clips: [clip], scenes: [] }),
    ...overrides,
  } as unknown as LauraClient;
}

describe("useRoughCutTranscript history", () => {
  it("canUndo becomes true after reload when getHistory returns can_undo:true", async () => {
    const client = makeClient({
      getHistory: vi.fn().mockResolvedValue({
        can_undo: true,
        can_redo: false,
        undo_label: "delete",
        redo_label: null,
      }),
    } as Partial<LauraClient>);

    const { result } = renderHook(() =>
      useRoughCutTranscript(client, "t", segments),
    );

    await waitFor(() => expect(result.current.canUndo).toBe(true));
    expect(result.current.undoLabel).toBe("delete");
    expect(result.current.canRedo).toBe(false);
  });

  it("undo() calls client.undo then reloads clips + refreshes history", async () => {
    const undoFn = vi.fn().mockResolvedValue({ clips: [clip], scenes: [] });
    const getHistoryFn = vi.fn().mockResolvedValue({
      can_undo: false, can_redo: true, undo_label: null, redo_label: "re-apply",
    });
    const client = makeClient({
      undo: undoFn,
      getHistory: getHistoryFn,
    } as Partial<LauraClient>);

    const { result } = renderHook(() =>
      useRoughCutTranscript(client, "t", segments),
    );

    // Wait for initial load
    await waitFor(() => expect(result.current.clips).toHaveLength(1));

    await act(async () => {
      await result.current.undo();
    });

    expect(undoFn).toHaveBeenCalledWith("t");
    // getHistory called at least twice: once on initial load, once after undo
    expect(getHistoryFn.mock.calls.length).toBeGreaterThan(1);
  });

  it("redo() calls client.redo then reloads", async () => {
    const redoFn = vi.fn().mockResolvedValue({ clips: [clip], scenes: [] });
    const client = makeClient({ redo: redoFn } as Partial<LauraClient>);

    const { result } = renderHook(() =>
      useRoughCutTranscript(client, "t", segments),
    );

    await waitFor(() => expect(result.current.clips).toHaveLength(1));

    await act(async () => {
      await result.current.redo();
    });

    expect(redoFn).toHaveBeenCalledWith("t");
  });
});
