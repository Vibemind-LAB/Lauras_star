import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentEvent, JobStatus, LauraClient, ProductionBoardStatus } from "../api";
import { ChatPanel, pickHighlights } from "./ChatPanel";

// v2 session-mode tests write a `laura.production.<assetId>` localStorage entry via
// useProductionSession's start()/sendMessage(). Clear it before every test (v1 tests never touch
// localStorage, so this is a no-op for them) so no session leaks across tests reusing assetId "a1".
beforeEach(() => {
  window.localStorage.clear();
});

function mockClient(streamAutoShort: ReturnType<typeof vi.fn>): LauraClient {
  return { streamAutoShort } as unknown as LauraClient;
}

function renderWithEvents(events: AgentEvent[]): ReturnType<typeof vi.fn> {
  const streamAutoShort = vi.fn(
    (_assetId: string, _req: { topic: string }, onEvent: (e: AgentEvent) => void) => {
      for (const e of events) onEvent(e);
      return Promise.resolve();
    },
  );
  render(<ChatPanel client={mockClient(streamAutoShort)} assetId="a1" />);
  fireEvent.change(screen.getByLabelText("Anfrage"), { target: { value: "Katzen" } });
  fireEvent.click(screen.getByRole("button", { name: "Los" }));
  return streamAutoShort;
}

const DONE_OK: AgentEvent = {
  type: "done",
  ok: true,
  stage: "A",
  team: "magentic",
  weak: false,
  escalated: false,
  summary: "",
};

