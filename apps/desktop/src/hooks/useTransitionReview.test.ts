import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { LauraClient, TransitionVerdict } from "../api";
import { useTransitionReview } from "./useTransitionReview";

function verdict(label: TransitionVerdict["label"]): TransitionVerdict {
  return {
    boundary_seq_frame: 0,
    asset_a: "a",
    asset_b: "b",
    src_out_a: 10,
    src_in_b: 10,
    smoothness: 0.5,
    label,
    reason: "",
    suggested_fix: { kind: "none" },
    model_id: "m",
    created_at: "t",
  };
}

describe("useTransitionReview", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("polls the review job to completion and surfaces ALL verdicts, not just the first", async () => {
    // Verdicts arrive incrementally across polls (one boundary at a time); the job reports
    // 'running' until the third status check, then 'succeeded'.
    const seq: TransitionVerdict[][] = [
      [],
      [verdict("jump_cut")],
      [verdict("jump_cut"), verdict("smooth")],
    ];
    let getN = 0;
    let jobN = 0;
    const client = {
      reviewTransitions: vi.fn().mockResolvedValue({ job_id: "j1" }),
      getTransitionReview: vi.fn(() =>
        Promise.resolve({ model: "m", verdicts: seq[Math.min(getN++, seq.length - 1)] }),
      ),
      getJob: vi.fn(() => Promise.resolve({ status: ++jobN >= 3 ? "succeeded" : "running" })),
    } as unknown as LauraClient;

    const { result } = renderHook(() => useTransitionReview(client, "tl1"));
    let runPromise!: Promise<void>;
    act(() => {
      runPromise = result.current.run();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500 * 5);
      await runPromise;
    });

    expect(result.current.loading).toBe(false);
    // The OLD hook broke at the first verdict (length 1); the fix waits for the job to finish (2).
    expect(result.current.verdicts).toHaveLength(2);
    expect(client.getJob).toHaveBeenCalled();
  });
});
