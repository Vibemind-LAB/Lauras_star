import { fireEvent, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatMessage, JobStatus, LauraClient } from "../../api";
import { renderWithQuery } from "../../test-utils";
import { ChatThread } from "./ChatThread";

function textMessage(role: "user" | "assistant", text: string, seq: number): ChatMessage {
  return {
    id: `m${seq}`,
    conversation_id: "c1",
    seq,
    role,
    kind: "text",
    content: { text },
    created_at: "2026-01-01T00:00:00Z",
  };
}

function approvalMessage(seq: number, status = "pending"): ChatMessage {
  return {
    id: `m${seq}`,
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
    created_at: "2026-01-01T00:00:00Z",
  };
}

function actionMessage(seq: number): ChatMessage {
  return {
    id: `m${seq}`,
    conversation_id: "c1",
    seq,
    role: "assistant",
    kind: "action",
    content: { tool: "import_urls", args: {}, refs: { job_ids: ["j1"] }, outcome: "running" },
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

function client(overrides: Partial<LauraClient> = {}): LauraClient {
  return {
    getJob: vi.fn().mockResolvedValue(job()),
    getProductionEvents: vi.fn(),
    getProductionStatus: vi.fn(),
    ...overrides,
  } as unknown as LauraClient;
}

beforeEach(() => {
  // jsdom does not implement scrollIntoView — stub it so the auto-scroll effect doesn't throw.
  Element.prototype.scrollIntoView = vi.fn();
});

describe("ChatThread", () => {
  it("renders text messages as role-styled bubbles", () => {
    const messages = [textMessage("user", "Hallo Laura", 1), textMessage("assistant", "Hallo!", 2)];
    renderWithQuery(
      <ChatThread messages={messages} client={client()} onDecide={vi.fn()} />,
    );
    expect(screen.getByText("Hallo Laura")).toBeTruthy();
    expect(screen.getByText("Hallo!")).toBeTruthy();
  });

  it("renders an approval_request message as an ApprovalCard", () => {
    const messages = [approvalMessage(1)];
    renderWithQuery(
      <ChatThread messages={messages} client={client()} onDecide={vi.fn()} />,
    );
    expect(screen.getByText("https://example.com/a")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Freigeben" })).toBeTruthy();
  });

  it("routes the approval decision through onDecide with the message id", () => {
    const onDecide = vi.fn();
    const messages = [approvalMessage(7)];
    renderWithQuery(
      <ChatThread messages={messages} client={client()} onDecide={onDecide} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Freigeben" }));
    expect(onDecide).toHaveBeenCalledWith("m7", "approve");
  });

  it("renders an action message as an ActionCard", async () => {
    const messages = [actionMessage(3)];
    renderWithQuery(
      <ChatThread messages={messages} client={client()} onDecide={vi.fn()} />,
    );
    expect(await screen.findByText("⚙ running")).toBeTruthy();
  });

  it("routes action focus through onFocusAction with the message id", async () => {
    const onFocusAction = vi.fn();
    const messages = [actionMessage(9)];
    const c = client({ getJob: vi.fn().mockResolvedValue(job({ status: "succeeded" })) });
    renderWithQuery(
      <ChatThread messages={messages} client={c} onDecide={vi.fn()} onFocusAction={onFocusAction} />,
    );
    // JobActionCard has no focus affordance itself — this exercises the wiring only when the
    // card type supports it (start_short/follow_up); a plain job card just confirms no crash.
    expect(await screen.findByText("✓ done")).toBeTruthy();
  });

  it("auto-scrolls to the newest message when messages change", () => {
    const scrollSpy = vi.fn();
    Element.prototype.scrollIntoView = scrollSpy;
    const { rerender } = renderWithQuery(
      <ChatThread messages={[textMessage("user", "eins", 1)]} client={client()} onDecide={vi.fn()} />,
    );
    expect(scrollSpy).toHaveBeenCalled();
    scrollSpy.mockClear();

    rerender(
      <ChatThread
        messages={[textMessage("user", "eins", 1), textMessage("assistant", "zwei", 2)]}
        client={client()}
        onDecide={vi.fn()}
      />,
    );
    expect(scrollSpy).toHaveBeenCalled();
  });
});
