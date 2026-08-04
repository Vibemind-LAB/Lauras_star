import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ChatMessage } from "../../api";
import { ApprovalCard } from "./ApprovalCard";

function approvalMessage(overrides: Partial<ChatMessage["content"]> = {}): ChatMessage {
  return {
    id: "m1",
    conversation_id: "c1",
    seq: 2,
    role: "assistant",
    kind: "approval_request",
    content: {
      action_type: "import_urls",
      payload: { urls: ["https://example.com/a", "https://example.com/b"], project_id: "p1" },
      status: "pending",
      decided_at: null,
      result: null,
      ...overrides,
    },
    created_at: "2026-01-01T00:00:00Z",
  };
}

describe("ApprovalCard", () => {
  it("lists the proposed URLs", () => {
    render(<ApprovalCard message={approvalMessage()} onDecide={vi.fn()} />);
    expect(screen.getByText("https://example.com/a")).toBeTruthy();
    expect(screen.getByText("https://example.com/b")).toBeTruthy();
  });

  it("pending shows both buttons and clicking calls onDecide", () => {
    const onDecide = vi.fn();
    render(<ApprovalCard message={approvalMessage({ status: "pending" })} onDecide={onDecide} />);

    const approve = screen.getByRole("button", { name: "Freigeben" });
    const reject = screen.getByRole("button", { name: "Ablehnen" });
    expect(approve).toBeTruthy();
    expect(reject).toBeTruthy();

    fireEvent.click(approve);
    expect(onDecide).toHaveBeenCalledWith("approve");

    fireEvent.click(reject);
    expect(onDecide).toHaveBeenCalledWith("reject");
  });

  it("executed shows no buttons — just the persisted decision", () => {
    render(
      <ApprovalCard
        message={approvalMessage({ status: "executed", decided_at: "2026-01-01T00:01:00Z" })}
        onDecide={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: "Freigeben" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Ablehnen" })).toBeNull();
    expect(screen.getByText("✓ freigegeben & ausgeführt")).toBeTruthy();
  });

  it("rejected shows no buttons — the negative read-only line", () => {
    render(
      <ApprovalCard
        message={approvalMessage({ status: "rejected", decided_at: "2026-01-01T00:01:00Z" })}
        onDecide={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: "Freigeben" })).toBeNull();
    expect(screen.getByText("✗ abgelehnt")).toBeTruthy();
  });
});
