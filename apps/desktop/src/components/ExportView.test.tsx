import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Export, LauraClient } from "../api";
import { ExportView } from "./ExportView";

afterEach(() => vi.restoreAllMocks());

const exp = (over: Partial<Export>): Export => ({
  id: "e1", project_id: "p1", timeline_id: "t1", format: "mp4", status: "ready",
  path: "/x/out.mp4", size_bytes: 1048576, error: null, created_at: "2026", ...over,
});

function mkClient(over: Partial<LauraClient>): LauraClient {
  return { listExports: vi.fn().mockResolvedValue([]), renderTimeline: vi.fn().mockResolvedValue({ export_id: "e", job_id: "j" }), ...over } as unknown as LauraClient;
}

describe("ExportView", () => {
  it("lists a ready export with its size", async () => {
    const client = mkClient({ listExports: vi.fn().mockResolvedValue([exp({})]) });
    render(<ExportView client={client} projectId="p1" timelineId="t1" />);
    await waitFor(() => expect(screen.getByText(/MP4/)).toBeTruthy());
    expect(screen.getByText(/1\.0 MiB/)).toBeTruthy();
  });

  it("calls renderTimeline when Export is clicked", async () => {
    const renderTimeline = vi.fn().mockResolvedValue({ export_id: "e", job_id: "j" });
    const client = mkClient({ renderTimeline });
    render(<ExportView client={client} projectId="p1" timelineId="t1" />);
    fireEvent.click(screen.getByRole("button", { name: /exportieren/i }));
    await waitFor(() => expect(renderTimeline).toHaveBeenCalledWith("t1", "mp4"));
  });
});
