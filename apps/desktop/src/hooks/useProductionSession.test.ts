import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { JobStatus, LauraClient, ProductionStatus } from "../api";
import { useProductionSession } from "./useProductionSession";

function job(overrides: Partial<JobStatus> = {}): JobStatus {
  return {
    id: "j1",
    queue: "default",
    kind: "production.run",
    status: "running",
    attempt: 1,
    max_attempts: 3,
    result_json: null,
    error_json: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    finished_at: null,
    ...overrides,
  };
}

function boardStatus(overrides: Partial<ProductionStatus> = {}): ProductionStatus {
  return {
    meta: {
      session_id: "s1",
      asset_id: "asset-1",
      created_utc: "2026-01-01T00:00:00Z",
      task: "make a short",
      format: "insta",
      target_seconds: 30,
      status: "active",
    },
    scene_reviews: { count: 0, scenes: [] },
    artifacts: {
      storyline: { version: null, archived_versions: [] },
      script: { version: null, archived_versions: [] },
      voice: { version: null, archived_versions: [] },
      cutlist: { version: null, archived_versions: [] },
      render_report: { version: null, archived_versions: [] },
      qa_report: { version: null, archived_versions: [] },
    },
    resume_point: "storyline",
    ...overrides,
  };
}

function makeClient(overrides: Partial<LauraClient> = {}): LauraClient {
  return {
    createProduction: vi.fn().mockResolvedValue({ session_id: "s1", job_id: "j1" }),
    sendProductionMessage: vi.fn().mockResolvedValue({ session_id: "s1", job_id: "j2" }),
    getProductionStatus: vi.fn().mockResolvedValue(boardStatus()),
    getJob: vi.fn().mockResolvedValue(job({ status: "succeeded", result_json: '{"ok":true}' })),
    ...overrides,
  } as unknown as LauraClient;
}

const STORAGE_KEY = "laura.production.asset-1";

