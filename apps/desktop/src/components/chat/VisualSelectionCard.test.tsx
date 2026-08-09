import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { LauraClient, VisualSelectionGateStatus } from "../../api";
import { VisualSelectionCard } from "./VisualSelectionCard";

type VisualSelectionClient = Pick<
  LauraClient,
  "assetFrameUrl" | "confirmVisualSelection"
>;

function candidate(
  beatId: string,
  candidateId: string,
  sceneNumber: number,
  thumbFrame: number,
) {
  return {
    candidate_id: candidateId,
    beat_id: beatId,
    voice_segment_index: beatId === "beat-1" ? 0 : 1,
    scene_number: sceneNumber,
    window_index: 0,
    src_start_frame: thumbFrame - 30,
    src_end_frame_exclusive: thumbFrame + 30,
    thumb_frame: thumbFrame,
    description: `Szene ${sceneNumber}`,
    transcript_snippet: `Transkript ${sceneNumber}`,
    rationale: `Begründung ${sceneNumber}`,
    score: 0.9,
  };
}

const gate: VisualSelectionGateStatus = {
  enabled: true,
  approved: false,
  pending: true,
  proposal_id: "a".repeat(64),
  beats: [
    {
      beat_id: "beat-1",
      voice_segment_index: 0,
      narration_text: "Rowboat organisiert Dateien.",
      duration_s: 2.5,
      candidates: [candidate("beat-1", "candidate-1", 2, 42), candidate("beat-1", "candidate-2", 4, 142)],
      recommended_candidate_id: "candidate-1",
      selected_candidate_id: null,
    },
    {
      beat_id: "beat-2",
      voice_segment_index: 1,
      narration_text: "Danach baut es Präsentationen.",
      duration_s: 3,
      candidates: [candidate("beat-2", "candidate-3", 6, 242)],
      recommended_candidate_id: "candidate-3",
      selected_candidate_id: null,
    },
  ],
};

function client(
  overrides: Partial<VisualSelectionClient> = {},
): VisualSelectionClient {
  return {
    assetFrameUrl: vi.fn().mockReturnValue(new Promise<string>(() => undefined)),
    confirmVisualSelection: vi.fn().mockResolvedValue({ session_id: "s1", job_id: "j2" }),
    ...overrides,
  };
}

describe("VisualSelectionCard", () => {
  it("preselects one recommendation per beat and replaces only that beat's choice", async () => {
    const confirmVisualSelection = vi
      .fn()
      .mockResolvedValue({ session_id: "s1", job_id: "j2" });
    const assetFrameUrl = vi.fn().mockReturnValue(new Promise<string>(() => undefined));
    const onConfirmed = vi.fn().mockResolvedValue(undefined);
    const c = client({ confirmVisualSelection, assetFrameUrl });

    render(
      <VisualSelectionCard
        gate={gate}
        assetId="asset-1"
        sessionId="s1"
        client={c}
        onConfirmed={onConfirmed}
      />,
    );

    expect((screen.getByTestId("visual-candidate-candidate-1") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByTestId("visual-candidate-candidate-3") as HTMLInputElement).checked).toBe(true);
    expect(assetFrameUrl).toHaveBeenCalledWith("asset-1", 42);

    fireEvent.click(screen.getByTestId("visual-candidate-candidate-2"));
    expect((screen.getByTestId("visual-candidate-candidate-1") as HTMLInputElement).checked).toBe(false);
    expect((screen.getByTestId("visual-candidate-candidate-2") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByTestId("visual-candidate-candidate-3") as HTMLInputElement).checked).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Bildauswahl übernehmen" }));
    await waitFor(() => expect(onConfirmed).toHaveBeenCalledTimes(1));
    expect(confirmVisualSelection).toHaveBeenCalledWith("s1", "a".repeat(64), [
      "candidate-2",
      "candidate-3",
    ]);
  });

  it("keeps confirm disabled while a beat is incomplete", () => {
    const incomplete: VisualSelectionGateStatus = {
      ...gate,
      beats: [
        gate.beats[0],
        {
          ...gate.beats[1],
          candidates: [],
          recommended_candidate_id: "missing",
        },
      ],
    };

    render(
      <VisualSelectionCard
        gate={incomplete}
        assetId="asset-1"
        sessionId="s1"
        client={client()}
        onConfirmed={() => undefined}
      />,
    );

    expect((screen.getByRole("button", { name: "Bildauswahl übernehmen" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("disables inputs and confirm while the request is in flight", () => {
    const pending = new Promise<{ session_id: string; job_id: string }>(() => undefined);
    const c = client({ confirmVisualSelection: vi.fn().mockReturnValue(pending) });
    render(
      <VisualSelectionCard
        gate={gate}
        assetId="asset-1"
        sessionId="s1"
        client={c}
        onConfirmed={() => undefined}
      />,
    );

    const confirm = screen.getByRole("button", { name: "Bildauswahl übernehmen" }) as HTMLButtonElement;
    fireEvent.click(confirm);

    expect(confirm.disabled).toBe(true);
    expect((screen.getByTestId("visual-candidate-candidate-1") as HTMLInputElement).disabled).toBe(true);
  });
});
