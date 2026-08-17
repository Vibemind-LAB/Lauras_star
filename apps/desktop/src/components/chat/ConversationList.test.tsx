import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ConversationSummary, OpenProductionSession } from "../../api";
import { ConversationList } from "./ConversationList";

function summary(overrides: Partial<ConversationSummary> = {}): ConversationSummary {
  return {
    id: "c1",
    title: "Erster Chat",
    updated_at: "2026-08-03T00:00:00Z",
    ...overrides,
  };
}

const openSession: OpenProductionSession = {
  session_id: "s1",
  conversation_id: "c1",
  project_id: "p1",
  asset_id: "a1",
  asset_display_name: "Rough Cut",
  brief_preview: "Szenenauswahl fortsetzen",
  resume_point: "visual_selection",
  state: "awaiting-approval",
  updated_utc: "2026-08-17T10:00:00+00:00",
  draft_updated_utc: "2026-08-17T09:59:00+00:00",
  latest_job_id: "j1",
  stale: false,
  stale_reason: null,
};

describe("ConversationList", () => {
  it("renders open productions above new chat and forwards an explicit resume", () => {
    const onResume = vi.fn();
    render(
      <ConversationList
        items={[]}
        activeId={null}
        onSelect={vi.fn()}
        onNew={vi.fn()}
        onDelete={vi.fn()}
        openSessions={[openSession]}
        onResume={onResume}
      />,
    );
    const resume = screen.getByRole("button", { name: "Fortsetzen" });
    const newChat = screen.getByRole("button", { name: "New chat" });
    expect(resume.compareDocumentPosition(newChat) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(resume);
    expect(onResume).toHaveBeenCalledWith(openSession);
  });

  it("shows the empty state when there are no conversations", () => {
    render(
      <ConversationList items={[]} activeId={null} onSelect={vi.fn()} onNew={vi.fn()} onDelete={vi.fn()} />,
    );
    expect(screen.getByText("No conversations yet")).toBeTruthy();
  });

  it("renders one row per conversation", () => {
    const items = [summary({ id: "c1", title: "Erster Chat" }), summary({ id: "c2", title: "Zweiter Chat" })];
    render(
      <ConversationList items={items} activeId={null} onSelect={vi.fn()} onNew={vi.fn()} onDelete={vi.fn()} />,
    );
    expect(screen.getByText("Erster Chat")).toBeTruthy();
    expect(screen.getByText("Zweiter Chat")).toBeTruthy();
  });

  it("clicking a row fires onSelect with its id", () => {
    const onSelect = vi.fn();
    const items = [summary({ id: "c1", title: "Erster Chat" })];
    render(
      <ConversationList items={items} activeId={null} onSelect={onSelect} onNew={vi.fn()} onDelete={vi.fn()} />,
    );
    fireEvent.click(screen.getByText("Erster Chat"));
    expect(onSelect).toHaveBeenCalledWith("c1");
  });

  it("marks the active conversation", () => {
    const items = [summary({ id: "c1", title: "Erster Chat" })];
    render(
      <ConversationList items={items} activeId="c1" onSelect={vi.fn()} onNew={vi.fn()} onDelete={vi.fn()} />,
    );
    const row = screen.getByText("Erster Chat").closest("[aria-pressed]");
    expect(row).toBeTruthy();
    expect(row?.getAttribute("aria-pressed")).toBe("true");
  });

  it(`clicking „Neuer Chat" fires onNew`, () => {
    const onNew = vi.fn();
    render(<ConversationList items={[]} activeId={null} onSelect={vi.fn()} onNew={onNew} onDelete={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "New chat" }));
    expect(onNew).toHaveBeenCalledOnce();
  });

  it("delete requires a confirm step — one click never fires onDelete", () => {
    const onDelete = vi.fn();
    const items = [summary({ id: "c1", title: "Erster Chat" })];
    render(
      <ConversationList items={items} activeId={null} onSelect={vi.fn()} onNew={vi.fn()} onDelete={onDelete} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete conversation" }));
    expect(onDelete).not.toHaveBeenCalled();
  });

  it("confirming the second step fires onDelete with the conversation id", () => {
    const onDelete = vi.fn();
    const onSelect = vi.fn();
    const items = [summary({ id: "c1", title: "Erster Chat" })];
    render(
      <ConversationList items={items} activeId={null} onSelect={onSelect} onNew={vi.fn()} onDelete={onDelete} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete conversation" }));
    fireEvent.click(screen.getByRole("button", { name: "Really delete?" }));
    expect(onDelete).toHaveBeenCalledWith("c1");
    // the delete affordance is a distinct control from the row — selecting must never fire too
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("cancelling the confirm step leaves the conversation untouched", () => {
    const onDelete = vi.fn();
    const items = [summary({ id: "c1", title: "Erster Chat" })];
    render(
      <ConversationList items={items} activeId={null} onSelect={vi.fn()} onNew={vi.fn()} onDelete={onDelete} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete conversation" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onDelete).not.toHaveBeenCalled();
    // back to the normal row — the delete affordance is available again
    expect(screen.getByRole("button", { name: "Delete conversation" })).toBeTruthy();
  });
});
