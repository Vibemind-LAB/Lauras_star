// apps/desktop/src/hooks/useSceneTimeline.test.ts
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { type LauraClient, type Timeline } from "../api";
import { useSceneTimeline } from "./useSceneTimeline";

const TL: Timeline = { id: "stl", project_id: "p", name: "Szene 1", kind: "scene", created_at: "", clips: [] };

function client(over: Partial<LauraClient>): LauraClient {
  return { openScene: vi.fn().mockResolvedValue(TL), deleteWords: vi.fn().mockResolvedValue(TL),
    ...over } as unknown as LauraClient;
}

describe("useSceneTimeline", () => {
  it("opens (materializes) the selected scene", async () => {
    const c = client({});
    const { result } = renderHook(() => useSceneTimeline(c, "scene1"));
    await waitFor(() => expect(result.current.timeline?.id).toBe("stl"));
    expect(c.openScene).toHaveBeenCalledWith("scene1");
  });

  it("deleteWords updates the timeline", async () => {
    const c = client({ deleteWords: vi.fn().mockResolvedValue({ ...TL, id: "stl2" }) });
    const { result } = renderHook(() => useSceneTimeline(c, "scene1"));
    await waitFor(() => expect(result.current.timeline).toBeTruthy());
    await act(async () => { await result.current.deleteWords("w0", "w1"); });
    expect(result.current.timeline?.id).toBe("stl2");
  });
});
