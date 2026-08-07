import { act, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { LauraClient } from "../api";
import { AutoOverviewPanel } from "./AutoOverviewPanel";
import { renderWithQuery } from "../test-utils";

// The auto-overview backend (topic -> montage across SEVERAL videos, own sequence + render)
// shipped 2026-07-31 and was reachable only via curl. This panel is its first UI entry: the
// Shorts tab covers "one video -> one short"; this covers "whole project -> one overview".

function mockClient(overrides: Partial<LauraClient> = {}): LauraClient {
  return {
    autoOverview: vi.fn().mockResolvedValue({
      sequence_id: "seq1",
      source_timeline_id: "tl1",
      clips: [
        {
          asset_id: "a1",
          display_name: "rowboat Uebersicht",
          scene_number: 2,
          start_frame: 100,
          end_frame_exclusive: 400,
          snippet: "AI Meetings transkribieren",
        },
        {
          asset_id: "a2",
          display_name: "n8n Farm",
          scene_number: 1,
          start_frame: 0,
          end_frame_exclusive: 300,
          snippet: "workflow builder",
        },
      ],
      rationale: "both sources demonstrate the topic",
      fallback: false,
      ranking: [],
      warnings: ["left out of the overview, source file missing: AgentFarm"],
      export_id: "e1",
      job_id: "j1",
    }),
    getJob: vi.fn().mockResolvedValue({ id: "j1", status: "running" }),
    ...overrides,
  } as unknown as LauraClient;
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

async function flush(): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

describe("AutoOverviewPanel", () => {
  it("submits the topic and shows clips, rationale and warnings", async () => {
    const client = mockClient();
    renderWithQuery(<AutoOverviewPanel client={client} projectId="p1" />);

    fireEvent.change(screen.getByLabelText("Übersichts-Thema"), {
      target: { value: "Meetings" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Übersicht erstellen" }));
    await flush();

    expect(client.autoOverview).toHaveBeenCalledWith("p1", {
      topic: "Meetings",
      target_seconds: 180,
    });
    expect(screen.getByText(/both sources demonstrate the topic/)).toBeTruthy();
    expect(screen.getByText(/rowboat Uebersicht/)).toBeTruthy();
    expect(screen.getByText(/n8n Farm/)).toBeTruthy();
    expect(screen.getByText(/source file missing: AgentFarm/)).toBeTruthy();
    // The render runs as a job; the panel must say where the film will appear.
    expect(screen.getByText(/Export-Tab/)).toBeTruthy();
  });

  it("a 422 shows the backend's reason instead of crashing", async () => {
    const client = mockClient({
      autoOverview: vi.fn().mockRejectedValue(new Error("422: no material found for topic")),
    });
    renderWithQuery(<AutoOverviewPanel client={client} projectId="p1" />);

    fireEvent.change(screen.getByLabelText("Übersichts-Thema"), { target: { value: "xyz" } });
    fireEvent.click(screen.getByRole("button", { name: "Übersicht erstellen" }));
    await flush();

    expect(screen.getByText(/no material found for topic/)).toBeTruthy();
  });

  it("without a project the panel offers nothing", () => {
    renderWithQuery(<AutoOverviewPanel client={mockClient()} projectId={null} />);
    expect(screen.queryByRole("button", { name: "Übersicht erstellen" })).toBeNull();
  });

  it("an empty topic does not fire a request", async () => {
    const client = mockClient();
    renderWithQuery(<AutoOverviewPanel client={client} projectId="p1" />);

    fireEvent.click(screen.getByRole("button", { name: "Übersicht erstellen" }));
    await flush();

    expect(client.autoOverview).not.toHaveBeenCalled();
  });
});
