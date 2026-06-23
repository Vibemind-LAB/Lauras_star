import { afterEach, describe, expect, it, vi } from "vitest";
import { LauraClient } from "./api";

afterEach(() => vi.restoreAllMocks());

function mockFetch(json: unknown) {
  const fn = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => json,
    text: async () => "",
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("history api", () => {
  it("undo POSTs and returns clips+scenes", async () => {
    const fn = mockFetch({ clips: [], scenes: [] });
    const c = new LauraClient("http://x", "tok");
    const r = await c.undo("tl1");
    expect(r).toEqual({ clips: [], scenes: [] });
    const [url, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://x/timelines/tl1/undo");
    expect(init.method).toBe("POST");
  });

  it("redo POSTs and returns clips+scenes", async () => {
    const fn = mockFetch({ clips: [], scenes: [] });
    const c = new LauraClient("http://x", "tok");
    const r = await c.redo("tl1");
    expect(r).toEqual({ clips: [], scenes: [] });
    const [url, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://x/timelines/tl1/redo");
    expect(init.method).toBe("POST");
  });

  it("getHistory GETs state", async () => {
    const state = { can_undo: true, can_redo: false, undo_label: "X", redo_label: null };
    const fn = mockFetch(state);
    const c = new LauraClient("http://x", "tok");
    const r = await c.getHistory("tl1");
    expect(r).toEqual(state);
    const [url] = fn.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://x/timelines/tl1/history");
  });
});
