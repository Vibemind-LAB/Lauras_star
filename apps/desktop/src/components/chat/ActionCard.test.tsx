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

/** A `review_transcript` action card (Gate A) — `content.payload` shape from
 * `_review_transcript_content` (services/local-api/src/laura/chat/executor.py). */
function reviewTranscriptMessage(payload: Record<string, unknown>): ChatMessage {
  return {
    id: "m2",
    conversation_id: "c1",
    seq: 4,
    role: "assistant",
    kind: "action",
    content: { tool: "review_transcript", refs: { asset_id: "a1" }, outcome: "done", payload },
    created_at: "2026-01-01T00:00:00Z",
  };
}

function reviewSegment(
  index: number,
  text: string,
  start_s = index,
): { index: number; id: string; start_s: number; text: string } {
  return { index, id: `seg-${index}`, start_s, text };
}

/** An `action` message with an arbitrary `content` — for exercising malformed/incomplete
 * payloads `reviewTranscriptMessage` can't express (e.g. `payload` missing entirely, rather than
 * present-but-empty). */
function rawActionMessage(content: Record<string, unknown>): ChatMessage {
  return {
    id: "m3",
    conversation_id: "c1",
    seq: 5,
    role: "assistant",
    kind: "action",
    content,
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
      // Unrelated to this test (events rendering), but job_id is present in the fixture like a
      // real action message — give the job-status backstop a resolved value so it does not
      // dangle in "loading" for the whole test.
      getJob: vi.fn().mockResolvedValue(job({ status: "running" })),
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

  // --- job-status backstop (refs.job_id) ---------------------------------------------------
  //
  // The events reader always serves the NEWEST run log for the session: (a) a follow-up's
  // first poll can land on the PREVIOUS run's already-"done" log, and (b) a dead/killed job
  // never writes "done" at all. Both are fixed by cross-checking the tracked job (client.getJob)
  // instead of trusting the events log alone.

  it("events say done but the tracked job is still running: does not finalize, keeps polling the job", async () => {
    const getProductionEvents = vi
      .fn()
      .mockResolvedValue({ events: [], next: 0, done: true });
    const getProductionStatus = vi.fn();
    const getJob = vi.fn().mockResolvedValue(job({ status: "running" }));
    const c = client({ getProductionEvents, getProductionStatus, getJob });
    renderWithQuery(
      <ActionCard
        message={actionMessage("start_short", { session_id: "s1", job_id: "j1" })}
        client={c}
      />,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    // A stale "done" events log must not finalize the card while its own job is still running.
    expect(screen.getByText("⚙ läuft …")).toBeTruthy();
    expect(screen.queryByText(/Export:/)).toBeNull();
    expect(getProductionStatus).not.toHaveBeenCalled();

    const jobCallsSoFar = getJob.mock.calls.length;
    expect(jobCallsSoFar).toBeGreaterThan(0);

    // It keeps polling the job (useJobStatus's own cadence) instead of giving up.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
    });
    expect(getJob.mock.calls.length).toBeGreaterThan(jobCallsSoFar);
  });

  it("a failed (or killed/cancelled) job finalizes as failed independent of the events log, and stops polling", async () => {
    const getProductionEvents = vi
      .fn()
      .mockResolvedValue({ events: [], next: 0, done: false });
    const getProductionStatus = vi.fn();
    const getJob = vi
      .fn()
      .mockResolvedValue(
        job({ status: "failed", error_json: JSON.stringify({ error: "Agent-Team abgestürzt" }) }),
      );
    const c = client({ getProductionEvents, getProductionStatus, getJob });
    renderWithQuery(
      <ActionCard
        message={actionMessage("start_short", { session_id: "s1", job_id: "j1" })}
        client={c}
      />,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(screen.getByText("✗ fehlgeschlagen: Agent-Team abgestürzt")).toBeTruthy();
    expect(getProductionStatus).not.toHaveBeenCalled();

    // Polling has stopped entirely (events never even said "done" — the dead-job case).
    expect(getProductionEvents).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500 * 3);
    });
    expect(getProductionEvents).not.toHaveBeenCalled();
  });

  it("events done + job succeeded finalizes exactly as before (result line)", async () => {
    const getProductionEvents = vi
      .fn()
      .mockResolvedValue({ events: [], next: 0, done: true });
    const getProductionStatus = vi.fn().mockResolvedValue(boardStatus());
    const getJob = vi.fn().mockResolvedValue(job({ status: "succeeded" }));
    const c = client({ getProductionEvents, getProductionStatus, getJob });
    renderWithQuery(
      <ActionCard
        message={actionMessage("start_short", { session_id: "s1", job_id: "j1" })}
        client={c}
      />,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    expect(getProductionStatus).toHaveBeenCalledWith("s1");
    expect(screen.getByText(/Export: exp-1/)).toBeTruthy();
  });

  it("a null job_id (old message) behaves exactly as before: done finalizes immediately, no job cross-check", async () => {
    const getProductionEvents = vi
      .fn()
      .mockResolvedValue({ events: [], next: 0, done: true });
    const getProductionStatus = vi.fn().mockResolvedValue(boardStatus());
    const getJob = vi.fn();
    const c = client({ getProductionEvents, getProductionStatus, getJob });
    renderWithQuery(
      <ActionCard message={actionMessage("start_short", { session_id: "s1" })} client={c} />,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    expect(screen.getByText(/Export: exp-1/)).toBeTruthy();
    expect(getJob).not.toHaveBeenCalled();
  });
});

