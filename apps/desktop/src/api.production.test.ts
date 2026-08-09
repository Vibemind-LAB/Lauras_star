import { afterEach, describe, expect, it, vi } from "vitest";

import {
  LauraClient,
  type ContactSheetGateStatus,
  type ProductionBoardStatus,
  type VisualSelectionGateStatus,
} from "./api";

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

const status: ProductionBoardStatus = {
  board_ready: true,
  job: null,
  meta: {
    session_id: "s1",
    asset_id: "a1",
    created_utc: "2026-07-13T00:00:00Z",
    task: "make a reel",
    format: "insta",
    target_seconds: 60,
    status: "active",
  },
  scene_reviews: { count: 2, scenes: [1, 2], degraded_count: 0, degraded_scenes: [] },
  artifacts: {
    scene_selection: { version: null, archived_versions: [] },
    storyline: { version: 1, archived_versions: [] },
    script: { version: 1, archived_versions: [] },
    voice: { version: null, archived_versions: [] },
    cutlist: { version: null, archived_versions: [] },
    contact_sheet: { version: null, archived_versions: [] },
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

  it("revertProduction POSTs artifact+version with the token header and maps the response", async () => {
    const fn = mockFetch({
      ok: true,
      artifact: "cutlist",
      version: 1,
      invalidated: ["cutlist", "contact_sheet"],
      restored: ["script"],
      status,
    });
    const c = new LauraClient("http://h", "tok");
    const out = await c.revertProduction("s1", "cutlist", 1);
    expect(out).toEqual({
      ok: true,
      artifact: "cutlist",
      version: 1,
      invalidated: ["cutlist", "contact_sheet"],
      restored: ["script"],
      status,
    });
    const [url, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://h/production/s1/revert");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["X-Laura-Token"]).toBe("tok");
    expect(JSON.parse(init.body as string)).toEqual({ artifact: "cutlist", version: 1 });
  });

  it("revertProduction rejects on a non-2xx response", async () => {
    mockFetchError(404, "session not found");
    const c = new LauraClient("http://h", "tok");
    await expect(c.revertProduction("missing", "cutlist", 1)).rejects.toThrow(
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

  it("confirms a visual proposal with its exact id and selected candidates", async () => {
    const fn = mockFetch({ session_id: "s1", job_id: "j2" });
    const c = new LauraClient("http://h", "tok");
    const proposalId = "a".repeat(64);

    const out = await c.confirmVisualSelection("s1", proposalId, ["candidate-1"]);

    expect(out).toEqual({ session_id: "s1", job_id: "j2" });
    expect(fn).toHaveBeenCalledWith(
      expect.stringContaining("/production/s1/visual-selection:confirm"),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          proposal_hash: proposalId,
          selected_candidate_ids: ["candidate-1"],
        }),
      }),
    );
  });

  it("confirms the currently displayed contact-sheet hash", async () => {
    const fn = mockFetch({ session_id: "s1", job_id: "j3" });
    const c = new LauraClient("http://h", "tok");
    const contactSheetHash = "b".repeat(64);

    const out = await c.confirmContactSheet("s1", contactSheetHash);

    expect(out).toEqual({ session_id: "s1", job_id: "j3" });
    expect(fn).toHaveBeenCalledWith(
      expect.stringContaining("/production/s1/contact-sheet:confirm"),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ contact_sheet_hash: contactSheetHash }),
      }),
    );
  });

  it("maps both optional visual gates while older status payloads remain valid", async () => {
    const visualGate: VisualSelectionGateStatus = {
      enabled: true,
      approved: false,
      pending: true,
      proposal_id: "a".repeat(64),
      beats: [
        {
          beat_id: "beat-1",
          voice_segment_index: 0,
          narration_text: "Rowboat ordnet Dateien.",
          duration_s: 2.5,
          recommended_candidate_id: "candidate-1",
          selected_candidate_id: null,
          candidates: [
            {
              candidate_id: "candidate-1",
              beat_id: "beat-1",
              voice_segment_index: 0,
              scene_number: 2,
              window_index: 1,
              src_start_frame: 120,
              src_end_frame_exclusive: 240,
              thumb_frame: 180,
              description: "Dateiliste",
              transcript_snippet: "organize files",
              rationale: "Passt zum Sprechertext",
              score: 0.9,
            },
          ],
        },
      ],
    };
    const contactGate: ContactSheetGateStatus = {
      enabled: true,
      approved: false,
      pending: true,
      current_sheet_hash: "b".repeat(64),
      tiles: [
        {
          order: 0,
          scene_number: 2,
          frame: 180,
          label: "0 S2",
          src_start_frame: 120,
          src_end_frame_exclusive: 240,
          narration_excerpt: "Rowboat ordnet Dateien.",
          rationale: "Passt zum Sprechertext",
        },
      ],
    };
    const current: ProductionBoardStatus = {
      ...status,
      visual_selection_gate: visualGate,
      contact_sheet_gate: contactGate,
    };
    mockFetch(current);
    const c = new LauraClient("http://h", "tok");

    const out = await c.getProductionStatus("s1");

    expect(out.board_ready && out.visual_selection_gate?.proposal_id).toBe("a".repeat(64));
    expect(out.board_ready && out.contact_sheet_gate?.current_sheet_hash).toBe("b".repeat(64));
    expect(status.visual_selection_gate).toBeUndefined();
    expect(status.contact_sheet_gate).toBeUndefined();
  });
});

