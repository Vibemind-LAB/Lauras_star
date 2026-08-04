import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ChatMessage,
  ChatTurnResult,
  ConversationSummary,
  LauraClient,
  ProductionBoardStatus,
} from "../../api";
import { renderWithQuery } from "../../test-utils";
import { ChatStage } from "./ChatStage";

function summary(overrides: Partial<ConversationSummary> = {}): ConversationSummary {
  return { id: "c1", title: "Erster Chat", updated_at: "2026-08-03T00:00:00Z", ...overrides };
}

function textMessage(
  id: string,
  seq: number,
  role: "user" | "assistant",
  text: string,
): ChatMessage {
  return {
    id,
    conversation_id: "c1",
    seq,
    role,
    kind: "text",
    content: { text },
    created_at: "2026-08-03T00:00:00Z",
  };
}

function approvalMessage(id: string, seq: number, status = "pending"): ChatMessage {
  return {
    id,
    conversation_id: "c1",
    seq,
    role: "assistant",
    kind: "approval_request",
    content: {
      action_type: "import_urls",
      payload: { urls: ["https://example.com/a"], project_id: "p1" },
      status,
      decided_at: null,
      result: null,
    },
    created_at: "2026-08-03T00:00:00Z",
  };
}

function actionMessage(
  id: string,
  seq: number,
  tool: string,
  refs: Record<string, unknown>,
  outcome = "running",
): ChatMessage {
  return {
    id,
    conversation_id: "c1",
    seq,
    role: "assistant",
    kind: "action",
    content: { tool, args: {}, refs, outcome },
    created_at: "2026-08-03T00:00:00Z",
  };
}

