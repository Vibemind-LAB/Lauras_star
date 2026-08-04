import { afterEach, describe, expect, it, vi } from "vitest";

import { LauraClient, type ChatMessage, type ProductionBoardStatus } from "./api";

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

function mockFetchNoContent() {
  const fn = vi.fn().mockResolvedValue({
    ok: true,
    status: 204,
    json: async () => ({}),
    text: async () => "",
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.restoreAllMocks());

const userMessage: ChatMessage = {
  id: "m1",
  conversation_id: "c1",
  seq: 1,
  role: "user",
  kind: "text",
  content: { text: "hi" },
  created_at: "2026-08-03T00:00:00Z",
};

describe("createConversation", () => {
  it("POSTs to /conversations with the token header and returns the id", async () => {
    const fn = mockFetch({ id: "c1" });
    const c = new LauraClient("http://h", "tok");

    const out = await c.createConversation();

    expect(out).toEqual({ id: "c1" });
    const [url, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://h/conversations");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["X-Laura-Token"]).toBe("tok");
  });

  it("rejects on a non-2xx response", async () => {
    mockFetchError(500, "boom");
    const c = new LauraClient("http://h", "tok");
    await expect(c.createConversation()).rejects.toThrow("500: boom");
  });
});

describe("listConversations", () => {
  it("GETs /conversations with the token header and returns the list", async () => {
    const summaries = [{ id: "c1", title: "Hallo", updated_at: "2026-08-03T00:00:00Z" }];
    const fn = mockFetch(summaries);
    const c = new LauraClient("http://h", "tok");

    const out = await c.listConversations();

    expect(out).toEqual(summaries);
    const [url, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://h/conversations");
    expect((init as RequestInit).method ?? "GET").toBe("GET");
    expect((init.headers as Record<string, string>)["X-Laura-Token"]).toBe("tok");
  });

  it("rejects on a non-2xx response", async () => {
    mockFetchError(500, "boom");
    const c = new LauraClient("http://h", "tok");
    await expect(c.listConversations()).rejects.toThrow("500: boom");
  });
});

describe("getConversation", () => {
  it("GETs /conversations/{id} with the token header and returns the detail", async () => {
    const detail = {
      id: "c1",
      title: "Hallo",
      active_project_id: "p1",
      messages: [userMessage],
    };
    const fn = mockFetch(detail);
    const c = new LauraClient("http://h", "tok");

    const out = await c.getConversation("c1");

    expect(out).toEqual(detail);
    const [url, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://h/conversations/c1");
    expect((init.headers as Record<string, string>)["X-Laura-Token"]).toBe("tok");
  });

  it("rejects on a non-2xx response", async () => {
    mockFetchError(404, "conversation not found");
    const c = new LauraClient("http://h", "tok");
    await expect(c.getConversation("missing")).rejects.toThrow("404: conversation not found");
  });
});

describe("deleteConversation", () => {
  it("DELETEs /conversations/{id} with the token header", async () => {
    const fn = mockFetchNoContent();
    const c = new LauraClient("http://h", "tok");

    await c.deleteConversation("c1");

    const [url, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://h/conversations/c1");
    expect(init.method).toBe("DELETE");
    expect((init.headers as Record<string, string>)["X-Laura-Token"]).toBe("tok");
  });

  it("rejects on a non-2xx response", async () => {
    mockFetchError(404, "conversation not found");
    const c = new LauraClient("http://h", "tok");
    await expect(c.deleteConversation("missing")).rejects.toThrow("404: conversation not found");
  });
});

describe("sendChatMessage", () => {
  it("POSTs {text} to /conversations/{id}/message and returns the appended messages", async () => {
    const result = { messages: [userMessage] };
    const fn = mockFetch(result);
    const c = new LauraClient("http://h", "tok");

    const out = await c.sendChatMessage("c1", "hi");

    expect(out).toEqual(result);
    const [url, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://h/conversations/c1/message");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["X-Laura-Token"]).toBe("tok");
    expect(JSON.parse(init.body as string)).toEqual({ text: "hi" });
  });

  it("rejects on a non-2xx response", async () => {
    mockFetchError(404, "conversation not found");
    const c = new LauraClient("http://h", "tok");
    await expect(c.sendChatMessage("missing", "hi")).rejects.toThrow(
      "404: conversation not found",
    );
  });
});

describe("decideApproval", () => {
  it("POSTs {decision} to /conversations/{id}/approvals/{messageId} and returns the messages", async () => {
    const approvalMessage: ChatMessage = {
      ...userMessage,
      id: "m2",
      kind: "approval_request",
      content: { status: "approved" },
    };
    const result = { messages: [approvalMessage] };
    const fn = mockFetch(result);
    const c = new LauraClient("http://h", "tok");

    const out = await c.decideApproval("c1", "m2", "approve");

    expect(out).toEqual(result);
    const [url, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://h/conversations/c1/approvals/m2");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["X-Laura-Token"]).toBe("tok");
    expect(JSON.parse(init.body as string)).toEqual({ decision: "approve" });
  });

  it("rejects on a non-2xx response (e.g. already-decided card)", async () => {
    mockFetchError(409, "approval already decided: status='rejected'");
    const c = new LauraClient("http://h", "tok");
    await expect(c.decideApproval("c1", "m2", "reject")).rejects.toThrow(
      "409: approval already decided: status='rejected'",
    );
  });
});

describe("getProductionEvents", () => {
  it("GETs /production/{sessionId}/events?after=N and returns the batch", async () => {
    const batch = {
      events: [{ type: "stage", stage: "storyline", team: "core" }],
      next: 3,
      done: false,
    };
    const fn = mockFetch(batch);
    const c = new LauraClient("http://h", "tok");

    const out = await c.getProductionEvents("s1", 2);

    expect(out).toEqual(batch);
    const [url, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://h/production/s1/events?after=2");
    expect((init.headers as Record<string, string>)["X-Laura-Token"]).toBe("tok");
  });

  it("rejects on a non-2xx response", async () => {
    mockFetchError(404, "session not found");
    const c = new LauraClient("http://h", "tok");
    await expect(c.getProductionEvents("missing", 0)).rejects.toThrow("404: session not found");
  });
});

describe("getProductionStatus (script gate contracts)", () => {
  it("parses script_gate + script_lines from the board status with typed access", async () => {
    const board: ProductionBoardStatus = {
      board_ready: true,
      job: null,
      meta: {
        session_id: "s1",
        asset_id: "a1",
        created_utc: "2026-08-04T00:00:00Z",
        task: "make a reel",
        format: "insta",
        target_seconds: 60,
        status: "active",
      },
      scene_reviews: { count: 1, scenes: [1], degraded_count: 0, degraded_scenes: [] },
      artifacts: {
        storyline: { version: 1, archived_versions: [] },
        script: { version: 1, archived_versions: [] },
        voice: { version: null, archived_versions: [] },
        cutlist: { version: null, archived_versions: [] },
        contact_sheet: { version: null, archived_versions: [] },
        render_report: { version: null, archived_versions: [] },
        qa_report: { version: null, archived_versions: [] },
      },
      resume_point: "voice",
      script_gate: { enabled: true, approved: false, pending: true },
      script_lines: [{ chapter: 1, scene_number: 1, text: "Hallo Welt" }],
    };
    mockFetch(board);
    const c = new LauraClient("http://h", "tok");

    const status = await c.getProductionStatus("s1");

    if (status.board_ready) {
      expect(status.script_gate?.pending).toBe(true);
      expect(status.script_gate?.enabled).toBe(true);
      expect(status.script_gate?.approved).toBe(false);
      expect(status.script_lines?.[0].text).toBe("Hallo Welt");
      expect(status.script_lines?.[0].chapter).toBe(1);
      expect(status.script_lines?.[0].scene_number).toBe(1);
    } else {
      throw new Error("expected board_ready status");
    }
  });

  it("omits script_gate/script_lines cleanly when an older backend does not send them", async () => {
    const board: ProductionBoardStatus = {
      board_ready: true,
      job: null,
      meta: {
        session_id: "s1",
        asset_id: "a1",
        created_utc: "2026-08-04T00:00:00Z",
        task: "make a reel",
        format: "insta",
        target_seconds: 60,
        status: "active",
      },
      scene_reviews: { count: 1, scenes: [1], degraded_count: 0, degraded_scenes: [] },
      artifacts: {
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
    mockFetch(board);
    const c = new LauraClient("http://h", "tok");

    const status = await c.getProductionStatus("s1");

    if (status.board_ready) {
      expect(status.script_gate).toBeUndefined();
      expect(status.script_lines).toBeUndefined();
    } else {
      throw new Error("expected board_ready status");
    }
  });
});

describe("getExport", () => {
  it("GETs /exports/{id} and returns the export status", async () => {
    const result = { id: "e1", status: "ready", path: "/out/e1.mp4", size_bytes: 12345 };
    const fn = mockFetch(result);
    const c = new LauraClient("http://h", "tok");

    const out = await c.getExport("e1");

    expect(out).toEqual(result);
    const [url, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://h/exports/e1");
    expect((init.headers as Record<string, string>)["X-Laura-Token"]).toBe("tok");
  });

  it("rejects on a non-2xx response", async () => {
    mockFetchError(404, "export not found");
    const c = new LauraClient("http://h", "tok");
    await expect(c.getExport("missing")).rejects.toThrow("404: export not found");
  });
});
