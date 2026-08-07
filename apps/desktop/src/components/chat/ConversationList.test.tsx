import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ConversationSummary } from "../../api";
import { ConversationList } from "./ConversationList";

function summary(overrides: Partial<ConversationSummary> = {}): ConversationSummary {
  return {
    id: "c1",
    title: "Erster Chat",
    updated_at: "2026-08-03T00:00:00Z",
    ...overrides,
  };
}

describe("ConversationList", () => {
  it("shows the empty state when there are no conversations", () => {
    render(
      <ConversationList items={[]} activeId={null} onSelect={vi.fn()} onNew={vi.fn()} onDelete={vi.fn()} />,
    );
    expect(screen.getByText("Noch keine Unterhaltungen")).toBeTruthy();
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
    fireEvent.click(screen.getByRole("button", { name: "Neuer Chat" }));
    expect(onNew).toHaveBeenCalledOnce();
  });

  it("delete requires a confirm step — one click never fires onDelete", () => {
    const onDelete = vi.fn();
    const items = [summary({ id: "c1", title: "Erster Chat" })];
    render(
      <ConversationList items={items} activeId={null} onSelect={vi.fn()} onNew={vi.fn()} onDelete={onDelete} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Unterhaltung löschen" }));
    expect(onDelete).not.toHaveBeenCalled();
  });

  it("confirming the second step fires onDelete with the conversation id", () => {
    const onDelete = vi.fn();
    const onSelect = vi.fn();
    const items = [summary({ id: "c1", title: "Erster Chat" })];
    render(
      <ConversationList items={items} activeId={null} onSelect={onSelect} onNew={vi.fn()} onDelete={onDelete} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Unterhaltung löschen" }));
    fireEvent.click(screen.getByRole("button", { name: "Wirklich löschen?" }));
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
    fireEvent.click(screen.getByRole("button", { name: "Unterhaltung löschen" }));
    fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));
    expect(onDelete).not.toHaveBeenCalled();
    // back to the normal row — the delete affordance is available again
    expect(screen.getByRole("button", { name: "Unterhaltung löschen" })).toBeTruthy();
  });
});