describe("ActionCard — Gate B (script checkpoint)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("a pending script_gate shows the checkpoint block with both lines and no '▶ ansehen'", async () => {
    const getProductionEvents = vi
      .fn()
      .mockResolvedValue({ events: [], next: 0, done: true });
    const getProductionStatus = vi.fn().mockResolvedValue(
      boardStatus({
        script_gate: { enabled: true, approved: false, pending: true },
        script_lines: [
          { chapter: 1, scene_number: 1, text: "Erste Zeile" },
          { chapter: 1, scene_number: 2, text: "Zweite Zeile" },
        ],
      }),
    );
    const getJob = vi.fn().mockResolvedValue(job({ status: "succeeded" }));
    const c = client({ getProductionEvents, getProductionStatus, getJob });
    renderWithQuery(
      <ActionCard
        message={actionMessage("start_short", { session_id: "s1", job_id: "j1" })}
        client={c}
      />,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    expect(screen.getByText("📝 Sprechertext wartet auf Freigabe")).toBeTruthy();
    expect(screen.getByText(/Erste Zeile/)).toBeTruthy();
    expect(screen.getByText(/Zweite Zeile/)).toBeTruthy();
    // Priority over the ordinary result row — even though the fixture job still carries an
    // export id from a prior run, the pending gate must win, not the export line/button.
    expect(screen.queryByText(/Export:/)).toBeNull();
    expect(screen.queryByRole("button", { name: "▶ ansehen" })).toBeNull();
  });

  it("an approved (non-pending) script_gate falls through to the normal result line", async () => {
    const getProductionEvents = vi
      .fn()
      .mockResolvedValue({ events: [], next: 0, done: true });
    const getProductionStatus = vi.fn().mockResolvedValue(
      boardStatus({ script_gate: { enabled: true, approved: true, pending: false } }),
    );
    const getJob = vi.fn().mockResolvedValue(job({ status: "succeeded" }));
    const c = client({ getProductionEvents, getProductionStatus, getJob });
    renderWithQuery(
      <ActionCard
        message={actionMessage("start_short", { session_id: "s1", job_id: "j1" })}
        client={c}
      />,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    expect(screen.getByText(/Export: exp-1/)).toBeTruthy();
    expect(screen.queryByText("📝 Sprechertext wartet auf Freigabe")).toBeNull();
  });

  it("a script_gate without an explicit 'pending' field falls back to enabled && !approved", async () => {
    const getProductionEvents = vi
      .fn()
      .mockResolvedValue({ events: [], next: 0, done: true });
    // A backend payload predating (or otherwise missing) the `pending` flag — `narrowPendingScript`
    // must still recognize this as pending from `enabled && !approved` alone. `ChatMessage.content`
    // is `Record<string, unknown>` at the API boundary, so an incomplete real-world payload is a
    // legitimate case to simulate even though the current type requires all three fields.
    const gate = { enabled: true, approved: false } as unknown as NonNullable<
      ProductionBoardStatus["script_gate"]
    >;
    const getProductionStatus = vi.fn().mockResolvedValue(
      boardStatus({
        script_gate: gate,
        script_lines: [{ chapter: 1, scene_number: 1, text: "Nur eine Zeile" }],
      }),
    );
    const getJob = vi.fn().mockResolvedValue(job({ status: "succeeded" }));
    const c = client({ getProductionEvents, getProductionStatus, getJob });
    renderWithQuery(
      <ActionCard
        message={actionMessage("start_short", { session_id: "s1", job_id: "j1" })}
        client={c}
      />,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    expect(screen.getByText("📝 Sprechertext wartet auf Freigabe")).toBeTruthy();
    expect(screen.getByText(/Nur eine Zeile/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "▶ ansehen" })).toBeNull();
  });
});

