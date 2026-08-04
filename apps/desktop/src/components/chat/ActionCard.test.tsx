import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  AgentEvent,
  ChatMessage,
  JobStatus,
  LauraClient,
  ProductionBoardStatus,
} from "../../api";
import { renderWithQuery } from "../../test-utils";
import { ActionCard } from "./ActionCard";

function actionMessage(
  tool: string,
  refs: Record<string, unknown>,
  outcome = "running",
): ChatMessage {
  return {
    id: "m1",
    conversation_id: "c1",
    seq: 3,
    role: "assistant",
    kind: "action",
    content: { tool, args: {}, refs, outcome },
    created_at: "2026-01-01T00:00:00Z",
  };
}

function job(overrides: Partial<JobStatus> = {}): JobStatus {
  return {
    id: "j1",
    queue: "default",
    kind: "ingest.fetch",
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

function boardStatus(overrides: Partial<ProductionBoardStatus> = {}): ProductionBoardStatus {
  return {
    board_ready: true,
    job: {
      id: "j1",
      status: "succeeded",
      attempt: 1,
      updated_at: "2026-01-01T00:00:05Z",
      lease_expires_at: null,
      finished_at: "2026-01-01T00:00:05Z",
      export_id: "exp-1",
    },
    meta: {
      session_id: "s1",
      asset_id: "a1",
      created_utc: "2026-01-01T00:00:00Z",
      task: "make a short",
      format: "insta",
      target_seconds: 30,
      status: "complete",
    },
    scene_reviews: { count: 0, scenes: [], degraded_count: 0, degraded_scenes: [] },
    artifacts: {
      storyline: { version: 1, archived_versions: [] },
      script: { version: 1, archived_versions: [] },
      voice: { version: 1, archived_versions: [] },
      cutlist: { version: 1, archived_versions: [] },
      contact_sheet: { version: 1, archived_versions: [] },
      render_report: { version: 1, archived_versions: [], target_ratio: 0.82 },
      qa_report: { version: 1, archived_versions: [] },
    },
    resume_point: "done",
    ...overrides,
  };
}

function client(overrides: Partial<LauraClient> = {}): LauraClient {
  return {
    getProductionEvents: vi.fn(),
    getProductionStatus: vi.fn(),
    getJob: vi.fn(),
    ...overrides,
  } as unknown as LauraClient;
}

describe("ActionCard — production tools (start_short / follow_up)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("running production card renders event lines from a mocked events response", async () => {
    const events: AgentEvent[] = [
      { type: "stage", stage: "storyline", team: "core" },
      { type: "agent", agent: "scout", text: "sucht Momente" },
    ];
    const c = client({
      getProductionEvents: vi.fn().mockResolvedValue({ events, next: 2, done: false }),
    });
    renderWithQuery(
      <ActionCard
        message={actionMessage("start_short", { session_id: "s1", job_id: "j1" })}
        client={c}
      />,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    expect(c.getProductionEvents).toHaveBeenCalledWith("s1", 0);
    expect(screen.getByText(/storyline/)).toBeTruthy();
    expect(screen.getByText(/sucht Momente/)).toBeTruthy();
  });

  it("advances the cursor and accumulates events across polls", async () => {
    const getProductionEvents = vi
      .fn()
      .mockResolvedValueOnce({
        events: [{ type: "agent", agent: "scout", text: "erste Runde" }],
        next: 1,
        done: false,
      })
      .mockResolvedValueOnce({
        events: [{ type: "agent", agent: "scout", text: "zweite Runde" }],
        next: 2,
        done: false,
      });
    const c = client({ getProductionEvents });
    renderWithQuery(
      <ActionCard message={actionMessage("start_short", { session_id: "s1" })} client={c} />,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    expect(screen.getByText(/erste Runde/)).toBeTruthy();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    expect(getProductionEvents).toHaveBeenNthCalledWith(2, "s1", 1);
    expect(screen.getByText(/zweite Runde/)).toBeTruthy();
    expect(screen.getByText(/erste Runde/)).toBeTruthy();
  });

  it("only shows the last 5 events until 'alle anzeigen' is clicked", async () => {
    const events: AgentEvent[] = Array.from({ length: 7 }, (_, i) => ({
      type: "agent",
      agent: "scout",
      text: `Nachricht ${i + 1}`,
    }));
    const c = client({
      getProductionEvents: vi.fn().mockResolvedValue({ events, next: 7, done: false }),
    });
    renderWithQuery(
      <ActionCard message={actionMessage("start_short", { session_id: "s1" })} client={c} />,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    expect(screen.queryByText("Nachricht 1")).toBeNull();
    expect(screen.getByText("Nachricht 7")).toBeTruthy();
    const expander = screen.getByText("alle anzeigen");

    fireEvent.click(expander);
    expect(screen.getByText("Nachricht 1")).toBeTruthy();
  });

  it("done shows the export id and the target_ratio percent", async () => {
    const c = client({
      getProductionEvents: vi.fn().mockResolvedValue({ events: [], next: 0, done: true }),
      getProductionStatus: vi.fn().mockResolvedValue(boardStatus()),
    });
    renderWithQuery(
      <ActionCard message={actionMessage("start_short", { session_id: "s1" })} client={c} />,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    expect(c.getProductionStatus).toHaveBeenCalledWith("s1");
    expect(screen.getByText(/Export: exp-1/)).toBeTruthy();
    expect(screen.getByText(/82%/)).toBeTruthy();
  });

  it("'▶ ansehen' fires onFocus", async () => {
    const c = client({
      getProductionEvents: vi.fn().mockResolvedValue({ events: [], next: 0, done: true }),
      getProductionStatus: vi.fn().mockResolvedValue(boardStatus()),
    });
    const onFocus = vi.fn();
    renderWithQuery(
      <ActionCard
        message={actionMessage("start_short", { session_id: "s1" })}
        client={c}
        onFocus={onFocus}
      />,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    expect(screen.getByText(/Export: exp-1/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "▶ ansehen" }));
    expect(onFocus).toHaveBeenCalledOnce();
  });

  it("stops polling once done — no leaked interval", async () => {
    const getProductionEvents = vi
      .fn()
      .mockResolvedValue({ events: [], next: 0, done: true });
    const c = client({
      getProductionEvents,
      getProductionStatus: vi.fn().mockResolvedValue(boardStatus()),
    });
    renderWithQuery(
      <ActionCard message={actionMessage("start_short", { session_id: "s1" })} client={c} />,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    expect(getProductionEvents).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500 * 3);
    });
    expect(getProductionEvents).toHaveBeenCalledTimes(1);
  });

  it("clears the poll interval on unmount", async () => {
    const getProductionEvents = vi
      .fn()
      .mockResolvedValue({ events: [], next: 0, done: false });
    const c = client({ getProductionEvents });
    const { unmount } = renderWithQuery(
      <ActionCard message={actionMessage("start_short", { session_id: "s1" })} client={c} />,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    expect(getProductionEvents).toHaveBeenCalledTimes(1);

    unmount();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500 * 3);
    });
    expect(getProductionEvents).toHaveBeenCalledTimes(1);
  });
});

