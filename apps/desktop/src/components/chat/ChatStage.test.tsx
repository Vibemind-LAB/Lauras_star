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

  it("an approval decision calls through and updates the card in place", async () => {
    const pending = approvalMessage("m1", 1, "pending");
    const c = client({
      listConversations: vi.fn().mockResolvedValue([summary()]),
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
    fireEvent.click(await screen.findByRole("button", { name: "Freigeben" }));

    expect(c.decideApproval).toHaveBeenCalledWith("c1", "m1", "approve");
    await waitFor(() => expect(screen.getByText("✓ freigegeben & ausgeführt")).toBeTruthy());
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
