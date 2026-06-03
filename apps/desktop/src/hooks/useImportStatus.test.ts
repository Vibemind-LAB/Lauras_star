import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ImportStatus, LauraClient } from "../api";
import { useImportStatus } from "./useImportStatus";

const status = (phase: ImportStatus["phase"]): ImportStatus => ({
  phase, downloaded_bytes: null, total_bytes: null, speed_bps: null,
  eta_seconds: null, error: null,
});

beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
afterEach(() => vi.useRealTimers());

describe("useImportStatus", () => {
  it("polls until ready, then stops", async () => {
    const getImportStatus = vi
      .fn<(id: string) => Promise<ImportStatus>>()
      .mockResolvedValueOnce(status("downloading"))
      .mockResolvedValueOnce(status("ready"));
    const client = { getImportStatus } as unknown as LauraClient;

    const { result } = renderHook(() => useImportStatus(client, "a1", 1000));
    await waitFor(() => expect(result.current?.phase).toBe("downloading"));
    await vi.advanceTimersByTimeAsync(1000);
    await waitFor(() => expect(result.current?.phase).toBe("ready"));

    const callsAfterReady = getImportStatus.mock.calls.length;
    await vi.advanceTimersByTimeAsync(3000);
    expect(getImportStatus.mock.calls.length).toBe(callsAfterReady);
  });
});
