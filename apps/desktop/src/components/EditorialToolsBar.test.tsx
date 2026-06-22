import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EditorialToolsBar } from "./EditorialToolsBar";

// Note: @testing-library/jest-dom is not installed in this project.
// We use native DOM property checks (.disabled, .textContent) consistent with
// the rest of the test suite (LipsyncPanel.test.tsx etc.).

const voices = [{ name: "Hedda", culture: "de-DE", gender: "Female" }];

describe("EditorialToolsBar", () => {
  it("shows the always-on synthetic-content disclosure with effects", () => {
    render(
      <EditorialToolsBar
        voices={voices} voiceId={null} onVoiceChange={() => {}}
        pendingEdge={null} onSmooth={() => {}} onReenact={() => {}}
        syntheticEffects={["Stimme", "Lippensync"]}
      />,
    );
    const disclosure = screen.getByText(/Enthält synthetische Inhalte/i);
    expect(disclosure.textContent).toContain("Stimme");
    expect(disclosure.textContent).toContain("Lippensync");
  });

  it("disables smooth until an edge is marked, enables + fires when present", () => {
    const onSmooth = vi.fn();
    const { rerender } = render(
      <EditorialToolsBar
        voices={voices} voiceId={null} onVoiceChange={() => {}}
        pendingEdge={null} onSmooth={onSmooth} onReenact={() => {}}
        syntheticEffects={[]}
      />,
    );
    expect((screen.getByRole("button", { name: /glätten/i }) as HTMLButtonElement).disabled).toBe(true);
    rerender(
      <EditorialToolsBar
        voices={voices} voiceId={null} onVoiceChange={() => {}}
        pendingEdge={{ asset_a: "A", asset_b: "A", src_out_a: 10, src_in_b: 10 }}
        onSmooth={onSmooth} onReenact={() => {}} syntheticEffects={[]}
      />,
    );
    const btn = screen.getByRole("button", { name: /glätten/i }) as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
    fireEvent.click(btn);
    expect(onSmooth).toHaveBeenCalledTimes(1);
  });

  it("fires onVoiceChange with the picked voice (null for Auto)", () => {
    const onVoiceChange = vi.fn();
    render(
      <EditorialToolsBar
        voices={voices} voiceId={null} onVoiceChange={onVoiceChange}
        pendingEdge={null} onSmooth={() => {}} onReenact={() => {}}
        syntheticEffects={[]}
      />,
    );
    fireEvent.change(screen.getByLabelText(/Stimme/i), { target: { value: "Hedda" } });
    expect(onVoiceChange).toHaveBeenCalledWith("Hedda");
  });
});
