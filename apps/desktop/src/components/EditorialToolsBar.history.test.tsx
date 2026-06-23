import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EditorialToolsBar } from "./EditorialToolsBar";

// Note: @testing-library/jest-dom is not installed in this project.
// We use native DOM property checks (.disabled) consistent with the sibling test
// (EditorialToolsBar.test.tsx).

describe("EditorialToolsBar undo/redo buttons", () => {
  it("renders ↶ Rückgängig button as disabled when canUndo is false (default)", () => {
    render(<EditorialToolsBar />);
    const btn = screen.getByRole("button", { name: /Rückgängig/i }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it("renders ↷ Wiederholen button as disabled when canRedo is false (default)", () => {
    render(<EditorialToolsBar />);
    const btn = screen.getByRole("button", { name: /Wiederholen/i }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it("enables ↶ and calls onUndo when canUndo is true and button is clicked", () => {
    const onUndo = vi.fn();
    render(<EditorialToolsBar canUndo={true} onUndo={onUndo} />);
    const btn = screen.getByRole("button", { name: /Rückgängig/i }) as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
    fireEvent.click(btn);
    expect(onUndo).toHaveBeenCalledTimes(1);
  });

  it("enables ↷ and calls onRedo when canRedo is true and button is clicked", () => {
    const onRedo = vi.fn();
    render(<EditorialToolsBar canRedo={true} onRedo={onRedo} />);
    const btn = screen.getByRole("button", { name: /Wiederholen/i }) as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
    fireEvent.click(btn);
    expect(onRedo).toHaveBeenCalledTimes(1);
  });

  it("shows undoLabel in title when provided", () => {
    render(<EditorialToolsBar canUndo={true} undoLabel="delete scene" />);
    const btn = screen.getByRole("button", { name: /Rückgängig/i }) as HTMLButtonElement;
    expect(btn.title).toBe("Rückgängig: delete scene");
  });

  it("shows generic title when undoLabel is null", () => {
    render(<EditorialToolsBar canUndo={true} undoLabel={null} />);
    const btn = screen.getByRole("button", { name: /Rückgängig/i }) as HTMLButtonElement;
    expect(btn.title).toBe("Rückgängig");
  });
});
