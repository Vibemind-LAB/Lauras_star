import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { type LauraClient, type ConsentRecord } from "../api";
import { partitionConsent, useConsent } from "./useConsent";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const base: ConsentRecord = {
  id: "x",
  project_id: "p",
  subject_label: "s",
  confirmed_at: "t",
  confirmed_by: null,
  source_asset_id: null,
  note: null,
  revoked_at: null,
};

const ACTIVE: ConsentRecord = { ...base, id: "a", revoked_at: null };
const REVOKED: ConsentRecord = {
  ...base,
  id: "b",
  revoked_at: "2026-06-22T00:00:00Z",
};

function fakeClient(over: Partial<LauraClient>): LauraClient {
  return {
    listConsent: vi.fn().mockResolvedValue([]),
    createConsent: vi.fn().mockResolvedValue(ACTIVE),
    revokeConsent: vi.fn().mockResolvedValue(REVOKED),
    ...over,
  } as unknown as LauraClient;
}

// ---------------------------------------------------------------------------
// partitionConsent — pure unit tests (no React)
// ---------------------------------------------------------------------------

describe("partitionConsent", () => {
  it("splits active vs revoked by revoked_at", () => {
    const recs: ConsentRecord[] = [
      { ...base, id: "a", revoked_at: null },
      { ...base, id: "b", revoked_at: "2026-06-22T00:00:00Z" },
    ];
    const { active, revoked } = partitionConsent(recs);
    expect(active.map((r) => r.id)).toEqual(["a"]);
    expect(revoked.map((r) => r.id)).toEqual(["b"]);
  });

  it("treats empty input as all-empty", () => {
    expect(partitionConsent([])).toEqual({ active: [], revoked: [] });
  });
});

// ---------------------------------------------------------------------------
// useConsent — hook integration tests (mocked client)
// ---------------------------------------------------------------------------

describe("useConsent", () => {
  it("loads consent records for a project on mount", async () => {
    const client = fakeClient({
      listConsent: vi.fn().mockResolvedValue([ACTIVE, REVOKED]),
    });
    const { result } = renderHook(() => useConsent(client, "p"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.records).toHaveLength(2);
    expect(result.current.active).toHaveLength(1);
    expect(result.current.active[0].id).toBe("a");
    expect(client.listConsent).toHaveBeenCalledWith("p");
  });

  it("create calls createConsent then reloads the list", async () => {
    const listFn = vi
      .fn()
      .mockResolvedValueOnce([]) // initial load
      .mockResolvedValueOnce([ACTIVE]); // after create
    const client = fakeClient({ listConsent: listFn });

    const { result } = renderHook(() => useConsent(client, "p"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.records).toHaveLength(0);

    await act(async () => {
      await result.current.create("Alice");
    });

    expect(client.createConsent).toHaveBeenCalledWith("p", {
      subjectLabel: "Alice",
    });
    expect(result.current.records).toHaveLength(1);
    expect(result.current.records[0].id).toBe("a");
  });

  it("revoke calls revokeConsent then reloads the list", async () => {
    const listFn = vi
      .fn()
      .mockResolvedValueOnce([ACTIVE]) // initial load
      .mockResolvedValueOnce([REVOKED]); // after revoke
    const client = fakeClient({ listConsent: listFn });

    const { result } = renderHook(() => useConsent(client, "p"));
    await waitFor(() => expect(result.current.records).toHaveLength(1));

    await act(async () => {
      await result.current.revoke("a");
    });

    expect(client.revokeConsent).toHaveBeenCalledWith("p", "a");
    expect(result.current.records[0].revoked_at).toBe(
      "2026-06-22T00:00:00Z",
    );
    expect(result.current.active).toHaveLength(0);
  });

  it("sets error state when listConsent throws", async () => {
    const client = fakeClient({
      listConsent: vi.fn().mockRejectedValue(new Error("network error")),
    });
    const { result } = renderHook(() => useConsent(client, "p"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("network error");
    expect(result.current.records).toHaveLength(0);
  });

  it("returns empty records and no error when projectId is null", async () => {
    const client = fakeClient({});
    const { result } = renderHook(() => useConsent(client, null));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.records).toHaveLength(0);
    expect(result.current.error).toBeNull();
    expect(client.listConsent).not.toHaveBeenCalled();
  });
});
