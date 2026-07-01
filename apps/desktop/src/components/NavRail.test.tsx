import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { NavRail } from "./NavRail";
describe("NavRail", () => {
  it("renders all seven stages and marks the active one", () => {
    render(<NavRail active="import" onSelect={vi.fn()} />);
    expect(screen.getByText("Download")).toBeTruthy();
    expect(screen.getByText("Export")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Import" }).getAttribute("aria-current")).toBe("page");
  });
  it("calls onSelect with the stage id when clicked", () => {
    const onSelect = vi.fn();
    render(<NavRail active="download" onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: "Export" }));
    expect(onSelect).toHaveBeenCalledWith("export");
  });
});
