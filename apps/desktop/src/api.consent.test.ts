import { afterEach, describe, expect, it, vi } from "vitest";

import { LauraClient, type ConsentRecord } from "./api";

const rec: ConsentRecord = {
  id: "c1",
  project_id: "p1",
  subject_label: "Laura",
  confirmed_at: "2026-06-22T00:00:00Z",
  confirmed_by: null,
  source_asset_id: null,
  note: null,
  revoked_at: null,
};

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

afterEach(() => vi.restoreAllMocks());

describe("consent client", () => {
  it("listConsent GETs the project consent collection", async () => {
    const fn = mockFetch([rec]);
    const c = new LauraClient("http://h", "tok");
    const records = await c.listConsent("p1");
    expect(records).toEqual([rec]);
    const [url, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/projects/p1/consent");
    expect((init as RequestInit).method ?? "GET").toBe("GET");
  });

  it("revokeConsent POSTs the revoke route", async () => {
    const fn = mockFetch({ ...rec, revoked_at: "2026-06-22T01:00:00Z" });
    const c = new LauraClient("http://h", "tok");
    const out = await c.revokeConsent("p1", "c1");
    expect(out.revoked_at).not.toBeNull();
    const [url, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/projects/p1/consent/c1/revoke");
    expect((init as RequestInit).method).toBe("POST");
  });
});
