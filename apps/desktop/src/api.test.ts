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

describe("import client methods", () => {
  it("importAssetFromUrl posts source_url", async () => {
    const fn = mockFetch({ asset_id: "a", job_id: "j", extra_asset_ids: [] });
    const c = new LauraClient("http://h", "tok");
    await c.importAssetFromUrl("p1", "https://x/y.mp4");
    expect(fn).toHaveBeenCalledWith(
      "http://h/projects/p1/assets/import",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ source_url: "https://x/y.mp4" }) }),
    );
  });

  it("importAssetFromUrl threads format + cookies into the body", async () => {
    const fn = mockFetch({ asset_id: "a", job_id: "j", extra_asset_ids: [] });
    const c = new LauraClient("http://h", "tok");
    await c.importAssetFromUrl("p1", "https://youtu.be/x", {
      format: "1080",
      cookiesFromBrowser: "chrome",
    });
    const [, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({
      source_url: "https://youtu.be/x",
      format: "1080",
      cookies_from_browser: "chrome",
    });
  });

  it("importAssetFromUrl returns extra_asset_ids for a playlist fan-out", async () => {
    mockFetch({ asset_id: "a", job_id: "j", extra_asset_ids: ["b", "c"] });
    const c = new LauraClient("http://h", "tok");
    const accepted = await c.importAssetFromUrl("p1", "https://yt/playlist");
    expect([accepted.asset_id, ...accepted.extra_asset_ids]).toEqual(["a", "b", "c"]);
  });

  it("getImportStatus GETs the status", async () => {
    const fn = mockFetch({ phase: "downloading", downloaded_bytes: 1, total_bytes: 2 });
    const c = new LauraClient("http://h", "tok");
    const st = await c.getImportStatus("a1");
    expect(fn).toHaveBeenCalledWith("http://h/assets/a1/import-status", expect.anything());
    expect(st.phase).toBe("downloading");
  });

  it("retryImport posts import-retry", async () => {
    const fn = mockFetch({ asset_id: "a1", job_id: "j" });
    const c = new LauraClient("http://h", "tok");
    await c.retryImport("a1");
    expect(fn).toHaveBeenCalledWith(
      "http://h/assets/a1/import-retry",
      expect.objectContaining({ method: "POST" }),
    );
  });
});

describe("LauraClient.startAnalysis", () => {
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

  it("threads cut_bias into the body and omits it when not given", async () => {
    const withBias = mockFetch({ timeline: {}, dropped: [], split_cuts: [], quality: null });
    const c = new LauraClient("http://h", "tok");
    await c.buildRoughCutFromShots("p", "a", "tl", { cutBias: 0.7 });
    const [, init] = withBias.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({
      asset_id: "a",
      timeline_id: "tl",
      cut_bias: 0.7,
    });

    const noBias = mockFetch({ timeline: {}, dropped: [], split_cuts: [], quality: null });
    await c.buildRoughCutFromShots("p", "a");
    const [, init2] = noBias.mock.calls[0] as [string, RequestInit];
    const body2 = JSON.parse(init2.body as string) as Record<string, unknown>;
    expect("cut_bias" in body2).toBe(false);
  });

  it("returns the quality summary and split_cuts from the response", async () => {
    mockFetch({
      timeline: { id: "t", project_id: "p", name: "RC", kind: "rough_cut", created_at: "", clips: [] },
      dropped: [],
      split_cuts: [{ seq_cut: 50, video_frame: 50, audio_frame: 53, offset: 3, kind: "L" }],
      quality: {
        overall: 0.8,
        visual_exactness: 0.9,
        editorial_cleanliness: 0.6,
        n_cuts: 3,
        n_split_cuts: 1,
      },
    });
    const c = new LauraClient("http://h", "tok");
    const res = await c.buildRoughCutFromShots("p", "a");
    expect(res.quality?.overall).toBe(0.8);
    expect(res.split_cuts[0].kind).toBe("L");
  });
});