describe("ActionCard — job tools (start_overview / import_urls)", () => {
  it("running shows the spinner line", async () => {
    const c = client({ getJob: vi.fn().mockResolvedValue(job({ status: "running" })) });
    renderWithQuery(
      <ActionCard message={actionMessage("import_urls", { job_ids: ["j1"] })} client={c} />,
    );

    await waitFor(() => expect(screen.getByText("⚙ läuft")).toBeTruthy());
  });

  it("done shows the success line", async () => {
    const c = client({ getJob: vi.fn().mockResolvedValue(job({ status: "succeeded" })) });
    renderWithQuery(
      <ActionCard message={actionMessage("start_overview", { job_id: "j1" })} client={c} />,
    );

    await waitFor(() => expect(screen.getByText("✓ fertig")).toBeTruthy());
  });

  it("failed import shows the reason", async () => {
    const c = client({
      getJob: vi.fn().mockResolvedValue(
        job({ status: "failed", error_json: JSON.stringify({ error: "Video nicht gefunden" }) }),
      ),
    });
    renderWithQuery(
      <ActionCard message={actionMessage("import_urls", { job_ids: ["j1"] })} client={c} />,
    );

    await waitFor(() =>
      expect(screen.getByText("✗ fehlgeschlagen: Video nicht gefunden")).toBeTruthy(),
    );
  });

  it("tracks the first job id when a URL import fanned out to several", async () => {
    const getJob = vi.fn().mockResolvedValue(job({ status: "running" }));
    const c = client({ getJob });
    renderWithQuery(
      <ActionCard
        message={actionMessage("import_urls", { job_ids: ["job-a", "job-b"] })}
        client={c}
      />,
    );

    await waitFor(() => expect(getJob).toHaveBeenCalledWith("job-a"));
  });
});
