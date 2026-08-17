import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ContactSheetGateStatus, LauraClient } from "../../api";
import { ContactSheetApprovalCard } from "./ContactSheetApprovalCard";

type ContactSheetClient = Pick<LauraClient, "assetFrameUrl" | "confirmContactSheet">;

const gate: ContactSheetGateStatus = {
  enabled: true,
  approved: false,
  pending: true,
  current_sheet_hash: "b".repeat(64),
  tiles: [
    {
      order: 0,
      scene_number: 3,
      frame: 45,
      label: "0 S3",
      src_start_frame: 15,
      src_end_frame_exclusive: 75,
      narration_excerpt: "Rowboat ordnet die Dateien.",
      rationale: "Die Dateiansicht belegt die Aussage.",
    },
  ],
};

function client(overrides: Partial<ContactSheetClient> = {}): ContactSheetClient {
  return {
    assetFrameUrl: vi.fn().mockReturnValue(new Promise<string>(() => undefined)),
    confirmContactSheet: vi.fn().mockResolvedValue({ session_id: "s1", job_id: "j3" }),
    ...overrides,
  };
}

describe("ContactSheetApprovalCard", () => {
  it("renders end-exclusive In/Out metadata and confirms the displayed hash", async () => {
    const confirmContactSheet = vi
      .fn()
      .mockResolvedValue({ session_id: "s1", job_id: "j3" });
    const onConfirmed = vi.fn().mockResolvedValue(undefined);

    render(
      <ContactSheetApprovalCard
        gate={gate}
        sessionId="s1"
        client={client({ confirmContactSheet })}
        onConfirmed={onConfirmed}
      />,
    );

    expect(screen.getByText("In 15 · Out 75")).toBeTruthy();
    expect(screen.getByText("Rowboat ordnet die Dateien.")).toBeTruthy();
    expect(screen.getByText("Die Dateiansicht belegt die Aussage.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Kontaktbogen freigeben" }));
    await waitFor(() => expect(onConfirmed).toHaveBeenCalledTimes(1));
    expect(confirmContactSheet).toHaveBeenCalledWith("s1", "b".repeat(64));
  });

  it("keeps a stale-hash conflict visible and does not refresh", async () => {
    const confirmContactSheet = vi
      .fn()
      .mockRejectedValue(new Error("409: stale contact sheet"));
    const onConfirmed = vi.fn().mockResolvedValue(undefined);

    render(
      <ContactSheetApprovalCard
        gate={gate}
        sessionId="s1"
        client={client({ confirmContactSheet })}
        onConfirmed={onConfirmed}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Kontaktbogen freigeben" }));

    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "409: stale contact sheet");
    expect(onConfirmed).not.toHaveBeenCalled();
  });
});