describe("ChatPanel", () => {
  it("streams a request and renders the agent events + user bubble", async () => {
    const streamAutoShort = renderWithEvents([
      { type: "stage", stage: "A", team: "magentic" },
      { type: "agent", agent: "scout", text: "suche Momente" },
      DONE_OK,
    ]);

    await waitFor(() => expect(streamAutoShort).toHaveBeenCalledTimes(1));
    expect(streamAutoShort.mock.calls[0][0]).toBe("a1");
    expect(streamAutoShort.mock.calls[0][1]).toEqual({ topic: "Katzen" });
    expect(screen.getByText(/Katzen/)).toBeTruthy();
    expect(screen.getByText(/Scout/)).toBeTruthy();
    expect(screen.getByText(/suche Momente/)).toBeTruthy();
    expect(screen.getByText(/Short fertig/)).toBeTruthy();
  });

  it("resets running when the stream ends without a terminal event", async () => {
    // Abrupt end: the stream promise resolves without ever emitting done/error.
    let resolve!: () => void;
    const streamAutoShort = vi.fn(() => new Promise<void>((r) => (resolve = r)));
    render(<ChatPanel client={mockClient(streamAutoShort)} assetId="a1" />);

    fireEvent.change(screen.getByLabelText("Anfrage"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Los" }));
    expect((screen.getByLabelText("Anfrage") as HTMLInputElement).disabled).toBe(true);

    resolve();
    await waitFor(() =>
      expect((screen.getByLabelText("Anfrage") as HTMLInputElement).disabled).toBe(false),
    );
  });

  it("forwards every event to onEvent (for live view refresh)", async () => {
    const streamAutoShort = vi.fn(
      (_assetId: string, _req: { topic: string }, onEvent: (e: AgentEvent) => void) => {
        onEvent({ type: "artifact", kind: "roughcut", id: "t1" });
        onEvent(DONE_OK);
        return Promise.resolve();
      },
    );
    const onEvent = vi.fn();
    render(<ChatPanel client={mockClient(streamAutoShort)} assetId="a1" onEvent={onEvent} />);

    fireEvent.change(screen.getByLabelText("Anfrage"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Los" }));

    await waitFor(() => expect(onEvent).toHaveBeenCalledTimes(2));
    expect(onEvent.mock.calls[0][0]).toEqual({ type: "artifact", kind: "roughcut", id: "t1" });
  });

  it("disables the input and send button when no asset is selected", () => {
    render(<ChatPanel client={mockClient(vi.fn())} assetId={null} />);
    expect((screen.getByLabelText("Anfrage") as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Los" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("surfaces the key facts of a tool result and hides the raw dump behind details", async () => {
    renderWithEvents([
      {
        type: "tool_result",
        tool: "render_short",
        ok: true,
        summary: "{'ok': True, 'export_id': 'abc123', 'status': 'ready', 'path': '/x.mp4'}",
      },
      DONE_OK,
    ]);

    await waitFor(() => expect(screen.getByText(/export_id=abc123/)).toBeTruthy());
    expect(screen.getByText(/status=ready/)).toBeTruthy();
    // The full raw summary stays available (collapsed).
    expect(screen.getByText(/'path': '\/x\.mp4'/)).toBeTruthy();
  });

  it("renders a SKIP reply as one quiet line instead of a bubble", async () => {
    renderWithEvents([
      { type: "agent", agent: "transcript_master", text: "SKIP." },
      DONE_OK,
    ]);
    await waitFor(() => expect(screen.getByText(/überspringt/)).toBeTruthy());
  });

  it("collapses the long task echo (agent 'user') behind a summary", async () => {
    renderWithEvents([
      { type: "agent", agent: "user", text: "Create a ~60s vertical short about ..." },
      DONE_OK,
    ]);
    await waitFor(() => expect(screen.getByText("📋 Auftrag ans Team")).toBeTruthy());
  });

  it("clamps long agent prose behind a 'mehr anzeigen' toggle", async () => {
    const long = `Anfang ${"blah ".repeat(120)}ENDE`;
    renderWithEvents([{ type: "agent", agent: "director", text: long }, DONE_OK]);

    await waitFor(() => expect(screen.getByText(/mehr anzeigen/)).toBeTruthy());
    expect(screen.queryByText(/ENDE/)).toBeNull();
    fireEvent.click(screen.getByText(/mehr anzeigen/));
    expect(screen.getByText(/ENDE/)).toBeTruthy();
    expect(screen.getByText(/weniger anzeigen/)).toBeTruthy();
  });

  it("shows a weak verdict as its own tone, not as plain success", async () => {
    renderWithEvents([
      {
        type: "done",
        ok: true,
        stage: "A",
        team: "graph",
        weak: true,
        escalated: false,
        summary: "qa said weak",
      },
    ]);
    await waitFor(() => expect(screen.getByText(/QA meldet Schwächen/)).toBeTruthy());
    expect(screen.queryByText(/✓ Short fertig/)).toBeNull();
  });
});

describe("pickHighlights", () => {
  it("extracts known keys from a python-dict summary", () => {
    const facts = pickHighlights(
      "{'ok': True, 'export_id': 'e9', 'count': 575, 'reason': 'no candidates'}",
    );
    expect(facts).toContain("export_id=e9");
    expect(facts).toContain("count=575");
    expect(facts).toContain("reason=no candidates");
  });

  it("returns an empty string for unknown shapes and skips None values", () => {
    expect(pickHighlights("plain text")).toBe("");
    expect(pickHighlights("{'error': None}")).toBe("");
  });
});

// -------------------------------------------------------------------------------------------
// Session (v2) mode. Driven through the real useProductionSession hook via a mocked client —
// same pattern useProductionSession.test.ts uses (fake timers + vi.fn() promises on the client)
// — never by mocking the hook module itself.
// -------------------------------------------------------------------------------------------

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

function boardStatus(overrides: Partial<ProductionBoardStatus> = {}): ProductionBoardStatus {
  return {
    board_ready: true,
    job: null,
    meta: {
      session_id: "s1",
      asset_id: "a1",
      created_utc: "2026-01-01T00:00:00Z",
      task: "make a short",
      format: "insta",
      target_seconds: 30,
      status: "active",
    },
    scene_reviews: { count: 0, scenes: [], degraded_count: 0, degraded_scenes: [] },
    artifacts: {
      storyline: { version: null, archived_versions: [] },
      script: { version: null, archived_versions: [] },
      voice: { version: null, archived_versions: [] },
      cutlist: { version: null, archived_versions: [] },
      contact_sheet: { version: null, archived_versions: [] },
      render_report: { version: null, archived_versions: [] },
      qa_report: { version: null, archived_versions: [] },
    },
    resume_point: "script",
    ...overrides,
  };
}

function mockSessionClient(overrides: Partial<LauraClient> = {}): LauraClient {
  return {
    streamAutoShort: vi.fn(),
    createProduction: vi.fn().mockResolvedValue({ session_id: "s1", job_id: "j1" }),
    sendProductionMessage: vi.fn().mockResolvedValue({ session_id: "s1", job_id: "j2" }),
    getProductionStatus: vi.fn().mockResolvedValue(boardStatus()),
    getJob: vi.fn().mockResolvedValue(job({ status: "running" })),
    ...overrides,
  } as unknown as LauraClient;
}

// Flushes a fire-and-forget start()/sendMessage() promise chain triggered from a click handler,
// without advancing real time — mirrors useProductionSession.test.ts's own flush() helper.
async function flushSession(): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

describe("ChatPanel session mode (v2)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("header toggle switches from the v1 stream UI to the session (v2) UI", () => {
    render(<ChatPanel client={mockSessionClient()} assetId="a1" />);
    expect(screen.getByLabelText("Anfrage")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Session (v2)" }));

    expect(screen.queryByLabelText("Anfrage")).toBeNull();
    expect(screen.getByLabelText("Sitzungsauftrag")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Start" })).toBeTruthy();
  });

  it("running renders the resume_point and board chips (reviews + artifact versions)", async () => {
    const status = boardStatus({
      scene_reviews: { count: 5, scenes: [1, 2, 3, 4, 5], degraded_count: 0, degraded_scenes: [] },
      artifacts: {
        ...boardStatus().artifacts,
        storyline: { version: 2, archived_versions: [1] },
        script: { version: 1, archived_versions: [] },
      },
      resume_point: "script",
    });
    const client = mockSessionClient({
      getJob: vi.fn().mockResolvedValue(job({ status: "running" })),
      getProductionStatus: vi.fn().mockResolvedValue(status),
    });
    render(<ChatPanel client={client} assetId="a1" />);
    fireEvent.click(screen.getByRole("button", { name: "Session (v2)" }));
    fireEvent.change(screen.getByLabelText("Sitzungsauftrag"), { target: { value: "Katzen" } });
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    await flushSession();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    expect(screen.getByText("⚙ script …")).toBeTruthy();
    expect(screen.getByText("🎬 5")).toBeTruthy();
    expect(screen.getByText("storyline v2")).toBeTruthy();
    expect(screen.getByText("script v1")).toBeTruthy();
  });

  it("chips surface degradation, staleness and the contact sheet — not just presence", async () => {
    // The API has reported degraded_count, stale and checks_ok since Portion 20; the panel
    // showed none of them, so a board with zero visual analysis looked identical to a fully
    // reviewed one — the same invisibility the backend fixes were built to end.
    const status = boardStatus({
      scene_reviews: { count: 6, scenes: [1, 2, 3, 4, 5, 6], degraded_count: 2, degraded_scenes: [3, 5] },
      artifacts: {
        ...boardStatus().artifacts,
        contact_sheet: { version: 3, archived_versions: [1, 2] },
        render_report: {
          version: 2,
          archived_versions: [1],
          stale: true,
          checks_ok: false,
          failed_checks: ["voice_fits"],
        },
      },
      resume_point: "qa_report",
    });
    const client = mockSessionClient({
      getJob: vi.fn().mockResolvedValue(job({ status: "running" })),
      getProductionStatus: vi.fn().mockResolvedValue(status),
    });
    render(<ChatPanel client={client} assetId="a1" />);
    fireEvent.click(screen.getByRole("button", { name: "Session (v2)" }));
    fireEvent.change(screen.getByLabelText("Sitzungsauftrag"), { target: { value: "Katzen" } });
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    await flushSession();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    expect(screen.getByText("🎬 6 (2⚠)")).toBeTruthy();
    expect(screen.getByText("Bogen v3")).toBeTruthy();
    const renderChip = screen.getByText(/Export v2/);
    expect(renderChip.textContent).toContain("⚠");
    expect(renderChip.getAttribute("title")).toContain("voice_fits");
    expect(renderChip.getAttribute("title")).toContain("älteren Skript");
  });

  it("healthy chips carry no warning markers", async () => {
    const status = boardStatus({
      scene_reviews: { count: 6, scenes: [1, 2, 3, 4, 5, 6], degraded_count: 0, degraded_scenes: [] },
      artifacts: {
        ...boardStatus().artifacts,
        render_report: {
          version: 1,
          archived_versions: [],
          stale: false,
          checks_ok: true,
          failed_checks: [],
        },
      },
    });
    const client = mockSessionClient({
      getJob: vi.fn().mockResolvedValue(job({ status: "running" })),
      getProductionStatus: vi.fn().mockResolvedValue(status),
    });
    render(<ChatPanel client={client} assetId="a1" />);
    fireEvent.click(screen.getByRole("button", { name: "Session (v2)" }));
    fireEvent.change(screen.getByLabelText("Sitzungsauftrag"), { target: { value: "Katzen" } });
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    await flushSession();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    expect(screen.getByText("🎬 6")).toBeTruthy();
    expect(screen.getByText("Export v1")).toBeTruthy();
  });

  it("shows a restored chip with the count and a label tooltip when a resume restored artifacts", async () => {
    const status = boardStatus({
      job: {
        id: "j1",
        status: "succeeded",
        attempt: 1,
        updated_at: "2026-01-01T00:00:00Z",
        lease_expires_at: null,
        finished_at: "2026-01-01T00:00:05Z",
        restored: ["voice", "contact_sheet"],
      },
    });
    const client = mockSessionClient({
      getJob: vi.fn().mockResolvedValue(job({ status: "running" })),
      getProductionStatus: vi.fn().mockResolvedValue(status),
    });
    render(<ChatPanel client={client} assetId="a1" />);
    fireEvent.click(screen.getByRole("button", { name: "Session (v2)" }));
    fireEvent.change(screen.getByLabelText("Sitzungsauftrag"), { target: { value: "Katzen" } });
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    await flushSession();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    const chip = screen.getByText("♻️ 2");
    // "contact_sheet" reuses the SAME label ("Bogen") the other chips already use — a second
    // mapping here would drift the moment one of them changes.
    expect(chip.getAttribute("title")).toBe("Wiederhergestellt: voice, Bogen");
  });

  it("renders no restored chip when restored is empty or absent (old backends included)", async () => {
    const emptyRestored = boardStatus({
      job: {
        id: "j1",
        status: "succeeded",
        attempt: 1,
        updated_at: "2026-01-01T00:00:00Z",
        lease_expires_at: null,
        finished_at: "2026-01-01T00:00:05Z",
        restored: [],
      },
    });
    const client = mockSessionClient({
      getJob: vi.fn().mockResolvedValue(job({ status: "running" })),
      getProductionStatus: vi.fn().mockResolvedValue(emptyRestored),
    });
    render(<ChatPanel client={client} assetId="a1" />);
    fireEvent.click(screen.getByRole("button", { name: "Session (v2)" }));
    fireEvent.change(screen.getByLabelText("Sitzungsauftrag"), { target: { value: "Katzen" } });
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    await flushSession();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    expect(screen.queryByText(/♻️/)).toBeNull();
  });

  it("renders no restored chip when the job carries no restored key at all (pre-restore backend)", async () => {
    // boardStatus()'s default job is `null`, and none of the earlier chip tests set `restored` —
    // an older backend simply omits the key. Reuses the plain "healthy" board from above.
    const status = boardStatus({
      scene_reviews: { count: 6, scenes: [1, 2, 3, 4, 5, 6], degraded_count: 0, degraded_scenes: [] },
    });
    const client = mockSessionClient({
      getJob: vi.fn().mockResolvedValue(job({ status: "running" })),
      getProductionStatus: vi.fn().mockResolvedValue(status),
    });
    render(<ChatPanel client={client} assetId="a1" />);
    fireEvent.click(screen.getByRole("button", { name: "Session (v2)" }));
    fireEvent.change(screen.getByLabelText("Sitzungsauftrag"), { target: { value: "Katzen" } });
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    await flushSession();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    expect(screen.getByText("🎬 6")).toBeTruthy();
    expect(screen.queryByText(/♻️/)).toBeNull();
  });

  it("running survives the pre-board window: pending status renders the job state, no crash", async () => {
    // Review finding: GET /production/{sid} now answers 200 {job, board_ready:false} before a
    // board exists (it used to 404). Every new session passes through this window, and the
    // chips renderer dereferenced scene_reviews on that shape — a TypeError on the first poll.
    const client = mockSessionClient({
      getJob: vi.fn().mockResolvedValue(job({ status: "running" })),
      getProductionStatus: vi.fn().mockResolvedValue({
        board_ready: false,
        session_id: "s1",
        job: {
          id: "j1",
          status: "queued",
          attempt: 0,
          updated_at: "2026-01-01T00:00:00Z",
          lease_expires_at: null,
          finished_at: null,
        },
      }),
    });
    render(<ChatPanel client={client} assetId="a1" />);
    fireEvent.click(screen.getByRole("button", { name: "Session (v2)" }));
    fireEvent.change(screen.getByLabelText("Sitzungsauftrag"), { target: { value: "Katzen" } });
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    await flushSession();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    expect(screen.getByText("⚙ queued …")).toBeTruthy();
  });

  it("done shows a follow-up input; sending it calls sendMessage with the typed text", async () => {
    const client = mockSessionClient({
      getJob: vi.fn().mockResolvedValue(
        job({ status: "succeeded", result_json: '{"ok":true,"weak":false,"export_id":"e9"}' }),
      ),
    });
    render(<ChatPanel client={client} assetId="a1" />);
    fireEvent.click(screen.getByRole("button", { name: "Session (v2)" }));
    fireEvent.change(screen.getByLabelText("Sitzungsauftrag"), { target: { value: "Katzen" } });
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    await flushSession();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    expect(screen.getByText(/Session fertig/)).toBeTruthy();
    expect(screen.getByText(/Export: e9/)).toBeTruthy();

    const followUp = screen.getByLabelText("Folgeanfrage");
    fireEvent.change(followUp, { target: { value: "Kapitel 2 andere Szene" } });
    fireEvent.click(screen.getByRole("button", { name: "Senden" }));
    await flushSession();

    expect(client.sendProductionMessage).toHaveBeenCalledWith("s1", "Kapitel 2 andere Szene");
  });

  it("error shows the failure message and a reset button", async () => {
    const client = mockSessionClient({
      createProduction: vi.fn().mockRejectedValue(new Error("boom")),
    });
    render(<ChatPanel client={client} assetId="a1" />);
    fireEvent.click(screen.getByRole("button", { name: "Session (v2)" }));
    fireEvent.change(screen.getByLabelText("Sitzungsauftrag"), { target: { value: "Katzen" } });
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    await flushSession();

    expect(screen.getByText(/boom/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Zurücksetzen" }));
    expect(screen.getByLabelText("Sitzungsauftrag")).toBeTruthy();
  });
});

// -------------------------------------------------------------------------------------------
// Revert dropdown on artifact chips (RU Task 3). Only artifact-slot chips with a non-empty
// `archived_versions` become interactive — everything else stays the plain pill covered above.
//
// The endpoint 409s on a queued/running job (see 2026-07-21-revert-ui-design.md), so the button
// is reachable ONLY once the run has landed (done/error) — never while "running". The tests below
// therefore drive the job to "succeeded" (via `startFinishedSession`) for every flow that clicks
// the revert button, and cover "running" separately to pin the suppression.
// -------------------------------------------------------------------------------------------

describe("ChatPanel session mode (v2) — revert dropdown", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  async function startSession(client: LauraClient): Promise<void> {
    render(<ChatPanel client={client} assetId="a1" />);
    fireEvent.click(screen.getByRole("button", { name: "Session (v2)" }));
    fireEvent.change(screen.getByLabelText("Sitzungsauftrag"), { target: { value: "Katzen" } });
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    await flushSession();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
  }

  /** Starts the session with a job that resolves as "succeeded" on the very first poll, landing
   * in the "done" phase — the finished state the revert button is actually reachable in. */
  async function startFinishedSession(client: LauraClient): Promise<void> {
    await startSession(client);
    expect(screen.getByText(/Session fertig/)).toBeTruthy();
  }

  /** A session client whose job is already "succeeded" on the first poll, so the session lands
   * in "done" carrying `status` as its final board snapshot. */
  function finishedClient(
    status: ProductionBoardStatus,
    overrides: Partial<LauraClient> = {},
  ): LauraClient {
    return mockSessionClient({
      getJob: vi
        .fn()
        .mockResolvedValue(job({ status: "succeeded", result_json: '{"ok":true,"weak":false}' })),
      getProductionStatus: vi.fn().mockResolvedValue(status),
      ...overrides,
    });
  }

  /** Starts the session with a job that resolves as "failed" on the very first poll, landing in
   * the "error" phase — the other finished state the revert button is reachable in (a failed run
   * can still carry a board with archived versions worth reverting from). */
  async function startFailedSession(client: LauraClient): Promise<void> {
    await startSession(client);
    expect(screen.getByText(/Produktion fehlgeschlagen/)).toBeTruthy();
  }

  /** A session client whose job is already "failed" on the first poll, so the session lands in
   * "error". `status` is the board snapshot `getProductionStatus` resolves with — `null` models a
   * run that died before any board ever existed (the endpoint 404s, and checkOnce swallows that,
   * leaving `state.status` null). */
  function failedClient(
    status: ProductionBoardStatus | null,
    overrides: Partial<LauraClient> = {},
  ): LauraClient {
    return mockSessionClient({
      getJob: vi.fn().mockResolvedValue(job({ status: "failed" })),
      getProductionStatus:
        status === null
          ? vi.fn().mockRejectedValue(new Error("404: not found"))
          : vi.fn().mockResolvedValue(status),
      ...overrides,
    });
  }

  it("running renders chips but suppresses the revert button, even with archived versions", async () => {
    const status = boardStatus({
      artifacts: {
        ...boardStatus().artifacts,
        cutlist: { version: 3, archived_versions: [1, 2] },
      },
    });
    const client = mockSessionClient({
      getJob: vi.fn().mockResolvedValue(job({ status: "running" })),
      getProductionStatus: vi.fn().mockResolvedValue(status),
      revertProduction: vi.fn(),
    });
    await startSession(client);

    // The chip is visible (chips stay up throughout the run) …
    const chip = screen.getByText("cutlist v3");
    // … but as a plain read-only pill, not the revert-capable button variant.
    expect(chip.tagName).toBe("SPAN");
    expect(screen.queryByRole("button", { name: /cutlist v3/ })).toBeNull();
  });

  it("finished phase: a chip with archived versions renders a button; picking v1 + confirming reverts to it", async () => {
    const status = boardStatus({
      artifacts: {
        ...boardStatus().artifacts,
        cutlist: { version: 3, archived_versions: [1, 2] },
      },
    });
    const revertProduction = vi.fn().mockResolvedValue({
      ok: true,
      artifact: "cutlist",
      version: 1,
      invalidated: [],
      restored: [],
      status,
    });
    const client = finishedClient(status, { revertProduction });
    await startFinishedSession(client);

    fireEvent.click(screen.getByRole("button", { name: /cutlist v3/ }));
    expect(screen.getByRole("button", { name: "v1" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "v2" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "v1" }));
    fireEvent.click(screen.getByRole("button", { name: "Zurückdrehen" }));
    await flushSession();

    expect(revertProduction).toHaveBeenCalledWith("s1", "cutlist", 1);
  });

  it("finished phase: re-renders chips from the revert response and shows the restored hint", async () => {
    const initialStatus = boardStatus({
      artifacts: {
        ...boardStatus().artifacts,
        cutlist: { version: 3, archived_versions: [1, 2] },
      },
    });
    const revertedStatus = boardStatus({
      artifacts: {
        ...boardStatus().artifacts,
        cutlist: { version: 1, archived_versions: [] },
        contact_sheet: { version: 1, archived_versions: [] },
      },
      resume_point: "contact_sheet",
    });
    const revertProduction = vi.fn().mockResolvedValue({
      ok: true,
      artifact: "cutlist",
      version: 1,
      invalidated: ["contact_sheet"],
      restored: ["contact_sheet"],
      status: revertedStatus,
    });
    const client = finishedClient(initialStatus, { revertProduction });
    await startFinishedSession(client);

    fireEvent.click(screen.getByRole("button", { name: /cutlist v3/ }));
    fireEvent.click(screen.getByRole("button", { name: "v1" }));
    fireEvent.click(screen.getByRole("button", { name: "Zurückdrehen" }));
    await flushSession();

    expect(screen.getByText("cutlist v1")).toBeTruthy();
    expect(screen.getByText("Bogen v1")).toBeTruthy();
    expect(screen.getByText(/♻️ Wiederhergestellt: Bogen/)).toBeTruthy();
  });

  it("finished phase: a chip with no archived versions stays a plain chip (no button)", async () => {
    const status = boardStatus({
      artifacts: {
        ...boardStatus().artifacts,
        script: { version: 1, archived_versions: [] },
      },
    });
    const client = finishedClient(status, { revertProduction: vi.fn() });
    await startFinishedSession(client);

    const chip = screen.getByText("script v1");
    expect(chip.tagName).toBe("SPAN");
    expect(screen.queryByRole("button", { name: /script v1/ })).toBeNull();
  });

  it("finished phase: a 409 (run in progress) revert response shows the 'Lauf aktiv' hint", async () => {
    const status = boardStatus({
      artifacts: {
        ...boardStatus().artifacts,
        cutlist: { version: 3, archived_versions: [1, 2] },
      },
    });
    const revertProduction = vi
      .fn()
      .mockRejectedValue(
        new Error('409: {"detail":"run in progress — revert would race the team"}'),
      );
    const client = finishedClient(status, { revertProduction });
    await startFinishedSession(client);

    fireEvent.click(screen.getByRole("button", { name: /cutlist v3/ }));
    fireEvent.click(screen.getByRole("button", { name: "v1" }));
    fireEvent.click(screen.getByRole("button", { name: "Zurückdrehen" }));
    await flushSession();

    expect(screen.getByText(/Lauf aktiv/)).toBeTruthy();
  });

  it("clears the stale revert hint once a new message/run starts", async () => {
    // Review finding: revertHint was only ever overwritten by the next revert call — nothing
    // cleared it when a fresh run began. Since sendMessage() flips phase back to "running" and
    // chips (+ this hint) render there too, an old "♻️ Wiederhergestellt: …" hint could resurface
    // and persist through a run it no longer describes.
    const initialStatus = boardStatus({
      artifacts: {
        ...boardStatus().artifacts,
        cutlist: { version: 3, archived_versions: [1, 2] },
      },
    });
    const revertedStatus = boardStatus({
      artifacts: {
        ...boardStatus().artifacts,
        cutlist: { version: 1, archived_versions: [] },
        contact_sheet: { version: 1, archived_versions: [] },
      },
      resume_point: "contact_sheet",
    });
    const revertProduction = vi.fn().mockResolvedValue({
      ok: true,
      artifact: "cutlist",
      version: 1,
      invalidated: ["contact_sheet"],
      restored: ["contact_sheet"],
      status: revertedStatus,
    });
    const client = finishedClient(initialStatus, {
      revertProduction,
      sendProductionMessage: vi.fn().mockResolvedValue({ session_id: "s1", job_id: "j2" }),
    });
    await startFinishedSession(client);

    fireEvent.click(screen.getByRole("button", { name: /cutlist v3/ }));
    fireEvent.click(screen.getByRole("button", { name: "v1" }));
    fireEvent.click(screen.getByRole("button", { name: "Zurückdrehen" }));
    await flushSession();

    expect(screen.getByText(/♻️ Wiederhergestellt: Bogen/)).toBeTruthy();

    // A new follow-up message starts a new run (sendMessage) — the stale hint from the previous
    // revert must not survive into it.
    fireEvent.change(screen.getByLabelText("Folgeanfrage"), {
      target: { value: "Kapitel 2 andere Szene" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Senden" }));
    await flushSession();

    expect(screen.queryByText(/♻️ Wiederhergestellt/)).toBeNull();
  });

  it("error phase: a chip with archived versions renders a button; confirming reverts to it", async () => {
    // The reachability fix renders the revert affordance in phase "error" too (a failed run can
    // still carry a board with archived versions worth reverting from) — this drives that path
    // end to end, not just "running"/"done".
    const status = boardStatus({
      artifacts: {
        ...boardStatus().artifacts,
        cutlist: { version: 3, archived_versions: [1, 2] },
      },
    });
    const revertProduction = vi.fn().mockResolvedValue({
      ok: true,
      artifact: "cutlist",
      version: 1,
      invalidated: [],
      restored: [],
      status,
    });
    const client = failedClient(status, { revertProduction });
    await startFailedSession(client);

    expect(screen.getByRole("button", { name: /cutlist v3/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /cutlist v3/ }));
    fireEvent.click(screen.getByRole("button", { name: "v1" }));
    fireEvent.click(screen.getByRole("button", { name: "Zurückdrehen" }));
    await flushSession();

    expect(revertProduction).toHaveBeenCalledWith("s1", "cutlist", 1);
  });

  it("error phase: a failed job with no board renders no chips (no crash)", async () => {
    // Subtle case the reviewer named: a job can fail before any board ever existed
    // (getProductionStatus 404s, checkOnce swallows it, `state.status` stays null). Chips must
    // render nothing rather than crash on a null status.
    const client = failedClient(null);
    await startFailedSession(client);

    expect(screen.queryByText(/♻️/)).toBeNull();
    // Nothing chip-wise renders at all: the mode toggle, the follow-up row (input row now shared
    // with "done" — see the reachability fix) and "Zurücksetzen" are still there, but never a
    // version/revert chip button (those are always named "v<number>").
    expect(screen.queryByRole("button", { name: /^v\d/ })).toBeNull();
  });

  it("error phase: a follow-up input can send a new message — not just reset (reachability fix)", async () => {
    // Review finding: the error phase used to render only "Zurücksetzen" — a board healed by an
    // error-phase revert could never be advanced from the UI, even though the backend accepts a
    // follow-up message from a terminal failed job. Drives that path end to end.
    const client = failedClient(boardStatus());
    await startFailedSession(client);

    const followUp = screen.getByLabelText("Folgeanfrage");
    fireEvent.change(followUp, { target: { value: "Nochmal versuchen" } });
    fireEvent.click(screen.getByRole("button", { name: "Senden" }));
    await flushSession();

    expect(client.sendProductionMessage).toHaveBeenCalledWith("s1", "Nochmal versuchen");
  });

  it("revert dropdown excludes the current version from the offered list (no-op guard)", async () => {
    // Review finding: board.revert leaves the restored version's own archive entry in
    // versions/, so after reverting cutlist to v1 the dropdown would otherwise still offer
    // "v1" — a no-op revert to the version already current.
    const status = boardStatus({
      artifacts: {
        ...boardStatus().artifacts,
        cutlist: { version: 1, archived_versions: [1, 2, 3] },
      },
    });
    const client = finishedClient(status, { revertProduction: vi.fn() });
    await startFinishedSession(client);

    fireEvent.click(screen.getByRole("button", { name: /cutlist v1/ }));
    expect(screen.queryByRole("button", { name: "v1" })).toBeNull();
    expect(screen.getByRole("button", { name: "v2" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "v3" })).toBeTruthy();
  });
});
