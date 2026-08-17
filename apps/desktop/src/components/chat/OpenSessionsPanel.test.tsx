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
    brief_preview: "Continuing the visual cut",
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
    expect(screen.queryByRole("button", { name: /Resume/ })).toBeNull();
  });

  it("shows one prominent resumable session with brief and saved time", () => {
    render(<OpenSessionsPanel sessions={[session()]} onResume={vi.fn()} />);
    expect(screen.getByText("Continuing the visual cut")).toBeTruthy();
    expect(screen.getByText(/Saved.*2026-08-17T09:59/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Resume" })).toBeTruthy();
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
    const buttons = screen.getAllByRole("button", { name: /Resume/ });
    expect(buttons[0].textContent).toContain("Neu");
    expect(buttons[0].textContent).toContain("running");
    expect(buttons[1].textContent).toContain("Alt");
  });

  it("warns for stale sessions and returns the complete session on click", () => {
    const stale = session({ stale: true, stale_reason: "source_content_changed" });
    const onResume = vi.fn();
    render(<OpenSessionsPanel sessions={[stale]} onResume={onResume} />);

    expect(screen.getByText(/The source file or the proposal changed/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    expect(onResume).toHaveBeenCalledWith(stale);
  });
});

describe("OpenSessionsPanel delete", () => {
  it("asks before deleting and says the video survives", () => {
    const onDelete = vi.fn();
    render(
      <OpenSessionsPanel sessions={[session()]} onResume={vi.fn()} onDelete={onDelete} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^Delete production/ }));
    expect(onDelete).not.toHaveBeenCalled(); // one click only arms the confirm
    expect(screen.getByText("the video stays")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Delete production" }));
    expect(onDelete).toHaveBeenCalledWith(expect.objectContaining({ session_id: "s1" }));
  });

  it("takes the confirmation back", () => {
    const onDelete = vi.fn();
    render(
      <OpenSessionsPanel sessions={[session()]} onResume={vi.fn()} onDelete={onDelete} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^Delete production/ }));
    fireEvent.click(screen.getByRole("button", { name: "Keep" }));

    expect(onDelete).not.toHaveBeenCalled();
    expect(screen.queryByText("the video stays")).toBeNull();
  });

  it("shows no delete affordance without a handler", () => {
    render(<OpenSessionsPanel sessions={[session()]} onResume={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /Delete/ })).toBeNull();
  });

  it("caps its own height so a long list cannot push the conversations out of the sidebar", () => {
    const many = Array.from({ length: 17 }, (_, i) => session({ session_id: `s${i}` }));
    const { container } = render(<OpenSessionsPanel sessions={many} onResume={vi.fn()} />);

    const list = container.querySelector(".overflow-y-auto");
    expect(list?.className).toContain("max-h-");
    expect(screen.getByText("17")).toBeTruthy(); // the count is visible without scrolling
  });
});
