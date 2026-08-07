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
import { ActionCard, narrowReviewTranscriptPayload } from "./ActionCard";

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
      scene_selection: { version: null, archived_versions: [] },
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

/** A pending Gate-S proposal (GS1/GS3-Payload) — one recommended candidate, one not. */
function sceneGate(
  overrides: Partial<NonNullable<ProductionBoardStatus["scene_gate"]>> = {},
): NonNullable<ProductionBoardStatus["scene_gate"]> {
  return {
    enabled: true,
    pending: true,
    confirmed: false,
    recommended: [2],
    candidates: [
      {
        scene_number: 2,
        src_start_frame: 0,
        src_end_frame_exclusive: 300,
        thumb_frame: 150,
        description: "n8n Flow im Bild",
        transcript_snippet: "wir bauen den flow",
        rationale: "Hook",
        recommended: true,
      },
      {
        scene_number: 5,
        src_start_frame: 300,
        src_end_frame_exclusive: 600,
        thumb_frame: 450,
        description: "Terminal",
        transcript_snippet: "deploy läuft",
        rationale: "Beweis",
        recommended: false,
      },
    ],
    ...overrides,
  };
}

function client(overrides: Partial<LauraClient> = {}): LauraClient {
  return {
    getProductionEvents: vi.fn(),
    getProductionStatus: vi.fn(),
    getJob: vi.fn(),
    // Never-resolving promise: SceneSelectionCard's tiles fetch a thumbnail via this on mount,
    // and jsdom does not implement URL.revokeObjectURL — same pattern as SceneStrip.test.tsx.
    assetFrameUrl: vi.fn().mockReturnValue(new Promise<string>(() => undefined)),
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

  it("approve_script (Gate B) renders as a production card, not the unknown-action fallback", async () => {
    // Regression for the review finding: approve_script's card carries the SAME
    // refs.session_id/refs.job_id contract as start_short/follow_up (see
    // _handle_approve_script in services/local-api/src/laura/chat/executor.py), so the
    // resumed production (voice -> cutlist -> render -> export) must narrate live here too,
    // not fall through to UnknownActionLine's bare tool-name text.
    const events: AgentEvent[] = [{ type: "stage", stage: "voice", team: "core" }];
    const c = client({
      getProductionEvents: vi.fn().mockResolvedValue({ events, next: 1, done: false }),
      getJob: vi.fn().mockResolvedValue(job({ status: "running" })),
    });
    renderWithQuery(
      <ActionCard
        message={actionMessage("approve_script", { session_id: "s1", job_id: "j1" })}
        client={c}
      />,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    expect(c.getProductionEvents).toHaveBeenCalledWith("s1", 0);
    expect(screen.getByText(/voice/)).toBeTruthy();
    expect(screen.queryByText("approve_script")).toBeNull();
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
    // Exact string, not a loose /82%/ match: the migrated artifact chips (below) also render a
    // "Export v1 · 82%" chip off the SAME render_report.target_ratio, so a loose regex would now
    // match two elements and throw. This pins the result block's own line specifically.
    expect(screen.getByText("Export: exp-1 · 82%")).toBeTruthy();
  });

  it("the done state invites further adjustment", async () => {
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

    expect(screen.getByText(/Export: exp-1/)).toBeTruthy();
    expect(
      screen.getByText(
        "Weiter anpassen: sag z. B. ‚mach den Hook kürzer' — oder frag einfach.",
      ),
    ).toBeTruthy();
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
    // The adjustment hint belongs to the done+export card only, not a failed run.
    expect(screen.queryByText(/Weiter anpassen/)).toBeNull();

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

// -------------------------------------------------------------------------------------------
// Board chips (artifact chain + revert dropdown) — migrated from the removed pre-chat
// "Assistent" panel's SessionChips/RevertChip (ChatPanel.tsx, deleted). ProductionActionCard
// now renders the same chips whenever a board snapshot (`status`) is available, wiring the
// revert button to `client.revertProduction` once the run has landed (not while "running" —
// the endpoint 409s on a queued/running job).
// -------------------------------------------------------------------------------------------

describe("ActionCard — board chips (artifact chain + revert)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("done renders one chip per present artifact in chain order", async () => {
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

    expect(screen.getByText("storyline v1")).toBeTruthy();
    expect(screen.getByText("script v1")).toBeTruthy();
    expect(screen.getByText("voice v1")).toBeTruthy();
    expect(screen.getByText("cutlist v1")).toBeTruthy();
    expect(screen.getByText("Bogen v1")).toBeTruthy();
    expect(screen.getByText("QA v1")).toBeTruthy();
    // render_report's chip carries its own ratio annotation, distinct from the result block's
    // "Export: exp-1 · 82%" line (pinned by exact string above).
    expect(screen.getByText("Export v1 · 82%")).toBeTruthy();
  });

  it("a chip with archived versions renders a button; picking a version and confirming reverts to it", async () => {
    const status = boardStatus({
      artifacts: { ...boardStatus().artifacts, cutlist: { version: 3, archived_versions: [1, 2] } },
    });
    const revertProduction = vi.fn().mockResolvedValue({
      ok: true,
      artifact: "cutlist",
      version: 1,
      invalidated: [],
      restored: [],
      status,
    });
    const c = client({
      getProductionEvents: vi.fn().mockResolvedValue({ events: [], next: 0, done: true }),
      getProductionStatus: vi.fn().mockResolvedValue(status),
      revertProduction,
    });
    renderWithQuery(
      <ActionCard message={actionMessage("start_short", { session_id: "s1" })} client={c} />,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    fireEvent.click(screen.getByRole("button", { name: /cutlist v3/ }));
    expect(screen.getByRole("button", { name: "v1" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "v2" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "v1" }));
    fireEvent.click(screen.getByRole("button", { name: "Zurückdrehen" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(revertProduction).toHaveBeenCalledWith("s1", "cutlist", 1);
  });

  it("re-renders chips from the revert response and shows the restored hint", async () => {
    const initialStatus = boardStatus({
      artifacts: { ...boardStatus().artifacts, cutlist: { version: 3, archived_versions: [1, 2] } },
    });
    const revertedStatus = boardStatus({
      artifacts: {
        ...boardStatus().artifacts,
        cutlist: { version: 1, archived_versions: [] },
        contact_sheet: { version: 2, archived_versions: [1] },
      },
    });
    const revertProduction = vi.fn().mockResolvedValue({
      ok: true,
      artifact: "cutlist",
      version: 1,
      invalidated: ["contact_sheet"],
      restored: ["contact_sheet"],
      status: revertedStatus,
    });
    const c = client({
      getProductionEvents: vi.fn().mockResolvedValue({ events: [], next: 0, done: true }),
      getProductionStatus: vi.fn().mockResolvedValue(initialStatus),
      revertProduction,
    });
    renderWithQuery(
      <ActionCard message={actionMessage("start_short", { session_id: "s1" })} client={c} />,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    fireEvent.click(screen.getByRole("button", { name: /cutlist v3/ }));
    fireEvent.click(screen.getByRole("button", { name: "v1" }));
    fireEvent.click(screen.getByRole("button", { name: "Zurückdrehen" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(screen.getByText("cutlist v1")).toBeTruthy();
    expect(screen.getByText("Bogen v2")).toBeTruthy();
    expect(screen.getByText(/♻️ Wiederhergestellt: Bogen/)).toBeTruthy();
  });

  it("a 409 (run in progress) revert response shows the 'Lauf aktiv' hint, not the raw detail", async () => {
    const status = boardStatus({
      artifacts: { ...boardStatus().artifacts, cutlist: { version: 3, archived_versions: [1, 2] } },
    });
    const revertProduction = vi
      .fn()
      .mockRejectedValue(new Error('409: {"detail":"run in progress — revert would race the team"}'));
    const c = client({
      getProductionEvents: vi.fn().mockResolvedValue({ events: [], next: 0, done: true }),
      getProductionStatus: vi.fn().mockResolvedValue(status),
      revertProduction,
    });
    renderWithQuery(
      <ActionCard message={actionMessage("start_short", { session_id: "s1" })} client={c} />,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    fireEvent.click(screen.getByRole("button", { name: /cutlist v3/ }));
    fireEvent.click(screen.getByRole("button", { name: "v1" }));
    fireEvent.click(screen.getByRole("button", { name: "Zurückdrehen" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(screen.getByText(/Lauf aktiv/)).toBeTruthy();
  });

  it("a resume that restored artifacts shows the ♻️ chip with a friendly-label tooltip", async () => {
    const status = boardStatus({
      job: {
        id: "j1",
        status: "succeeded",
        attempt: 1,
        updated_at: "2026-01-01T00:00:05Z",
        lease_expires_at: null,
        finished_at: "2026-01-01T00:00:05Z",
        restored: ["voice", "contact_sheet"],
      },
    });
    const c = client({
      getProductionEvents: vi.fn().mockResolvedValue({ events: [], next: 0, done: true }),
      getProductionStatus: vi.fn().mockResolvedValue(status),
    });
    renderWithQuery(
      <ActionCard message={actionMessage("start_short", { session_id: "s1" })} client={c} />,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    const chip = screen.getByText("♻️ 2");
    expect(chip.getAttribute("title")).toBe("Wiederhergestellt: voice, Bogen");
  });

  it("running (post Gate-S confirm) shows chips but suppresses the revert button", async () => {
    const getProductionEvents = vi
      .fn()
      .mockResolvedValue({ events: [], next: 0, done: true });
    const pendingStatus = boardStatus({ scene_gate: sceneGate() });
    const resumedStatus = boardStatus({
      scene_gate: { enabled: true, pending: false, confirmed: true, selected: [2] },
      job: {
        id: "j2",
        status: "running",
        attempt: 1,
        updated_at: "2026-01-01T00:01:00Z",
        lease_expires_at: null,
        finished_at: null,
      },
      artifacts: { ...boardStatus().artifacts, cutlist: { version: 3, archived_versions: [1, 2] } },
    });
    const getProductionStatus = vi
      .fn()
      .mockResolvedValueOnce(pendingStatus)
      .mockResolvedValueOnce(resumedStatus);
    const getJob = vi
      .fn()
      .mockImplementation((id: string) =>
        Promise.resolve(job({ id, status: id === "j1" ? "succeeded" : "running" })),
      );
    const confirmSceneSelection = vi.fn().mockResolvedValue({ session_id: "s1", job_id: "j2" });
    const c = client({ getProductionEvents, getProductionStatus, getJob, confirmSceneSelection });
    renderWithQuery(
      <ActionCard
        message={actionMessage("start_short", { session_id: "s1", job_id: "j1" })}
        client={c}
      />,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Auswahl übernehmen/ }));
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(screen.getByText("⚙ läuft …")).toBeTruthy();
    // The chip is visible (chips show across running too) …
    const chip = screen.getByText("cutlist v3");
    // … but as a plain read-only pill, not the revert-capable button variant.
    expect(chip.tagName).toBe("SPAN");
    expect(screen.queryByRole("button", { name: /cutlist v3/ })).toBeNull();
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
    // The adjustment hint is part of the export result block, not the pending-gate block.
    expect(screen.queryByText(/Weiter anpassen/)).toBeNull();
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

describe("ActionCard — Gate S (scene checkpoint)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("a pending scene_gate shows the tile picker with the recommendation pre-checked, ahead of script_gate/export", async () => {
    const getProductionEvents = vi
      .fn()
      .mockResolvedValue({ events: [], next: 0, done: true });
    const getProductionStatus = vi.fn().mockResolvedValue(
      boardStatus({
        scene_gate: sceneGate(),
        // Both a pending script_gate and a finished export are present in the same fixture —
        // scene selection runs BEFORE the script in the pipeline, so it must win the ternary,
        // same "priority over the ordinary result row" contract script_gate itself has above.
        script_gate: { enabled: true, approved: false, pending: true },
        script_lines: [{ chapter: 1, scene_number: 1, text: "sollte nicht erscheinen" }],
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

    expect(screen.getByText(/Szenen-Auswahl/)).toBeTruthy();
    expect(screen.getByTestId("scene-tile-2").getAttribute("data-selected")).toBe("true");
    expect(screen.getByTestId("scene-tile-5").getAttribute("data-selected")).toBe("false");
    expect(screen.queryByText("📝 Sprechertext wartet auf Freigabe")).toBeNull();
    expect(screen.queryByText(/Export:/)).toBeNull();
  });

  it("confirming a tile pick posts the selection and resumes live narration for the confirm's resumed job", async () => {
    const getProductionEvents = vi
      .fn()
      .mockResolvedValue({ events: [], next: 0, done: true });
    const pendingStatus = boardStatus({ scene_gate: sceneGate() });
    const resumedStatus = boardStatus({
      scene_gate: { enabled: true, pending: false, confirmed: true, selected: [2] },
      job: {
        id: "j2",
        status: "running",
        attempt: 1,
        updated_at: "2026-01-01T00:01:00Z",
        lease_expires_at: null,
        finished_at: null,
      },
    });
    const getProductionStatus = vi
      .fn()
      .mockResolvedValueOnce(pendingStatus) // ProductionActionCard's own on-done fetch
      .mockResolvedValueOnce(resumedStatus); // refreshAfterConfirm's fetch after the tile confirm
    // j1 (the original run) has already finished — same "succeeded while the gate is pending"
    // shape the other test in this block uses. j2 (the confirm's resumed run) is still going,
    // so the job-status backstop must not finalize it the instant it starts polling.
    const getJob = vi
      .fn()
      .mockImplementation((id: string) =>
        Promise.resolve(job({ id, status: id === "j1" ? "succeeded" : "running" })),
      );
    const confirmSceneSelection = vi.fn().mockResolvedValue({ session_id: "s1", job_id: "j2" });
    const c = client({ getProductionEvents, getProductionStatus, getJob, confirmSceneSelection });
    renderWithQuery(
      <ActionCard
        message={actionMessage("start_short", { session_id: "s1", job_id: "j1" })}
        client={c}
      />,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    expect(screen.getByText(/Szenen-Auswahl/)).toBeTruthy();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Auswahl übernehmen/ }));
      await vi.advanceTimersByTimeAsync(0);
    });

    // The card's own `client` (not the tile card's own confirm-prop default) fielded the POST —
    // no `confirm` prop was passed at the ActionCard wiring site, so this proves the wiring uses
    // the real client method, not a test-only override.
    expect(confirmSceneSelection).toHaveBeenCalledWith("s1", [2]);
    expect(getProductionStatus).toHaveBeenCalledTimes(2);
    // The tile picker is gone (scene_gate.pending is now false) and the card resumed narrating
    // live for the NEW job the confirm's resume enqueued, instead of going stale.
    expect(screen.queryByText(/Szenen-Auswahl/)).toBeNull();
    expect(screen.getByText("⚙ läuft …")).toBeTruthy();

    getProductionEvents.mockClear();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    expect(getProductionEvents).toHaveBeenCalledWith("s1", 0);
  });
});

describe("ActionCard — select_scenes (Gate S chat path)", () => {
  // Regression for the same review finding class as approve_script above: select_scenes'
  // action card (`_handle_select_scenes` in services/local-api/src/laura/chat/executor.py) must
  // not fall through to UnknownActionLine's bare tool-name text.

  it("outcome 'running' shows a busy line, not the unknown-action fallback", () => {
    const c = client();
    renderWithQuery(
      <ActionCard
        message={actionMessage("select_scenes", { session_id: "s1", job_id: "j2" }, "running")}
        client={c}
      />,
    );
    expect(screen.getByText("⚙ Auswahl wird angewendet …")).toBeTruthy();
    expect(screen.queryByText("select_scenes")).toBeNull();
  });

  it("outcome 'done' (e.g. an idempotent re-confirm) shows a checkmark line", () => {
    const c = client();
    renderWithQuery(
      <ActionCard
        message={actionMessage("select_scenes", { session_id: "s1" }, "done")}
        client={c}
      />,
    );
    expect(screen.getByText("✓ Auswahl übernommen")).toBeTruthy();
    expect(screen.queryByText("select_scenes")).toBeNull();
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

  it("a segment entry that isn't an object, is null, or is missing fields: degrades per-field, no crash", () => {
    const c = client();
    renderWithQuery(
      <ActionCard
        message={reviewTranscriptMessage({
          confirmed_at: null,
          // A literal `null` exercises the `raw !== null` half of the row guard specifically:
          // `typeof null === "object"` is true (the classic JS gotcha), so `typeof raw ===
          // "object"` alone would let `null` through as `row`, and `row.index` below would throw
          // — only the `&& raw !== null` half prevents that.
          segments: ["not-an-object", null, { text: "Nur Text vorhanden, sonst nichts" }],
          total: 3,
        })}
        client={c}
      />,
    );

    expect(screen.getByText("Transkript prüfen")).toBeTruthy();
    // First entry: not an object at all — every field falls back (index 1, empty text).
    expect(screen.getByText(/#1 · 0s ·/)).toBeTruthy();
    // Second entry: null — same full fallback (index 2, empty text), not a crash.
    expect(screen.getByText(/#2 · 0s ·/)).toBeTruthy();
    // Third entry: an object, but only `text` is present — the rest fall back, text survives.
    expect(screen.getByText(/#3 · 0s · Nur Text vorhanden, sonst nichts/)).toBeTruthy();
  });

  // The two `total`-fallback cases below are asserted directly against
  // `narrowReviewTranscriptPayload` rather than via a rendered card: at the render level,
  // `total - segments.length` with the `typeof total === "number"` guard removed evaluates to
  // `NaN - n === NaN`, and `NaN > 0` is false exactly like `0 > 0` — so a render-level assertion
  // of "no remainder line" holds whether or not the guard exists and can never fail. A direct
  // call catches the real regression: `total` must equal `segments.length`, not `NaN`.

  it("narrowReviewTranscriptPayload: total missing falls back to segments.length", () => {
    const result = narrowReviewTranscriptPayload({
      payload: {
        confirmed_at: null,
        segments: [reviewSegment(1, "Eins"), reviewSegment(2, "Zwei")],
      },
    });

    expect(result.total).toBe(2);
    expect(Number.isNaN(result.total)).toBe(false);
  });

  it("narrowReviewTranscriptPayload: non-number total falls back to segments.length", () => {
    const result = narrowReviewTranscriptPayload({
      payload: {
        confirmed_at: null,
        segments: [reviewSegment(1, "Eins")],
        total: "viele",
      },
    });

    expect(result.total).toBe(1);
    expect(Number.isNaN(result.total)).toBe(false);
  });

  it("a malformed total never produces a false-positive remainder line at render time", () => {
    // Render-level companion to the two direct tests above: whatever `total` ends up as for a
    // malformed input, the card must not claim there are more segments than were actually shown.
    // This does NOT guarantee `total` itself is correct (see the direct tests for that) — a
    // `NaN` or negative `total` would pass this exact assertion too.
    const c = client();
    renderWithQuery(
      <ActionCard
        message={reviewTranscriptMessage({
          confirmed_at: null,
          segments: [reviewSegment(1, "Eins"), reviewSegment(2, "Zwei")],
          total: "viele",
        })}
        client={c}
      />,
    );

    expect(screen.getByText("Transkript prüfen")).toBeTruthy();
    expect(screen.getByText(/#1 · 1s · Eins/)).toBeTruthy();
    expect(screen.getByText(/#2 · 2s · Zwei/)).toBeTruthy();
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