describe("autoOverview", () => {
  // POST /projects/{pid}/auto-overview has existed since the auto-overview arc (2026-07-31,
  // live-tested) and was reachable only via curl — the desktop never grew a client method,
  // so the feature effectively did not exist for an app user.
  it("POSTs topic and target to the project endpoint and returns the montage result", async () => {
    const result = {
      sequence_id: "seq1",
      source_timeline_id: "tl1",
      clips: [
        {
          asset_id: "a1",
          display_name: "rowboat",
          scene_number: 2,
          start_frame: 100,
          end_frame_exclusive: 400,
          snippet: "AI Meetings transkribieren",
        },
      ],
      rationale: "covers the topic across two sources",
      fallback: false,
      ranking: [],
      warnings: ["overview covers a single source: only rowboat matched the topic"],
      export_id: "e1",
      job_id: "j1",
    };
    const fn = mockFetch(result);
    const c = new LauraClient("http://h", "tok");

    const out = await c.autoOverview("p1", { topic: "Meetings", target_seconds: 120 });

    expect(out).toEqual(result);
    const [url, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://h/projects/p1/auto-overview");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ topic: "Meetings", target_seconds: 120 });
    expect((init.headers as Record<string, string>)["X-Laura-Token"]).toBe("tok");
  });

  it("rejects with the backend's 422 reason text", async () => {
    mockFetchError(422, '{"detail":{"reason":"no material found for topic"}}');
    const c = new LauraClient("http://h", "tok");
    await expect(c.autoOverview("p1", { topic: "xyz" })).rejects.toThrow(
      "no material found for topic",
    );
  });
});

describe("contactSheetUrl", () => {
  // The backend has served GET /production/{sid}/contact-sheet since the Kontaktbogen arc;
  // the desktop never grew a client method for it, so the "Bogen" chip existed with no way
  // to SHOW the sheet — the last open gap of that feature.
  it("fetches the sheet PNG with the token and returns an object URL", async () => {
    const fn = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      blob: async () => new Blob(["png-bytes"]),
      text: async () => "",
    });
    vi.stubGlobal("fetch", fn);
    const c = new LauraClient("http://h", "tok");

    const url = await c.contactSheetUrl("s1");

    expect(url).toBe("blob:stub");
    const [reqUrl, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(reqUrl).toBe("http://h/production/s1/contact-sheet");
    expect((init.headers as Record<string, string>)["X-Laura-Token"]).toBe("tok");
  });

  it("rejects on a non-2xx response", async () => {
    mockFetchError(404, "no sheet");
    const c = new LauraClient("http://h", "tok");
    await expect(c.contactSheetUrl("s1")).rejects.toThrow("404: no sheet");
  });
});
