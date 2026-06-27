// apps/desktop/src/hooks/useScenes.test.ts
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { type LauraClient, type Scene } from "../api";
import { queryWrapper } from "../test-utils";
import { useScenes } from "./useScenes";

const SCENE: Scene = {
  id: "s1", project_id: "p", source_timeline_id: "tl",
  name: "Szene 1", order_index: 0, seq_in_frame: 0, seq_out_frame_exclusive: 30,
};

function fakeClient(over: Partial<LauraClient>): LauraClient {
  return {
    listScenes: vi.fn().mockResolvedValue([]),
    generateScenes: vi.fn().mockResolvedValue([SCENE]),
    splitScene: vi.fn().mockResolvedValue([SCENE]),
    mergeScenes: vi.fn().mockResolvedValue([SCENE]),
    renameScene: vi.fn().mockResolvedValue(SCENE),
    ...over,
  } as unknown as LauraClient;
}

describe("useScenes", () => {
  it("loads scenes for a timeline on mount", async () => {
    const client = fakeClient({ listScenes: vi.fn().mockResolvedValue([SCENE]) });
    const { result } = renderHook(() => useScenes(client, "tl"), {
      wrapper: queryWrapper(),
    });
    await waitFor(() => expect(result.current.scenes.length).toBe(1));
    expect(result.current.scenes[0].name).toBe("Szene 1");
  });

  it("generate replaces scenes from the response", async () => {
    const client = fakeClient({});
    const { result } = renderHook(() => useScenes(client, "tl"), {
      wrapper: queryWrapper(),
    });
    await act(async () => { await result.current.generate("asset1"); });
    expect(result.current.scenes.length).toBe(1);
    expect(client.generateScenes).toHaveBeenCalledWith("tl", "asset1");
  });
});
