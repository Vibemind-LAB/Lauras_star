import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Export, LauraClient } from "../api";
import type { ExportTarget } from "../App";
import { ExportView } from "./ExportView";

afterEach(() => vi.restoreAllMocks());

const exp = (over: Partial<Export>): Export => ({
  id: "e1", project_id: "p1", timeline_id: "t1", format: "mp4", status: "ready",
  path: "/x/out.mp4", size_bytes: 1048576, error: null, created_at: "2026", ...over,
});

const TARGET: ExportTarget = { id: "t1", label: "Rough Cut: video.mp4", kind: "rough_cut" };

function mkClient(over: Partial<LauraClient>): LauraClient {
  return {
    listExports: vi.fn().mockResolvedValue([]),
    renderTimeline: vi.fn().mockResolvedValue({ export_id: "e", job_id: "j" }),
    renderReel: vi.fn().mockResolvedValue({ export_id: "e", job_id: "j" }),
    getSequenceFlattened: vi.fn().mockResolvedValue([]),
    getTimeline: vi.fn().mockResolvedValue({
      id: "t1", project_id: "p1", name: "t", kind: "rough_cut", created_at: "", clips: [],
    }),
    assetFrameUrl: vi.fn().mockResolvedValue("blob:poster"),
    ...over,
  } as unknown as LauraClient;
}

describe("ExportView", () => {
  it("lists a ready export with its size", async () => {
    const client = mkClient({ listExports: vi.fn().mockResolvedValue([exp({})]) });
    render(<ExportView client={client} projectId="p1" project={null} exportTargets={[TARGET]} />);
    await waitFor(() => expect(screen.getByText(/MP4/)).toBeTruthy());
    expect(screen.getByText(/1\.0 MiB/)).toBeTruthy();
  });

  it("calls renderTimeline when Export is clicked", async () => {
    const renderTimeline = vi.fn().mockResolvedValue({ export_id: "e", job_id: "j" });
    const client = mkClient({ renderTimeline });
    render(<ExportView client={client} projectId="p1" project={null} exportTargets={[TARGET]} />);
    fireEvent.click(screen.getByRole("button", { name: /exportieren/i }));
    await waitFor(() => expect(renderTimeline).toHaveBeenCalledWith("t1", "mp4"));
  });

  it("defaults to the first non-empty source (skips the empty assembled sequence)", async () => {
    const SEQ: ExportTarget = { id: "seq1", label: "Sequenz (Zusammenfügen)", kind: "sequence" };
    const RC: ExportTarget = { id: "rc1", label: "Rough Cut: video.mp4", kind: "rough_cut" };
    // The sequence resolves via /flattened (empty); the rough cut carries clips directly.
    const getTimeline = vi.fn(async (id: string) => ({
      id, project_id: "p1", name: "rc", kind: "rough_cut", created_at: "",
      clips: id === "rc1" ? [{ asset_id: "a", seq_out_frame_exclusive: 100 }] : [],
    }));
    const client = mkClient({
      getSequenceFlattened: vi.fn().mockResolvedValue([]),
      getTimeline: getTimeline as unknown as LauraClient["getTimeline"],
    });
    render(<ExportView client={client} projectId="p1" project={null} exportTargets={[SEQ, RC]} />);
    // The populated rough cut is auto-selected even though the empty sequence is listed first.
    await waitFor(() => {
      const rc = screen.getByRole("radio", { name: /Rough Cut/ }) as HTMLInputElement;
      expect(rc.checked).toBe(true);
    });
    expect((screen.getByRole("radio", { name: /Sequenz/ }) as HTMLInputElement).checked).toBe(false);
  });

  it("sends caption direction options for reel export", async () => {
    const renderReel = vi.fn().mockResolvedValue({ export_id: "e", job_id: "j" });
    const client = mkClient({ renderReel });
    render(<ExportView client={client} projectId="p1" project={null} exportTargets={[TARGET]} />);

    fireEvent.change(screen.getByLabelText("Caption-Preset"), { target: { value: "reels" } });
    fireEvent.change(screen.getByLabelText("Caption-Modus"), { target: { value: "normal" } });
    fireEvent.change(screen.getByLabelText("Caption-Position"), { target: { value: "top" } });
    fireEvent.change(screen.getByLabelText("Caption-Groesse"), { target: { value: "84" } });
    fireEvent.change(screen.getByLabelText("Safe-Zone"), { target: { value: "180" } });
    fireEvent.click(screen.getByRole("button", { name: /Reel 9:16/i }));

    await waitFor(() => expect(renderReel).toHaveBeenCalledWith("t1", {
      hookText: null,
      disclosureText: "KI · synthetisch",
      vertical: true,
      captions: true,
      captionPreset: "reels",
      captionMode: "normal",
      captionPosition: "top",
      captionFontsize: 84,
      captionSafeMargin: 180,
    }));
  });
});