function boardStatus(overrides: Partial<ProductionBoardStatus> = {}): ProductionBoardStatus {
  return {
    board_ready: true,
    job: {
      id: "j1",
      status: "succeeded",
      attempt: 1,
      updated_at: "2026-08-03T00:00:05Z",
      lease_expires_at: null,
      finished_at: "2026-08-03T00:00:05Z",
      export_id: "exp-1",
    },
    meta: {
      session_id: "s1",
      asset_id: "a1",
      created_utc: "2026-08-03T00:00:00Z",
      task: "mach einen Short",
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
    listConversations: vi.fn().mockResolvedValue([]),
    createConversation: vi.fn(),
    getConversation: vi.fn(),
    deleteConversation: vi.fn(),
    sendChatMessage: vi.fn(),
    decideApproval: vi.fn(),
    getProductionStatus: vi.fn(),
    getProductionEvents: vi.fn(),
    getJob: vi.fn(),
    ...overrides,
  } as unknown as LauraClient;
}

beforeEach(() => {
  // jsdom does not implement scrollIntoView — ChatThread's auto-scroll effect needs a stub.
  Element.prototype.scrollIntoView = vi.fn();
});

describe("ChatStage", () => {
  it("loads the conversation list on mount", async () => {
    const items = [
      summary({ id: "c1", title: "Erster Chat" }),
      summary({ id: "c2", title: "Zweiter Chat" }),
    ];
    const c = client({ listConversations: vi.fn().mockResolvedValue(items) });

    renderWithQuery(<ChatStage client={c} />);

    await waitFor(() => expect(screen.getByText("Erster Chat")).toBeTruthy());
    expect(screen.getByText("Zweiter Chat")).toBeTruthy();
  });

  it(`"Neuer Chat" creates a conversation and activates it`, async () => {
    const c = client({
      listConversations: vi
        .fn()
        .mockResolvedValueOnce([])
        .mockResolvedValueOnce([summary({ id: "new1", title: "" })]),
      createConversation: vi.fn().mockResolvedValue({ id: "new1" }),
      getConversation: vi
        .fn()
        .mockResolvedValue({ id: "new1", title: "", active_project_id: null, messages: [] }),
    });

    renderWithQuery(<ChatStage client={c} />);
    await waitFor(() => expect(c.listConversations).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: "Neuer Chat" }));

    await waitFor(() => expect(c.createConversation).toHaveBeenCalledOnce());
    await waitFor(() => expect(c.getConversation).toHaveBeenCalledWith("new1"));
  });

  it("sending a message renders the returned turn", async () => {
    const c = client({
      listConversations: vi.fn().mockResolvedValue([summary()]),
      getConversation: vi
        .fn()
        .mockResolvedValue({ id: "c1", title: "Erster Chat", active_project_id: null, messages: [] }),
      sendChatMessage: vi.fn().mockResolvedValue({
        messages: [
          textMessage("u1", 1, "user", "Hallo Laura"),
          textMessage("a1", 2, "assistant", "Hallo zurück!"),
        ],
      } satisfies ChatTurnResult),
    });

    renderWithQuery(<ChatStage client={c} />);
    fireEvent.click(await screen.findByText("Erster Chat"));
    await waitFor(() => expect(c.getConversation).toHaveBeenCalledWith("c1"));

    fireEvent.change(screen.getByLabelText("Nachricht"), { target: { value: "Hallo Laura" } });
    fireEvent.click(screen.getByRole("button", { name: "Senden" }));

    expect(c.sendChatMessage).toHaveBeenCalledWith("c1", "Hallo Laura");
    await waitFor(() => expect(screen.getByText("Hallo Laura")).toBeTruthy());
    expect(screen.getByText("Hallo zurück!")).toBeTruthy();
  });

  it("a failed send's error banner clears the moment the next send starts", async () => {
    const sendChatMessage = vi
      .fn()
      .mockRejectedValueOnce(new Error("network down"))
      .mockReturnValue(new Promise<ChatTurnResult>(() => undefined));
    const c = client({
      listConversations: vi.fn().mockResolvedValue([summary()]),
      getConversation: vi
        .fn()
        .mockResolvedValue({ id: "c1", title: "Erster Chat", active_project_id: null, messages: [] }),
      sendChatMessage,
    });

    renderWithQuery(<ChatStage client={c} />);
    fireEvent.click(await screen.findByText("Erster Chat"));
    await waitFor(() => expect(c.getConversation).toHaveBeenCalledWith("c1"));

    fireEvent.change(screen.getByLabelText("Nachricht"), { target: { value: "Hallo" } });
    fireEvent.click(screen.getByRole("button", { name: "Senden" }));

    await waitFor(() => expect(screen.getByText("network down")).toBeTruthy());

    fireEvent.change(screen.getByLabelText("Nachricht"), { target: { value: "Nochmal" } });
    fireEvent.click(screen.getByRole("button", { name: "Senden" }));

    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("a rejected decideApproval (e.g. a 409 already-decided race) reloads the conversation and renders the real persisted status", async () => {
    const pending = approvalMessage("m1", 1, "pending");
    const executedElsewhere: ChatMessage = {
      ...pending,
      content: { ...pending.content, status: "executed", result: { asset_ids: ["a1"] } },
    };
    const getConversation = vi
      .fn()
      .mockResolvedValueOnce({
        id: "c1", title: "Erster Chat", active_project_id: null, messages: [pending],
      })
      .mockResolvedValueOnce({
        id: "c1", title: "Erster Chat", active_project_id: null, messages: [executedElsewhere],
      });
    const c = client({
      listConversations: vi.fn().mockResolvedValue([summary()]),
      getConversation,
      decideApproval: vi.fn().mockRejectedValue(new Error("409 approval already decided")),
    });

    renderWithQuery(<ChatStage client={c} />);
    fireEvent.click(await screen.findByText("Erster Chat"));
    fireEvent.click(await screen.findByRole("button", { name: "Freigeben" }));

    expect(c.decideApproval).toHaveBeenCalledWith("c1", "m1", "approve");
    await waitFor(() => expect(getConversation).toHaveBeenCalledTimes(2));
    expect(getConversation).toHaveBeenNthCalledWith(2, "c1");
    await waitFor(() => expect(screen.getByText("✓ freigegeben & ausgeführt")).toBeTruthy());
    // The stale optimistic "pending" card (still clickable) must be gone, replaced by the
    // refetched read-only persisted state.
    expect(screen.queryByRole("button", { name: "Freigeben" })).toBeNull();
  });

  it("an approval decision calls through, updates the card in place, and renders the appended action", async () => {
    // Real backend shape (execute_import_approval): the turn returns the SAME approval-card id
    // with updated content PLUS a newly appended `action` message narrating the executed import.
    const pending = approvalMessage("m1", 1, "pending");
    const executedCard: ChatMessage = {
      ...pending,
      content: { ...pending.content, status: "executed", result: { asset_ids: ["a1"] } },
    };
    const appendedAction = actionMessage("m2", 2, "import_urls", { job_ids: ["j1"] }, "running");
    const c = client({
      listConversations: vi.fn().mockResolvedValue([summary()]),
      getConversation: vi.fn().mockResolvedValue({
        id: "c1",
        title: "Erster Chat",
        active_project_id: null,
        messages: [pending],
      }),
      decideApproval: vi.fn().mockResolvedValue({
        messages: [executedCard, appendedAction],
      } satisfies ChatTurnResult),
      getJob: vi.fn().mockResolvedValue({
        id: "j1",
        queue: "import",
        kind: "import",
        status: "succeeded",
        attempt: 1,
        max_attempts: 3,
        result_json: null,
        error_json: null,
        created_at: "2026-08-03T00:00:00Z",
        updated_at: "2026-08-03T00:00:05Z",
        finished_at: "2026-08-03T00:00:05Z",
      }),
    });

    renderWithQuery(<ChatStage client={c} />);
    fireEvent.click(await screen.findByText("Erster Chat"));
    fireEvent.click(await screen.findByRole("button", { name: "Freigeben" }));

    expect(c.decideApproval).toHaveBeenCalledWith("c1", "m1", "approve");
    await waitFor(() => expect(screen.getByText("✓ freigegeben & ausgeführt")).toBeTruthy());
    // No duplicate card — the executed-card update merged in place by id, it did not append.
    expect(screen.getAllByText("✓ freigegeben & ausgeführt")).toHaveLength(1);
    // The appended action message rendered its own card (JobActionCard, polled via getJob).
    await waitFor(() => expect(screen.getByText("✓ fertig")).toBeTruthy());
  });

  it("composer disables while an approval decision is in flight (Finding 1)", async () => {
    const pending = approvalMessage("m1", 1, "pending");
    let resolveDecide: (result: ChatTurnResult) => void = () => {
      throw new Error("resolveDecide called before assignment");
    };
    const decidePromise = new Promise<ChatTurnResult>((resolve) => {
      resolveDecide = resolve;
    });
    const c = client({
      listConversations: vi.fn().mockResolvedValue([summary()]),
      getConversation: vi.fn().mockResolvedValue({
        id: "c1",
        title: "Erster Chat",
        active_project_id: null,
        messages: [pending],
      }),
      decideApproval: vi.fn().mockReturnValue(decidePromise),
    });

    renderWithQuery(<ChatStage client={c} />);
    fireEvent.click(await screen.findByText("Erster Chat"));
    // Give the composer real text so "Senden" isn't disabled merely for being empty — its
    // disabled state below must be attributable to the in-flight decision, not empty input.
    fireEvent.change(screen.getByLabelText("Nachricht"), { target: { value: "Hallo" } });
    fireEvent.click(await screen.findByRole("button", { name: "Freigeben" }));

    expect(c.decideApproval).toHaveBeenCalledWith("c1", "m1", "approve");
    await waitFor(() =>
      expect((screen.getByLabelText("Nachricht") as HTMLTextAreaElement).disabled).toBe(true),
    );
    expect((screen.getByRole("button", { name: "Senden" }) as HTMLButtonElement).disabled).toBe(
      true,
    );

    await act(async () => {
      resolveDecide({
        messages: [
          { ...pending, content: { ...pending.content, status: "executed", result: { asset_ids: ["a1"] } } },
        ],
      });
    });

    await waitFor(() =>
      expect((screen.getByLabelText("Nachricht") as HTMLTextAreaElement).disabled).toBe(false),
    );
    expect((screen.getByRole("button", { name: "Senden" }) as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  it(`a manual "▶ ansehen" selection survives a follow-up text turn (Finding 2)`, async () => {
    vi.useFakeTimers();
    try {
      const action = actionMessage("m1", 1, "start_short", { session_id: "s1" }, "running");
      const done = boardStatus();
      // What the default-derivation effect would (wrongly, pre-fix) recompute to after the
      // follow-up turn: same session, but no export yet and no contact sheet — a plausible
      // in-between snapshot of a still-live board that must NOT clobber the manual pin.
      const noExportYet = boardStatus({
        job: {
          id: "j1",
          status: "running",
          attempt: 2,
          updated_at: "2026-08-03T00:00:10Z",
          lease_expires_at: null,
          finished_at: null,
          export_id: null,
        },
        artifacts: {
          ...done.artifacts,
          contact_sheet: { version: null, archived_versions: [] },
        },
      });
      const getProductionStatus = vi
        .fn()
        .mockImplementationOnce(() => new Promise<never>(() => {})) // ChatStage's initial default derive
        .mockResolvedValueOnce(done) // ActionCard's own on-done fetch
        .mockResolvedValueOnce(done) // click "▶ ansehen" -> onFocusAction's derive
        .mockResolvedValue(noExportYet); // any later default-derivation recompute
      const c = client({
        listConversations: vi.fn().mockResolvedValue([summary()]),
        getConversation: vi.fn().mockResolvedValue({
          id: "c1",
          title: "Erster Chat",
          active_project_id: null,
          messages: [action],
        }),
        getProductionStatus,
        getProductionEvents: vi.fn().mockResolvedValue({ events: [], next: 0, done: true }),
        sendChatMessage: vi.fn().mockResolvedValue({
          messages: [textMessage("u2", 2, "user", "Danke")],
        } satisfies ChatTurnResult),
      });

      renderWithQuery(<ChatStage client={c} />);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      await act(async () => {
        fireEvent.click(screen.getByText("Erster Chat"));
        await vi.advanceTimersByTimeAsync(0);
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2500);
      });
      const ansehenButton = screen.getByRole("button", { name: "▶ ansehen" });
      await act(async () => {
        fireEvent.click(ansehenButton);
        await vi.advanceTimersByTimeAsync(0);
      });

      let video = document.querySelector("video");
      expect(video?.getAttribute("src")).toBe("laura-media://media/export/exp-1");

      fireEvent.change(screen.getByLabelText("Nachricht"), { target: { value: "Danke" } });
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Senden" }));
        await vi.advanceTimersByTimeAsync(0);
      });

      // The manual pin must survive the follow-up turn's default-derivation recompute.
      video = document.querySelector("video");
      expect(video?.getAttribute("src")).toBe("laura-media://media/export/exp-1");
    } finally {
      vi.useRealTimers();
    }
  });

  it("a delayed send from a conversation left before it resolved does not merge into the new one (Finding 3)", async () => {
    let resolveSend: (result: ChatTurnResult) => void = () => {
      throw new Error("resolveSend called before assignment");
    };
    const sendPromise = new Promise<ChatTurnResult>((resolve) => {
      resolveSend = resolve;
    });
    const c = client({
      listConversations: vi.fn().mockResolvedValue([
        summary({ id: "c1", title: "Erster Chat" }),
        summary({ id: "c2", title: "Zweiter Chat" }),
      ]),
      getConversation: vi.fn().mockImplementation((id: string) => {
        if (id === "c1") {
          return Promise.resolve({
            id: "c1",
            title: "Erster Chat",
            active_project_id: null,
            messages: [],
          });
        }
        return Promise.resolve({
          id: "c2",
          title: "Zweiter Chat",
          active_project_id: null,
          messages: [textMessage("b1", 1, "assistant", "Willkommen in B")],
        });
      }),
      sendChatMessage: vi.fn().mockReturnValue(sendPromise),
    });

    renderWithQuery(<ChatStage client={c} />);
    fireEvent.click(await screen.findByText("Erster Chat"));
    await waitFor(() => expect(c.getConversation).toHaveBeenCalledWith("c1"));

    fireEvent.change(screen.getByLabelText("Nachricht"), { target: { value: "Hallo aus A" } });
    fireEvent.click(screen.getByRole("button", { name: "Senden" }));
    expect(c.sendChatMessage).toHaveBeenCalledWith("c1", "Hallo aus A");

    fireEvent.click(screen.getByText("Zweiter Chat"));
    await waitFor(() => expect(c.getConversation).toHaveBeenCalledWith("c2"));
    await waitFor(() => expect(screen.getByText("Willkommen in B")).toBeTruthy());

    await act(async () => {
      resolveSend({
        messages: [
          textMessage("u1", 1, "user", "Hallo aus A"),
          textMessage("a1", 2, "assistant", "Antwort aus A"),
        ],
      });
    });

    expect(screen.queryByText("Hallo aus A")).toBeNull();
    expect(screen.queryByText("Antwort aus A")).toBeNull();
    expect(screen.getByText("Willkommen in B")).toBeTruthy();
  });

  it("decideApproval reloads the conversation list, matching onSend (Finding 4)", async () => {
    const pending = approvalMessage("m1", 1, "pending");
    const listConversations = vi.fn().mockResolvedValue([summary()]);
    const c = client({
      listConversations,
      getConversation: vi.fn().mockResolvedValue({
        id: "c1",
        title: "Erster Chat",
        active_project_id: null,
        messages: [pending],
      }),
      decideApproval: vi.fn().mockResolvedValue({
        messages: [
          { ...pending, content: { ...pending.content, status: "executed", result: { asset_ids: ["a1"] } } },
        ],
      } satisfies ChatTurnResult),
    });

    renderWithQuery(<ChatStage client={c} />);
    fireEvent.click(await screen.findByText("Erster Chat"));
    await waitFor(() => expect(listConversations).toHaveBeenCalledTimes(1));

    fireEvent.click(await screen.findByRole("button", { name: "Freigeben" }));

    expect(c.decideApproval).toHaveBeenCalledWith("c1", "m1", "approve");
    await waitFor(() => expect(listConversations).toHaveBeenCalledTimes(2));
  });

  it(`preview switches when an ActionCard's "▶ ansehen" fires`, async () => {
    vi.useFakeTimers();
    try {
      const action = actionMessage("m1", 1, "start_short", { session_id: "s1" }, "running");
      const done = boardStatus();
      // The FIRST call is ChatStage's own default-derive fetch (fired the moment the thread
      // loads) — it never resolves, so the default preview target deterministically stays
      // "none" for the whole test. Every later call (the ActionCard's own on-done fetch, and
      // the click-triggered derive) resolves to the finished board.
      const getProductionStatus = vi
        .fn()
        .mockImplementationOnce(() => new Promise<never>(() => {}))
        .mockResolvedValue(done);
      const c = client({
        listConversations: vi.fn().mockResolvedValue([summary()]),
        getConversation: vi.fn().mockResolvedValue({
          id: "c1",
          title: "Erster Chat",
          active_project_id: null,
          messages: [action],
        }),
        getProductionStatus,
        getProductionEvents: vi.fn().mockResolvedValue({ events: [], next: 0, done: true }),
      });

      renderWithQuery(<ChatStage client={c} />);

      // Let the conversation list's own mount-time load resolve before selecting a row.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });

      await act(async () => {
        fireEvent.click(screen.getByText("Erster Chat"));
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(screen.getByText("Noch nichts zu zeigen — bau etwas.")).toBeTruthy();

      // Advance the ActionCard's own poll interval so it reaches its "done" state and renders
      // the "▶ ansehen" affordance.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2500);
      });
      const ansehenButton = screen.getByRole("button", { name: "▶ ansehen" });

      await act(async () => {
        fireEvent.click(ansehenButton);
        await vi.advanceTimersByTimeAsync(0);
      });

      const video = document.querySelector("video");
      expect(video?.getAttribute("src")).toBe("laura-media://media/export/exp-1");
    } finally {
      vi.useRealTimers();
    }
  });
});
