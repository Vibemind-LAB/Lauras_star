import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  LauraApiError,
  type LauraClient,
  type VisualSceneSelection,
  type VisualSelectionDraft,
  type VisualSelectionGateStatus,
} from "../../api";
import { useVisualSelectionDraft } from "./useVisualSelectionDraft";

type DraftClient = Pick<LauraClient, "saveVisualSelectionDraft">;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

const recommended: VisualSceneSelection[] = [
  { rough_cut_order: 0, candidate_id: "recommended-0", included: true, requested_duration_s: 5 },
  { rough_cut_order: 1, candidate_id: "recommended-1", included: true, requested_duration_s: 5 },
  { rough_cut_order: 2, candidate_id: "recommended-2", included: true, requested_duration_s: 5 },
];

function gate(draft?: VisualSelectionDraft): VisualSelectionGateStatus {
  return {
    enabled: true,
    approved: false,
    pending: true,
    proposal_id: "a".repeat(64),
    beats: [],
    scene_choices: recommended.map((selection) => ({
      rough_cut_order: selection.rough_cut_order,
      scene_number: selection.rough_cut_order + 1,
      description: "description",
      transcript: "transcript",
      rationale: "rationale",
      candidates: [
        {
          candidate_id: selection.candidate_id,
          rough_cut_order: selection.rough_cut_order,
          scene_number: selection.rough_cut_order + 1,
          window_index: 0,
          src_start_frame: selection.rough_cut_order * 100,
          src_end_frame_exclusive: selection.rough_cut_order * 100 + 100,
          thumb_frame: selection.rough_cut_order * 100 + 50,
          max_duration_s: 10,
          description: "candidate",
          transcript_snippet: "snippet",
          rationale: "reason",
          score: 1,
        },
      ],
      recommended_candidate_id: selection.candidate_id,
      recommended_included: selection.included,
      recommended_duration_s: selection.requested_duration_s,
      selected_candidate_id: null,
      included: null,
      requested_duration_s: null,
    })),
    voice_total_frames: 450,
    fps: 30,
    draft,
  };
}

function saved(
  selections: VisualSceneSelection[],
  revision: number,
): VisualSelectionDraft {
  return {
    session_id: "s1",
    proposal_hash: "a".repeat(64),
    selections,
    revision,
    updated_utc: `2026-08-17T10:00:0${revision}+00:00`,
    stale: false,
    stale_reason: null,
  };
}

describe("useVisualSelectionDraft", () => {
  it("initializes from the server draft instead of recommendations", () => {
    const serverSelections = recommended.map((item, index) => ({
      ...item,
      included: index !== 1,
      requested_duration_s: 7,
    }));
    const client: DraftClient = { saveVisualSelectionDraft: vi.fn() };
    const { result } = renderHook(() =>
      useVisualSelectionDraft({
        client,
        sessionId: "s1",
        gate: gate(saved(serverSelections, 4)),
      }),
    );

    expect(result.current.decisions).toEqual(serverSelections);
    expect(result.current.savedAt).toBe("2026-08-17T10:00:04+00:00");
  });

  it("serializes rapid complete-state saves and advances server revisions", async () => {
    const saves = [deferred<VisualSelectionDraft>(), deferred<VisualSelectionDraft>(), deferred<VisualSelectionDraft>()];
    const saveVisualSelectionDraft = vi
      .fn()
      .mockReturnValueOnce(saves[0].promise)
      .mockReturnValueOnce(saves[1].promise)
      .mockReturnValueOnce(saves[2].promise);
    const { result } = renderHook(() =>
      useVisualSelectionDraft({
        client: { saveVisualSelectionDraft },
        sessionId: "s1",
        gate: gate(),
      }),
    );
    const states = [6, 7, 8].map((duration) =>
      recommended.map((item) => ({ ...item, requested_duration_s: duration })),
    );

    act(() => {
      result.current.updateDecision(states[0]);
      result.current.updateDecision(states[1]);
      result.current.updateDecision(states[2]);
    });
    await waitFor(() => expect(saveVisualSelectionDraft).toHaveBeenCalledTimes(1));
    expect(saveVisualSelectionDraft.mock.calls[0]?.[1]).toEqual({
      proposal_hash: "a".repeat(64),
      expected_revision: null,
      selections: states[0],
    });

    saves[0].resolve(saved(states[0], 1));
    await waitFor(() => expect(saveVisualSelectionDraft).toHaveBeenCalledTimes(2));
    expect(saveVisualSelectionDraft.mock.calls[1]?.[1].expected_revision).toBe(1);
    saves[1].resolve(saved(states[1], 2));
    await waitFor(() => expect(saveVisualSelectionDraft).toHaveBeenCalledTimes(3));
    expect(saveVisualSelectionDraft.mock.calls[2]?.[1].expected_revision).toBe(2);
    saves[2].resolve(saved(states[2], 3));
    await act(async () => result.current.flush());

    expect(result.current.decisions).toEqual(states[2]);
    expect(result.current.saveState).toBe("saved");
    expect(result.current.savedAt).toBe("2026-08-17T10:00:03+00:00");
  });

  it("preserves local decisions on conflict and can load the server draft", async () => {
    const serverDraft = saved(recommended, 4);
    const local = recommended.map((item) => ({ ...item, included: false }));
    const error = new LauraApiError(
      409,
      { detail: { code: "revision_conflict", current: serverDraft } },
      "409: conflict",
    );
    const { result } = renderHook(() =>
      useVisualSelectionDraft({
        client: { saveVisualSelectionDraft: vi.fn().mockRejectedValue(error) },
        sessionId: "s1",
        gate: gate(),
      }),
    );

    act(() => result.current.updateDecision(local));
    await act(async () => result.current.flush());
    expect(result.current.saveState).toBe("conflict");
    expect(result.current.decisions).toEqual(local);

    act(() => result.current.loadServerDraft());
    expect(result.current.decisions).toEqual(serverDraft.selections);
    expect(result.current.saveState).toBe("saved");
  });

  it("classifies stale source conflicts", async () => {
    const error = new LauraApiError(
      409,
      { detail: { code: "stale_visual_selection", reason: "source_content_changed" } },
      "409: stale",
    );
    const { result } = renderHook(() =>
      useVisualSelectionDraft({
        client: { saveVisualSelectionDraft: vi.fn().mockRejectedValue(error) },
        sessionId: "s1",
        gate: gate(),
      }),
    );
    act(() => result.current.updateDecision(recommended));
    await act(async () => result.current.flush());
    expect(result.current.saveState).toBe("stale");
  });

  it("retries the latest complete state after a network error", async () => {
    const changed = recommended.map((item) => ({ ...item, requested_duration_s: 8 }));
    const saveVisualSelectionDraft = vi
      .fn()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(saved(changed, 1));
    const { result } = renderHook(() =>
      useVisualSelectionDraft({
        client: { saveVisualSelectionDraft },
        sessionId: "s1",
        gate: gate(),
      }),
    );
    act(() => result.current.updateDecision(changed));
    await act(async () => result.current.flush());
    expect(result.current.saveState).toBe("error");

    act(() => result.current.retry());
    await act(async () => result.current.flush());
    expect(saveVisualSelectionDraft).toHaveBeenCalledTimes(2);
    expect(saveVisualSelectionDraft.mock.calls[1]?.[1].selections).toEqual(changed);
    expect(result.current.saveState).toBe("saved");
  });
});
