import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { LauraClient } from "../api";
import { AutoPilotPanel } from "./AutoPilotPanel";

function mockClient(autoPilot: ReturnType<typeof vi.fn>): LauraClient {
  return { autoPilot } as unknown as LauraClient;
}

describe("AutoPilotPanel", () => {
  it("drives to export: re-calls autoPilot until terminal, then onChanged", async () => {
    const autoPilot = vi
      .fn()
      .mockResolvedValueOnce({ status: "blocked" })
      .mockResolvedValueOnce({ status: "target_reached" });
    const onChanged = vi.fn();
    render(
      <AutoPilotPanel client={mockClient(autoPilot)} assetId="a1" onChanged={onChanged} pollMs={0} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "→ Export" }));

    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(autoPilot).toHaveBeenCalledTimes(2); // blocked → re-call → target_reached
    expect(autoPilot).toHaveBeenNthCalledWith(1, "a1", "render");
  });

  it("→ Rough-Cut targets roughcut", async () => {
    const autoPilot = vi.fn().mockResolvedValue({ status: "target_reached" });
    render(<AutoPilotPanel client={mockClient(autoPilot)} assetId="a1" pollMs={0} />);

    fireEvent.click(screen.getByRole("button", { name: "→ Rough-Cut" }));

    await waitFor(() => expect(autoPilot).toHaveBeenCalledWith("a1", "roughcut"));
  });
});
