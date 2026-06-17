import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AnalysisRun, Asset, LauraClient } from "../api";
import { useAnalysis } from "./useAnalysis";

const asset = { id: "a1" } as unknown as Asset;

const run = (status: string): AnalysisRun => ({
  id: "r1",
  asset_id: "a1",
  pipeline_version: "v",
  status,
  started_at: null,
  finished_at: null,
  diagnostics: {},
});

function makeClient(over: Partial<LauraClient>): LauraClient {
  return {
    startAnalysis: vi.fn().mockResolvedValue({ analysis_run_id: "r1" }),
    getLatestAnalysis: vi.fn().mockResolvedValue(null),
    getShots: vi.fn().mockResolvedValue([]),
    getTranscript: vi.fn().mockResolvedValue([]),
    ...over,
  } as unknown as LauraClient;
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("useAnalysis.runAnalysis", () => {
  it("polls until the run is terminal, well past the old fixed ~126s cap", async () => {
    // The previous implementation gave up after 180 polls (~126s) and reported
    // "done" regardless. A 30-min video's transcription runs far longer, so the
    // poll must continue until the backend run actually finishes.
    const RUNNING_POLLS = 200; // > old 180 cap
    let polls = 0;
    const getLatestAnalysis = vi.fn(async () => {
      polls += 1;
      return polls > RUNNING_POLLS ? run("succeeded") : run("running");
    });
    const client = makeClient({ getLatestAnalysis });

    const { result } = renderHook(() => useAnalysis(client, asset));
    // Let the mount effect's getLatestAnalysis settle, then isolate the poll calls.
    await act(async () => {
      await Promise.resolve();
    });
    polls = 0;
    getLatestAnalysis.mockClear();

    await act(async () => {
      const p = result.current.runAnalysis();
      // Advance generously — far more than the old 126s window — so every poll fires.
      await vi.advanceTimersByTimeAsync(15 * 60 * 1000);
      await p;
    });

    expect(getLatestAnalysis.mock.calls.length).toBeGreaterThan(180);
    expect(result.current.status).toBe("done");
  });

  it("reports an error when the run finishes failed (not a silent 'done')", async () => {
    const getLatestAnalysis = vi.fn().mockResolvedValue(run("failed"));
    const client = makeClient({ getLatestAnalysis });

    const { result } = renderHook(() => useAnalysis(client, asset));
    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      const p = result.current.runAnalysis();
      await vi.advanceTimersByTimeAsync(5000);
      await p;
    });

    expect(result.current.status).toBe("error");
  });

  it("tolerates a few transient poll errors and still completes", async () => {
    // A momentary local-API hiccup mid-run must not abort a multi-minute analysis.
    let polls = 0;
    const getLatestAnalysis = vi.fn(async () => {
      polls += 1;
      if (polls === 1) return null; // mount effect — harmless
      if (polls <= 4) throw new Error("transient network blip"); // 3 consecutive errors
      return polls > 6 ? run("succeeded") : run("running");
    });
    const client = makeClient({ getLatestAnalysis });

    const { result } = renderHook(() => useAnalysis(client, asset));
    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      const p = result.current.runAnalysis();
      await vi.advanceTimersByTimeAsync(60 * 1000);
      await p;
    });

    expect(result.current.status).toBe("done");
  });

  it("gives up with status=error after too many consecutive poll errors", async () => {
    let polls = 0;
    const getLatestAnalysis = vi.fn(async () => {
      polls += 1;
      if (polls === 1) return null; // mount effect — harmless
      throw new Error("persistent network failure");
    });
    const client = makeClient({ getLatestAnalysis });

    const { result } = renderHook(() => useAnalysis(client, asset));
    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      const p = result.current.runAnalysis();
      await vi.advanceTimersByTimeAsync(60 * 1000);
      await p;
    });

    expect(result.current.status).toBe("error");
  });
});
