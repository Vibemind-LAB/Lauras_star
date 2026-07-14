import { afterEach, describe, expect, it, vi } from "vitest";

import { LauraClient, type ProductionStatus } from "./api";

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

function mockFetchError(status: number, text: string) {
  const fn = vi.fn().mockResolvedValue({
    ok: false,
    status,
    json: async () => ({}),
    text: async () => text,
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.restoreAllMocks());

const status: ProductionStatus = {
  meta: {
    session_id: "s1",
    asset_id: "a1",
    created_utc: "2026-07-13T00:00:00Z",
    task: "make a reel",
    format: "insta",
    target_seconds: 60,
    status: "active",
  },
  scene_reviews: { count: 2, scenes: [1, 2] },
  artifacts: {
    storyline: { version: 1, archived_versions: [] },
    script: { version: 1, archived_versions: [] },
    voice: { version: null, archived_versions: [] },
    cutlist: { version: null, archived_versions: [] },
    render_report: { version: null, archived_versions: [] },
    qa_report: { version: null, archived_versions: [] },
  },
  resume_point: "voice",
};

describe("production session client methods", () => {
  it("createProduction POSTs task+target_seconds with the token header and maps the response", async () => {
    const fn = mockFetch({ session_id: "s1", job_id: "j1" });
    const c = new LauraClient("http://h", "tok");
    const out = await c.createProduction("a1", "make a reel", 45);
    expect(out).toEqual({ session_id: "s1", job_id: "j1" });
    const [url, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://h/assets/a1/production");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["X-Laura-Token"]).toBe("tok");
    expect(JSON.parse(init.body as string)).toEqual({ task: "make a reel", target_seconds: 45 });
  });

  it("createProduction omits target_seconds when not given (backend default applies)", async () => {
    const fn = mockFetch({ session_id: "s1", job_id: "j1" });
    const c = new LauraClient("http://h", "tok");
    await c.createProduction("a1", "make a reel");
    const [, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({ task: "make a reel" });
  });

  it("createProduction rejects on a non-2xx response", async () => {
    mockFetchError(404, "asset not found");
    const c = new LauraClient("http://h", "tok");
    await expect(c.createProduction("missing", "make a reel")).rejects.toThrow(
      "404: asset not found",
    );
  });

  it("sendProductionMessage POSTs text with the token header and maps the response", async () => {
    const fn = mockFetch({ session_id: "s1", job_id: "j2" });
    const c = new LauraClient("http://h", "tok");
    const out = await c.sendProductionMessage("s1", "make it punchier");
    expect(out).toEqual({ session_id: "s1", job_id: "j2" });
    const [url, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://h/production/s1/message");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["X-Laura-Token"]).toBe("tok");
    expect(JSON.parse(init.body as string)).toEqual({ text: "make it punchier" });
  });

  it("sendProductionMessage rejects on a non-2xx response", async () => {
    mockFetchError(404, "session not found");
    const c = new LauraClient("http://h", "tok");
    await expect(c.sendProductionMessage("missing", "hi")).rejects.toThrow(
      "404: session not found",
    );
  });

  it("getProductionStatus GETs the session status and maps the response verbatim", async () => {
    const fn = mockFetch(status);
    const c = new LauraClient("http://h", "tok");
    const out = await c.getProductionStatus("s1");
    expect(out).toEqual(status);
    const [url, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://h/production/s1");
    expect((init as RequestInit).method ?? "GET").toBe("GET");
    expect((init.headers as Record<string, string>)["X-Laura-Token"]).toBe("tok");
  });

  it("getProductionStatus rejects on a non-2xx response", async () => {
    mockFetchError(404, "session not found");
    const c = new LauraClient("http://h", "tok");
    await expect(c.getProductionStatus("missing")).rejects.toThrow("404: session not found");
  });
});
