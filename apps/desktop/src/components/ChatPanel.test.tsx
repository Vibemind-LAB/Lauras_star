import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AgentEvent, LauraClient } from "../api";
import { ChatPanel, pickHighlights } from "./ChatPanel";

function mockClient(streamAutoShort: ReturnType<typeof vi.fn>): LauraClient {
  return { streamAutoShort } as unknown as LauraClient;
}

function renderWithEvents(events: AgentEvent[]): ReturnType<typeof vi.fn> {
  const streamAutoShort = vi.fn(
    (_assetId: string, _req: { topic: string }, onEvent: (e: AgentEvent) => void) => {
      for (const e of events) onEvent(e);
      return Promise.resolve();
    },
  );
  render(<ChatPanel client={mockClient(streamAutoShort)} assetId="a1" />);
  fireEvent.change(screen.getByLabelText("Anfrage"), { target: { value: "Katzen" } });
  fireEvent.click(screen.getByRole("button", { name: "Los" }));
  return streamAutoShort;
}

const DONE_OK: AgentEvent = {
  type: "done",
  ok: true,
  stage: "A",
  team: "magentic",
  weak: false,
  escalated: false,
  summary: "",
};

describe("ChatPanel", () => {
  it("streams a request and renders the agent events + user bubble", async () => {
    const streamAutoShort = renderWithEvents([
      { type: "stage", stage: "A", team: "magentic" },
      { type: "agent", agent: "scout", text: "suche Momente" },
      DONE_OK,
    ]);

    await waitFor(() => expect(streamAutoShort).toHaveBeenCalledTimes(1));
    expect(streamAutoShort.mock.calls[0][0]).toBe("a1");
    expect(streamAutoShort.mock.calls[0][1]).toEqual({ topic: "Katzen" });
    expect(screen.getByText(/Katzen/)).toBeTruthy();
    expect(screen.getByText(/Scout/)).toBeTruthy();
    expect(screen.getByText(/suche Momente/)).toBeTruthy();
    expect(screen.getByText(/Short fertig/)).toBeTruthy();
  });

  it("resets running when the stream ends without a terminal event", async () => {
    // Abrupt end: the stream promise resolves without ever emitting done/error.
    let resolve!: () => void;
    const streamAutoShort = vi.fn(() => new Promise<void>((r) => (resolve = r)));
    render(<ChatPanel client={mockClient(streamAutoShort)} assetId="a1" />);

    fireEvent.change(screen.getByLabelText("Anfrage"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Los" }));
    expect((screen.getByLabelText("Anfrage") as HTMLInputElement).disabled).toBe(true);

    resolve();
    await waitFor(() =>
      expect((screen.getByLabelText("Anfrage") as HTMLInputElement).disabled).toBe(false),
    );
  });

  it("forwards every event to onEvent (for live view refresh)", async () => {
    const streamAutoShort = vi.fn(
      (_assetId: string, _req: { topic: string }, onEvent: (e: AgentEvent) => void) => {
        onEvent({ type: "artifact", kind: "roughcut", id: "t1" });
        onEvent(DONE_OK);
        return Promise.resolve();
      },
    );
    const onEvent = vi.fn();
    render(<ChatPanel client={mockClient(streamAutoShort)} assetId="a1" onEvent={onEvent} />);

    fireEvent.change(screen.getByLabelText("Anfrage"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Los" }));

    await waitFor(() => expect(onEvent).toHaveBeenCalledTimes(2));
    expect(onEvent.mock.calls[0][0]).toEqual({ type: "artifact", kind: "roughcut", id: "t1" });
  });

  it("disables the input and send button when no asset is selected", () => {
    render(<ChatPanel client={mockClient(vi.fn())} assetId={null} />);
    expect((screen.getByLabelText("Anfrage") as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Los" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("surfaces the key facts of a tool result and hides the raw dump behind details", async () => {
    renderWithEvents([
      {
        type: "tool_result",
        tool: "render_short",
        ok: true,
        summary: "{'ok': True, 'export_id': 'abc123', 'status': 'ready', 'path': '/x.mp4'}",
      },
      DONE_OK,
    ]);

    await waitFor(() => expect(screen.getByText(/export_id=abc123/)).toBeTruthy());
    expect(screen.getByText(/status=ready/)).toBeTruthy();
    // The full raw summary stays available (collapsed).
    expect(screen.getByText(/'path': '\/x\.mp4'/)).toBeTruthy();
  });

  it("renders a SKIP reply as one quiet line instead of a bubble", async () => {
    renderWithEvents([
      { type: "agent", agent: "transcript_master", text: "SKIP." },
      DONE_OK,
    ]);
    await waitFor(() => expect(screen.getByText(/überspringt/)).toBeTruthy());
  });

  it("collapses the long task echo (agent 'user') behind a summary", async () => {
    renderWithEvents([
      { type: "agent", agent: "user", text: "Create a ~60s vertical short about ..." },
      DONE_OK,
    ]);
    await waitFor(() => expect(screen.getByText("📋 Auftrag ans Team")).toBeTruthy());
  });

  it("clamps long agent prose behind a 'mehr anzeigen' toggle", async () => {
    const long = `Anfang ${"blah ".repeat(120)}ENDE`;
    renderWithEvents([{ type: "agent", agent: "director", text: long }, DONE_OK]);

    await waitFor(() => expect(screen.getByText(/mehr anzeigen/)).toBeTruthy());
    expect(screen.queryByText(/ENDE/)).toBeNull();
    fireEvent.click(screen.getByText(/mehr anzeigen/));
    expect(screen.getByText(/ENDE/)).toBeTruthy();
    expect(screen.getByText(/weniger anzeigen/)).toBeTruthy();
  });

  it("shows a weak verdict as its own tone, not as plain success", async () => {
    renderWithEvents([
      {
        type: "done",
        ok: true,
        stage: "A",
        team: "graph",
        weak: true,
        escalated: false,
        summary: "qa said weak",
      },
    ]);
    await waitFor(() => expect(screen.getByText(/QA meldet Schwächen/)).toBeTruthy());
    expect(screen.queryByText(/✓ Short fertig/)).toBeNull();
  });
});

describe("pickHighlights", () => {
  it("extracts known keys from a python-dict summary", () => {
    const facts = pickHighlights(
      "{'ok': True, 'export_id': 'e9', 'count': 575, 'reason': 'no candidates'}",
    );
    expect(facts).toContain("export_id=e9");
    expect(facts).toContain("count=575");
    expect(facts).toContain("reason=no candidates");
  });

  it("returns an empty string for unknown shapes and skips None values", () => {
    expect(pickHighlights("plain text")).toBe("");
    expect(pickHighlights("{'error': None}")).toBe("");
  });
});
