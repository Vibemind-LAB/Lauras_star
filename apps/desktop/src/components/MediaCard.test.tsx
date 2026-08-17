import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ImportStatus, LauraClient } from "../api";
import { MediaCard } from "./MediaCard";
const dl = (over: Partial<ImportStatus>): ImportStatus => ({
  phase: "downloading", downloaded_bytes: null, total_bytes: null, speed_bps: null, eta_seconds: null, error: null, ...over,
});
describe("MediaCard", () => {
  it("renders title and meta", () => {
    render(<MediaCard title="Clip A" meta="MP4 · 1080p" onClick={vi.fn()} onRetry={vi.fn()} />);
    expect(screen.getByText("Clip A")).toBeTruthy();
    expect(screen.getByText("MP4 · 1080p")).toBeTruthy();
  });
  it("fires onClick when the card body is clicked", () => {
    const onClick = vi.fn();
    render(<MediaCard title="Clip A" onClick={onClick} onRetry={vi.fn()} />);
    fireEvent.click(screen.getByText("Clip A"));
    expect(onClick).toHaveBeenCalledOnce();
  });
  it("shows a progress footer when status is non-terminal", () => {
    render(<MediaCard title="Clip B" status={dl({ downloaded_bytes: 50, total_bytes: 100, speed_bps: 10 })} onClick={vi.fn()} onRetry={vi.fn()} />);
    expect(screen.getByText(/50%/)).toBeTruthy();
  });
  it("calls onCancel when Cancel is clicked on an active card", () => {
    const cancelImport = vi.fn().mockResolvedValue(undefined);
    const fakeClient = { cancelImport } as unknown as LauraClient;
    const onCancel = () => void fakeClient.cancelImport("asset-1");
    render(
      <MediaCard
        title="Clip C"
        status={dl({ phase: "downloading" })}
        onClick={vi.fn()}
        onRetry={vi.fn()}
        onCancel={onCancel}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(cancelImport).toHaveBeenCalledWith("asset-1");
  });
  it("shows Abgebrochen state when phase is cancelled", () => {
    render(
      <MediaCard
        title="Clip D"
        status={dl({ phase: "cancelled" })}
        onClick={vi.fn()}
        onRetry={vi.fn()}
      />,
    );
    expect(screen.getByText(/abgebrochen/i)).toBeTruthy();
  });
});
