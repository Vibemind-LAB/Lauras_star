import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { type ConsentRecord, type LauraClient } from "../api";
import { ReenactPanel } from "./ReenactPanel";

function client(overrides: Partial<LauraClient>): LauraClient {
  const consent: ConsentRecord = {
    id: "consent-1",
    project_id: "project-1",
    subject_label: "Person A",
    source_asset_id: null,
    confirmed_by: null,
    confirmed_at: "2026-06-13T00:00:00Z",
    note: null,
    revoked_at: null,
  };
  return {
    createConsent: vi.fn().mockResolvedValue(consent),
    reenact: vi.fn().mockResolvedValue({ job_id: "job-1" }),
    ...overrides,
  } as unknown as LauraClient;
}

describe("ReenactPanel", () => {
  it("passes liveportrait when the LivePortrait backend is selected", async () => {
    const reenact = vi.fn().mockResolvedValue({ job_id: "job-1" });
    const c = client({ reenact });

    render(
      <ReenactPanel
        client={c}
        projectId="project-1"
        timelineId="timeline-1"
        assets={[{ id: "asset-1", display_name: "Portrait" }]}
        onChange={vi.fn()}
        currentSeqFrame={0}
        rateNum={30}
        rateDen={1}
      />,
    );

    fireEvent.change(screen.getByLabelText("Subjekt-Label für Consent"), {
      target: { value: "Person A" },
    });
    fireEvent.click(screen.getByRole("button", { name: /consent bestätigen/i }));
    await waitFor(() => expect(screen.getByText(/Consent für/)).toBeTruthy());

    fireEvent.change(screen.getByLabelText("Reenact-Backend auswählen"), {
      target: { value: "liveportrait" },
    });
    fireEvent.change(screen.getByLabelText("Sequenz-Auspunkt exklusiv (Frames)"), {
      target: { value: "25" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Reenact/ }));

    await waitFor(() =>
      expect(reenact).toHaveBeenCalledWith("timeline-1", {
        seqIn: 0,
        seqOut: 25,
        portraitAssetId: "asset-1",
        consentId: "consent-1",
        backend: "liveportrait",
      }),
    );
  });
});