describe("ActionCard — review_transcript (Gate A)", () => {
  it("renders the segment list and an 'unbestätigt' badge when not yet confirmed", () => {
    const c = client();
    renderWithQuery(
      <ActionCard
        message={reviewTranscriptMessage({
          confirmed_at: null,
          segments: [
            reviewSegment(1, "Erstes Segment"),
            reviewSegment(2, "Zweites Segment"),
            reviewSegment(3, "Drittes Segment"),
          ],
          total: 3,
        })}
        client={c}
      />,
    );

    expect(screen.getByText("Transkript prüfen")).toBeTruthy();
    expect(screen.getByText("unbestätigt")).toBeTruthy();
    expect(screen.getByText(/#1 · 1s · Erstes Segment/)).toBeTruthy();
    expect(screen.getByText(/#2 · 2s · Zweites Segment/)).toBeTruthy();
    expect(screen.getByText(/#3 · 3s · Drittes Segment/)).toBeTruthy();
    expect(screen.getByText(/Korrigieren per Nachricht/)).toBeTruthy();
  });

  it("shows a '✓ bestätigt' badge once confirmed_at is set", () => {
    const c = client();
    renderWithQuery(
      <ActionCard
        message={reviewTranscriptMessage({
          confirmed_at: "2026-01-01T00:00:00Z",
          segments: [reviewSegment(1, "Erstes Segment")],
          total: 1,
        })}
        client={c}
      />,
    );

    expect(screen.getByText("✓ bestätigt")).toBeTruthy();
    expect(screen.queryByText("unbestätigt")).toBeNull();
  });

  it("shows a remainder line when total exceeds the shown segments", () => {
    const c = client();
    renderWithQuery(
      <ActionCard
        message={reviewTranscriptMessage({
          confirmed_at: null,
          segments: [reviewSegment(1, "Erstes Segment"), reviewSegment(2, "Zweites Segment")],
          total: 7,
        })}
        client={c}
      />,
    );

    expect(screen.getByText("… und 5 weitere Segmente")).toBeTruthy();
  });

  it("shows no remainder line when total matches the shown segments", () => {
    const c = client();
    renderWithQuery(
      <ActionCard
        message={reviewTranscriptMessage({
          confirmed_at: null,
          segments: [reviewSegment(1, "Erstes Segment")],
          total: 1,
        })}
        client={c}
      />,
    );

    expect(screen.queryByText(/weitere Segmente/)).toBeNull();
  });

  // --- malformed/incomplete payloads (defensive narrowing in narrowReviewTranscriptPayload) ---
  //
  // `content` is `Record<string, unknown>` at the API boundary — none of these shapes are ruled
  // out by the type system, only by the narrowing function itself. Each case must render a
  // degraded-but-safe card (heading still present, no crash), never throw.

  it("payload missing entirely: renders the heading, unconfirmed badge, empty list", () => {
    const c = client();
    renderWithQuery(
      <ActionCard
        message={rawActionMessage({ tool: "review_transcript", refs: {}, outcome: "done" })}
        client={c}
      />,
    );

    expect(screen.getByText("Transkript prüfen")).toBeTruthy();
    expect(screen.getByText("unbestätigt")).toBeTruthy();
    expect(screen.queryByText(/#\d+ · /)).toBeNull();
    expect(screen.queryByText(/weitere Segmente/)).toBeNull();
    expect(screen.getByText(/Korrigieren per Nachricht/)).toBeTruthy();
  });

  it("segments is not an array (e.g. a string): renders an empty list, not a crash", () => {
    const c = client();
    renderWithQuery(
      <ActionCard
        message={reviewTranscriptMessage({
          confirmed_at: null,
          segments: "oops, not an array",
          total: 3,
        })}
        client={c}
      />,
    );

    expect(screen.getByText("Transkript prüfen")).toBeTruthy();
    expect(screen.getByText("unbestätigt")).toBeTruthy();
    expect(screen.queryByText(/#\d+ · /)).toBeNull();
  });

  it("a segment entry that isn't an object, or is missing fields: degrades per-field, no crash", () => {
    const c = client();
    renderWithQuery(
      <ActionCard
        message={reviewTranscriptMessage({
          confirmed_at: null,
          segments: ["not-an-object", { text: "Nur Text vorhanden, sonst nichts" }],
          total: 2,
        })}
        client={c}
      />,
    );

    expect(screen.getByText("Transkript prüfen")).toBeTruthy();
    // First entry: not an object at all — every field falls back (index 1, empty text).
    expect(screen.getByText(/#1 · 0s ·/)).toBeTruthy();
    // Second entry: an object, but only `text` is present — the rest fall back, text survives.
    expect(screen.getByText(/#2 · 0s · Nur Text vorhanden, sonst nichts/)).toBeTruthy();
  });

  it("total missing: falls back to the shown segment count, no remainder line", () => {
    const c = client();
    renderWithQuery(
      <ActionCard
        message={reviewTranscriptMessage({
          confirmed_at: null,
          segments: [reviewSegment(1, "Eins"), reviewSegment(2, "Zwei")],
        })}
        client={c}
      />,
    );

    expect(screen.getByText("Transkript prüfen")).toBeTruthy();
    expect(screen.getByText(/#1 · 1s · Eins/)).toBeTruthy();
    expect(screen.getByText(/#2 · 2s · Zwei/)).toBeTruthy();
    expect(screen.queryByText(/weitere Segmente/)).toBeNull();
  });

  it("total is not a number: falls back to the shown segment count, no remainder line", () => {
    const c = client();
    renderWithQuery(
      <ActionCard
        message={reviewTranscriptMessage({
          confirmed_at: null,
          segments: [reviewSegment(1, "Eins")],
          total: "viele",
        })}
        client={c}
      />,
    );

    expect(screen.getByText("Transkript prüfen")).toBeTruthy();
    expect(screen.getByText(/#1 · 1s · Eins/)).toBeTruthy();
    expect(screen.queryByText(/weitere Segmente/)).toBeNull();
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
