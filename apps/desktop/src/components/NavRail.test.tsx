import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { NavRail } from "./NavRail";
describe("NavRail", () => {
  it("renders all seven stages and marks the active one", () => {
    render(<NavRail active="media" onSelect={vi.fn()} />);
    expect(screen.getByText("Media")).toBeTruthy();
    expect(screen.getByText("Export")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Media" }).getAttribute("aria-current")).toBe("page");
  });
  it("calls onSelect with the stage id when clicked", () => {
    const onSelect = vi.fn();
    render(<NavRail active="media" onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: "Export" }));
    expect(onSelect).toHaveBeenCalledWith("export");
  });
});