// Flushes pending promise chains (mount-effect resume checks, etc.) without advancing real
// time — vi.advanceTimersByTimeAsync loops timer-run + microtask-flush even for ms=0.
async function flush(): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  window.localStorage.clear();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useProductionSession", () => {
  it("start(): creates the session, persists it, then polls to done (create -> poll -> done)", async () => {
    const calls: string[] = [];
    const client = makeClient({
      createProduction: vi.fn(async () => {
        calls.push("create");
        return { session_id: "s1", job_id: "j1" };
      }),
      getJob: vi.fn(async () => {
        calls.push("getJob");
        return job({ status: "succeeded", result_json: '{"ok":true,"export_id":"e1"}' });
      }),
      getProductionStatus: vi.fn(async () => {
        calls.push("getStatus");
        return boardStatus();
      }),
    });

    const { result } = renderHook(() => useProductionSession(client, "asset-1"));

    await act(async () => {
      await result.current.start("Make a short", 30);
    });

    // Session created and persisted immediately; the poll loop hasn't ticked yet.
    expect(result.current.state.phase).toBe("running");
    expect(result.current.state.sessionId).toBe("s1");
    expect(result.current.state.jobId).toBe("j1");
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe(
      JSON.stringify({ sessionId: "s1", jobId: "j1" }),
    );
    expect(client.getJob).not.toHaveBeenCalled();
    expect(client.createProduction).toHaveBeenCalledWith("asset-1", "Make a short", 30);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    expect(result.current.state.phase).toBe("done");
    expect(result.current.state.jobResult).toEqual({ ok: true, export_id: "e1" });
    expect(result.current.state.status).not.toBeNull();
    expect(result.current.state.error).toBeNull();
    expect(calls).toEqual(["create", "getJob", "getStatus"]);

    // Timer stopped on terminal — advancing further must not poll again.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(calls).toEqual(["create", "getJob", "getStatus"]);
  });

  it("start(): a rejected createProduction sets phase error and writes no storage entry", async () => {
    const client = makeClient({
      createProduction: vi.fn().mockRejectedValue(new Error("boom")),
    });
    const { result } = renderHook(() => useProductionSession(client, "asset-1"));

    await act(async () => {
      await result.current.start("Make a short");
    });

    expect(result.current.state.phase).toBe("error");
    expect(result.current.state.error).toContain("boom");
    expect(result.current.state.sessionId).toBeNull();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
    expect(client.getJob).not.toHaveBeenCalled();
  });

  it("mount with no localStorage entry stays idle and polls nothing", async () => {
    const client = makeClient();
    const { result } = renderHook(() => useProductionSession(client, "asset-1"));

    await flush();

    expect(result.current.state.phase).toBe("idle");
    expect(result.current.state.sessionId).toBeNull();
    expect(client.getJob).not.toHaveBeenCalled();
    expect(client.getProductionStatus).not.toHaveBeenCalled();
  });

  it("resumes a still-running stored session immediately, then keeps polling until done", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ sessionId: "s1", jobId: "j1" }));
    let pollCount = 0;
    const client = makeClient({
      getJob: vi.fn(async () => {
        pollCount += 1;
        return job({
          status: pollCount >= 2 ? "succeeded" : "running",
          result_json: '{"ok":true}',
        });
      }),
    });

    const { result } = renderHook(() => useProductionSession(client, "asset-1"));

    // Resume performs an immediate check — no timer advance needed for the first poll.
    await flush();

    expect(result.current.state.phase).toBe("running");
    expect(result.current.state.sessionId).toBe("s1");
    expect(result.current.state.jobId).toBe("j1");
    expect(client.getJob).toHaveBeenCalledTimes(1);
    expect(client.getProductionStatus).toHaveBeenCalledTimes(1);
    expect(result.current.state.status).not.toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    expect(client.getJob).toHaveBeenCalledTimes(2);
    expect(result.current.state.phase).toBe("done");
  });

  it("resumes an already-terminal stored session as phase done, without ever polling", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ sessionId: "s1", jobId: "j1" }));
    const client = makeClient({
      getJob: vi.fn().mockResolvedValue(job({ status: "succeeded", result_json: '{"ok":true}' })),
    });

    const { result } = renderHook(() => useProductionSession(client, "asset-1"));
    await flush();

    expect(result.current.state.phase).toBe("done");
    expect(result.current.state.jobResult).toEqual({ ok: true });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(client.getJob).toHaveBeenCalledTimes(1);
  });

  it("resumes an already-failed stored session as phase error", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ sessionId: "s1", jobId: "j1" }));
    const client = makeClient({
      getJob: vi.fn().mockResolvedValue(job({ status: "failed", error_json: '{"error":"boom"}' })),
    });

    const { result } = renderHook(() => useProductionSession(client, "asset-1"));
    await flush();

    expect(result.current.state.phase).toBe("error");
    expect(result.current.state.error).toBe("boom");
  });

  it("a failed-before-board job does not crash: status stays null, phase still error", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ sessionId: "s1", jobId: "j1" }));
    const client = makeClient({
      getJob: vi.fn().mockResolvedValue(job({ status: "failed", error_json: '{"error":"no board"}' })),
      getProductionStatus: vi.fn().mockRejectedValue(new Error("404: no such session")),
    });

    const { result } = renderHook(() => useProductionSession(client, "asset-1"));
    await flush();

    expect(result.current.state.phase).toBe("error");
    expect(result.current.state.error).toBe("no board");
    expect(result.current.state.status).toBeNull();
  });

  it("sendMessage(): posts the follow-up, starts a new poll cycle under the same session", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ sessionId: "s1", jobId: "j1" }));
    const client = makeClient({
      getJob: vi.fn().mockResolvedValue(job({ status: "succeeded", result_json: '{"ok":true}' })),
      sendProductionMessage: vi.fn().mockResolvedValue({ session_id: "s1", job_id: "j2" }),
    });

    const { result } = renderHook(() => useProductionSession(client, "asset-1"));
    await flush();
    expect(result.current.state.phase).toBe("done");

    // The follow-up job (j2) stays non-terminal here — this test only proves a fresh poll
    // cycle started for it, not another full lifecycle.
    client.getJob = vi.fn().mockResolvedValue(job({ id: "j2", status: "running" }));

    await act(async () => {
      await result.current.sendMessage("Kapitel 2 andere Szene");
    });

    expect(client.sendProductionMessage).toHaveBeenCalledWith("s1", "Kapitel 2 andere Szene");
    expect(result.current.state.phase).toBe("running");
    expect(result.current.state.jobId).toBe("j2");
    expect(result.current.state.jobResult).toBeNull();
    expect(result.current.state.error).toBeNull();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe(
      JSON.stringify({ sessionId: "s1", jobId: "j2" }),
    );
    expect(client.getJob).not.toHaveBeenCalled(); // fresh mock — no tick yet

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    expect(client.getJob).toHaveBeenCalledTimes(1);
  });

  it("sendMessage(): a no-op without an active session (nothing to post a follow-up onto)", async () => {
    const client = makeClient();
    const { result } = renderHook(() => useProductionSession(client, "asset-1"));
    await flush();

    await act(async () => {
      await result.current.sendMessage("hello");
    });

    expect(client.sendProductionMessage).not.toHaveBeenCalled();
    expect(result.current.state.phase).toBe("idle");
  });

  it("stops polling on unmount (no poll leak)", async () => {
    const client = makeClient({
      getJob: vi.fn().mockResolvedValue(job({ status: "running" })),
    });
    const { result, unmount } = renderHook(() => useProductionSession(client, "asset-1"));

    await act(async () => {
      await result.current.start("Make a short");
    });
    expect(result.current.state.phase).toBe("running");

    unmount();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });

    expect(client.getJob).not.toHaveBeenCalled();
  });

  it("stops polling for the previous asset on an asset switch (no cross-asset leak)", async () => {
    const client = makeClient({
      getJob: vi.fn().mockResolvedValue(job({ status: "running" })),
    });
    const { result, rerender } = renderHook(
      ({ assetId }: { assetId: string | null }) => useProductionSession(client, assetId),
      { initialProps: { assetId: "asset-1" as string | null } },
    );

    await act(async () => {
      await result.current.start("Make a short");
    });
    expect(result.current.state.phase).toBe("running");

    rerender({ assetId: "asset-2" });
    await flush();

    // asset-2 has no stored session -> idle, and asset-1's job must no longer be polled.
    expect(result.current.state.phase).toBe("idle");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(client.getJob).not.toHaveBeenCalled();
  });

  it("reset(): clears storage, stops polling, and returns to idle", async () => {
    const client = makeClient({
      getJob: vi.fn().mockResolvedValue(job({ status: "running" })),
    });
    const { result } = renderHook(() => useProductionSession(client, "asset-1"));

    await act(async () => {
      await result.current.start("Make a short");
    });
    expect(window.localStorage.getItem(STORAGE_KEY)).not.toBeNull();

    act(() => {
      result.current.reset();
    });

    expect(result.current.state).toEqual({
      phase: "idle",
      sessionId: null,
      jobId: null,
      status: null,
      jobResult: null,
      error: null,
    });
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(client.getJob).not.toHaveBeenCalled();
  });
});
