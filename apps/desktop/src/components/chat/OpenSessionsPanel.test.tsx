import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { OpenProductionSession } from "../../api";
import { OpenSessionsPanel } from "./OpenSessionsPanel";

function session(
  overrides: Partial<OpenProductionSession> = {},
): OpenProductionSession {
  return {
    session_id: "s1",
    conversation_id: "c1",
    project_id: "p1",
    asset_id: "a1",
    asset_display_name: "Rough Cut",
    brief_preview: "Baue den visuellen Schnitt weiter",
    resume_point: "visual_selection",
    state: "awaiting-approval",
    updated_utc: "2026-08-17T10:00:00+00:00",
    draft_updated_utc: "2026-08-17T09:59:00+00:00",
    latest_job_id: "j1",
    stale: false,
    stale_reason: null,
    ...overrides,
  };
}

describe("OpenSessionsPanel", () => {
  it("stays compact without a false resume action when no session is open", () => {
    render(<OpenSessionsPanel sessions={[]} onResume={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /Fortsetzen/ })).toBeNull();
  });

  it("shows one prominent resumable session with brief and saved time", () => {
    render(<OpenSessionsPanel sessions={[session()]} onResume={vi.fn()} />);
    expect(screen.getByText("Baue den visuellen Schnitt weiter")).toBeTruthy();
    expect(screen.getByText(/Gespeichert.*2026-08-17T09:59/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Fortsetzen" })).toBeTruthy();
  });

  it("sorts multiple sessions newest first and exposes their state", () => {
    render(
      <OpenSessionsPanel
        sessions={[
          session({ session_id: "old", brief_preview: "Alt", updated_utc: "2026-08-16" }),
          session({
            session_id: "new",
            brief_preview: "Neu",
            updated_utc: "2026-08-17",
            state: "running",
          }),
        ]}
        onResume={vi.fn()}
      />,
    );
    const buttons = screen.getAllByRole("button", { name: /Fortsetzen/ });
    expect(buttons[0].textContent).toContain("Neu");
    expect(buttons[0].textContent).toContain("running");
    expect(buttons[1].textContent).toContain("Alt");
  });

  it("warns for stale sessions and returns the complete session on click", () => {
    const stale = session({ stale: true, stale_reason: "source_content_changed" });
    const onResume = vi.fn();
    render(<OpenSessionsPanel sessions={[stale]} onResume={onResume} />);

    expect(screen.getByText(/Quelldatei oder Vorschlag hat sich geändert/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Fortsetzen" }));
    expect(onResume).toHaveBeenCalledWith(stale);
  });
});
