import { afterEach, describe, expect, it, vi } from "vitest";

import { type Asset, hasFile, LauraClient } from "./api";

const make = (kinds: string[]): Asset =>
  ({ files: kinds.map((k) => ({ kind: k })) }) as unknown as Asset;

describe("hasFile", () => {
  it("finds a present file kind", () => {
    expect(hasFile(make(["proxy", "waveform"]), "proxy")).toBe(true);
  });

  it("returns false for a missing kind", () => {
    expect(hasFile(make(["poster"]), "proxy")).toBe(false);
  });

  it("returns false when there are no files", () => {
    expect(hasFile(make([]), "proxy")).toBe(false);
  });
});

describe("LauraClient.startAnalysis", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("forwards the detector option in the POST body", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ analysis_run_id: "r1" }),
    } as unknown as Response);

    const client = new LauraClient("http://localhost:8765", "tok");
    await client.startAnalysis("asset-1", { detector: "histogram" });

    expect(fetchSpy).toHaveBeenCalledOnce();
    const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body["detector"]).toBe("histogram");
  });

  it("defaults to no detector key when option is omitted", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ analysis_run_id: "r2" }),
    } as unknown as Response);

    const client = new LauraClient("http://localhost:8765", "tok");
    await client.startAnalysis("asset-2");

    const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body["detector"]).toBeUndefined();
  });
});

describe("LauraClient.buildRoughCutFromShots", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("POSTs to the correct URL and returns dropped list", async () => {
    const mockResponse = {
      timeline: {
        id: "t",
        project_id: "p",
        name: "RC",
        kind: "rough_cut",
        created_at: "",
        clips: [],
      },
      dropped: [{ src_in_frame: 0, src_out_frame_exclusive: 9, drop_reason: "black" }],
    };

    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockResponse),
    } as unknown as Response);

    const client = new LauraClient("http://localhost:8765", "test-token");
    const result = await client.buildRoughCutFromShots("p", "a");

    expect(fetchSpy).toHaveBeenCalledOnce();
    const [calledUrl] = fetchSpy.mock.calls[0] as [string, ...unknown[]];
    expect(calledUrl).toBe("http://localhost:8765/projects/p/timelines/from-shots");
    expect(result.dropped[0].drop_reason).toBe("black");
  });
});
