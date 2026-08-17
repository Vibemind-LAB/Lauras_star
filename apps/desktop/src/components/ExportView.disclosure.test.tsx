import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { LauraClient } from "../api";
import type { ExportTarget } from "../App";
import { ExportView } from "./ExportView";

const TARGET: ExportTarget = { id: "t1", label: "Rough Cut: video.mp4", kind: "rough_cut" };

function makeExportViewProps() {
  const client: LauraClient = {
    listExports: vi.fn().mockResolvedValue([]),
    renderTimeline: vi.fn().mockResolvedValue({ export_id: "e", job_id: "j" }),
    renderReel: vi.fn().mockResolvedValue({ export_id: "e", job_id: "j" }),
    getSequenceFlattened: vi.fn().mockResolvedValue([]),
    getTimeline: vi.fn().mockResolvedValue({
      id: "t1", project_id: "p1", name: "t", kind: "rough_cut", created_at: "", clips: [],
    }),
    assetFrameUrl: vi.fn().mockResolvedValue("blob:poster"),
  } as unknown as LauraClient;

  return {
    client,
    projectId: "p1",
    project: null,
    exportTargets: [TARGET],
  };
}

describe("ExportView disclosure is mandatory", () => {
  it("has no off-switch for the KI disclosure", () => {
    render(<ExportView {...makeExportViewProps()} />);
    expect(screen.queryByLabelText(/KI-Kennzeichnung einblenden/i)).toBeNull();
  });

  it("shows a persistent disclosure confirmation", () => {
    render(<ExportView {...makeExportViewProps()} />);
    expect(screen.getByText(/AI disclosure is always shown/i)).toBeTruthy();
  });
});
