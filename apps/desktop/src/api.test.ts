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
    const fn = mockFetch({ asset_id: "a", job_id: "j" });
    const c = new LauraClient("http://h", "tok");
    await c.importAssetFromUrl("p1", "https://x/y.mp4");
    expect(fn).toHaveBeenCalledWith(
      "http://h/projects/p1/assets/import",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ source_url: "https://x/y.mp4" }) }),
    );
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
