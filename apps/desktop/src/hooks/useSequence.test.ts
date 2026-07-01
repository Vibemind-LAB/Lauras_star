// apps/desktop/src/hooks/useSequence.test.ts
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { type LauraClient, type Sequence } from "../api";
import { queryWrapper } from "../test-utils";
import { useSequence } from "./useSequence";

const SEQ: Sequence = { timeline_id: "seq", project_id: "p", items: [] };

function client(over: Partial<LauraClient>): LauraClient {
  return {
    getProjectSequence: vi.fn().mockResolvedValue(SEQ),
    setSequenceScenes: vi.fn().mockResolvedValue({
      ...SEQ,
      items: [{ id: "i1", scene_id: "s2", scene_name: "Szene 2", order_index: 0 }],
    }),
    ...over,
  } as unknown as LauraClient;
}

describe("useSequence", () => {
  it("loads the project sequence", async () => {
    const c = client({});
    const { result } = renderHook(() => useSequence(c, "p"), {
      wrapper: queryWrapper(),
    });
    await waitFor(() => expect(result.current.sequence?.timeline_id).toBe("seq"));
    expect(c.getProjectSequence).toHaveBeenCalledWith("p");
  });
  it("setScenes PUTs the new order", async () => {
    const c = client({});
    const { result } = renderHook(() => useSequence(c, "p"), {
      wrapper: queryWrapper(),
    });
    await waitFor(() => expect(result.current.sequence).toBeTruthy());
    await act(async () => {
      await result.current.setScenes(["s2"]);
    });
    expect(c.setSequenceScenes).toHaveBeenCalledWith("seq", ["s2"]);
    await waitFor(() => expect(result.current.sequence?.items[0]?.scene_id).toBe("s2"));
  });
});
