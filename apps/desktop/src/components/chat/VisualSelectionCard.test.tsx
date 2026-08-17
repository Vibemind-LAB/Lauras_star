import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  LauraClient,
  VisualSceneChoice,
  VisualSelectionGateStatus,
} from "../../api";
import { VisualSelectionCard } from "./VisualSelectionCard";

type VisualSelectionClient = Pick<
  LauraClient,
  "assetFrameUrl" | "confirmVisualSelection" | "saveVisualSelectionDraft"
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

function sceneChoice(
  roughCutOrder: number,
  options: {
    included?: boolean;
    duration?: number;
    maxDuration?: number;
    candidateCount?: number;
    startFrame?: number;
  } = {},
): VisualSceneChoice {
  const sceneNumber = roughCutOrder + 1;
  const startFrame = options.startFrame ?? roughCutOrder * 300;
  const candidateCount = options.candidateCount ?? 1;
  return {
    rough_cut_order: roughCutOrder,
    scene_number: sceneNumber,
    description:
      roughCutOrder === 0 ? "Rowboat dashboard and file organizer" : `Rough-Cut scene ${sceneNumber}`,
    transcript: roughCutOrder === 0 ? "recognized UI: Draft an email" : `Transcript ${sceneNumber}`,
    rationale: `Relevant für Rough Cut ${roughCutOrder + 1}`,
    candidates: Array.from({ length: candidateCount }, (_, windowIndex) => ({
      candidate_id: `scene-${roughCutOrder}-candidate-${windowIndex}`,
      rough_cut_order: roughCutOrder,
      scene_number: sceneNumber,
      window_index: windowIndex,
      src_start_frame: startFrame + windowIndex * 300,
      src_end_frame_exclusive: startFrame + windowIndex * 300 + 300,
      thumb_frame: startFrame + windowIndex * 300 + 150,
      max_duration_s: options.maxDuration ?? 10,
      description: `Fenster ${windowIndex + 1}`,
      transcript_snippet:
        roughCutOrder === 0 ? "recognized UI: Draft an email" : `Transcript ${sceneNumber}`,
      rationale: `Kandidat ${windowIndex + 1}`,
      score: 1 - windowIndex / 10,
    })),
    recommended_candidate_id: `scene-${roughCutOrder}-candidate-0`,
    recommended_included: options.included ?? true,
    recommended_duration_s: options.duration ?? 10,
    selected_candidate_id: null,
    included: null,
    requested_duration_s: null,
  };
}

function sceneGate(
  overrides: Partial<VisualSelectionGateStatus> = {},
): VisualSelectionGateStatus {
  return {
    enabled: true,
    approved: false,
    pending: true,
    proposal_id: "a".repeat(64),
    beats: [],
    scene_choices: Array.from({ length: 5 }, (_, index) => sceneChoice(index)),
    voice_total_frames: 1350,
    fps: 30,
    ...overrides,
  };
}

function client(
  overrides: Partial<VisualSelectionClient> = {},
): VisualSelectionClient {
  return {
    assetFrameUrl: vi.fn().mockReturnValue(new Promise<string>(() => undefined)),
    confirmVisualSelection: vi.fn().mockResolvedValue({ session_id: "s1", job_id: "j2" }),
    saveVisualSelectionDraft: vi.fn().mockImplementation((sessionId, request) =>
      Promise.resolve({
        session_id: sessionId,
        proposal_hash: request.proposal_hash,
        selections: request.selections,
        revision: (request.expected_revision ?? 0) + 1,
        updated_utc: "2026-08-17T10:00:00+00:00",
        stale: false,
        stale_reason: null,
      }),
    ),
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
    expect(c.saveVisualSelectionDraft).not.toHaveBeenCalled();
  });

  it("restores the persisted rough-cut draft and autosaves every complete decision change", async () => {
    const choices = [
      sceneChoice(0, { candidateCount: 2, duration: 5 }),
      sceneChoice(1, { duration: 5 }),
      sceneChoice(2, { duration: 5 }),
      sceneChoice(3, { duration: 5 }),
    ];
    const serverSelections = choices.map((choice) => ({
      rough_cut_order: choice.rough_cut_order,
      candidate_id:
        choice.rough_cut_order === 0
          ? "scene-0-candidate-1"
          : choice.recommended_candidate_id,
      included: choice.rough_cut_order !== 3,
      requested_duration_s: choice.rough_cut_order === 0 ? 6 : 5,
    }));
    const saveVisualSelectionDraft = vi.fn().mockImplementation((sessionId, request) =>
      Promise.resolve({
        session_id: sessionId,
        proposal_hash: request.proposal_hash,
        selections: request.selections,
        revision: (request.expected_revision ?? 7) + 1,
        updated_utc: "2026-08-17T10:05:00+00:00",
        stale: false,
        stale_reason: null,
      }),
    );
    const withDraft = sceneGate({
      scene_choices: choices,
      voice_total_frames: 600,
      draft: {
        session_id: "s1",
        proposal_hash: "a".repeat(64),
        selections: serverSelections,
        revision: 7,
        updated_utc: "2026-08-17T09:00:00+00:00",
        stale: false,
        stale_reason: null,
      },
    });

    render(
      <VisualSelectionCard
        gate={withDraft}
        assetId="asset-1"
        sessionId="s1"
        client={client({ saveVisualSelectionDraft })}
        onConfirmed={() => undefined}
      />,
    );

    expect(
      (screen.getByTestId("visual-scene-candidate-scene-0-candidate-1") as HTMLInputElement)
        .checked,
    ).toBe(true);
    expect((screen.getByTestId("visual-scene-use-3") as HTMLInputElement).checked).toBe(false);
    expect(screen.getByText("Gespeichert")).toBeTruthy();

    fireEvent.click(screen.getByTestId("visual-scene-candidate-scene-0-candidate-0"));
    fireEvent.click(screen.getByTestId("visual-scene-use-3"));
    fireEvent.click(screen.getByRole("button", { name: "Szene 1: 7 Sekunden" }));

    await waitFor(() => expect(saveVisualSelectionDraft).toHaveBeenCalledTimes(3));
    expect(saveVisualSelectionDraft.mock.calls[2]?.[1].selections).toEqual([
      { rough_cut_order: 0, candidate_id: "scene-0-candidate-0", included: true, requested_duration_s: 7 },
      { rough_cut_order: 1, candidate_id: "scene-1-candidate-0", included: true, requested_duration_s: 5 },
      { rough_cut_order: 2, candidate_id: "scene-2-candidate-0", included: true, requested_duration_s: 5 },
      { rough_cut_order: 3, candidate_id: "scene-3-candidate-0", included: true, requested_duration_s: 5 },
    ]);
    await waitFor(() => expect(screen.getByText("Gespeichert")).toBeTruthy());
  });

  it("blocks confirmation after a draft save error and offers an explicit retry", async () => {
    const saveVisualSelectionDraft = vi.fn().mockRejectedValue(new Error("offline"));
    render(
      <VisualSelectionCard
        gate={sceneGate()}
        assetId="asset-1"
        sessionId="s1"
        client={client({ saveVisualSelectionDraft })}
        onConfirmed={() => undefined}
      />,
    );

    fireEvent.click(screen.getByTestId("visual-scene-use-4"));
    expect(await screen.findByRole("button", { name: "Erneut speichern" })).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Bildauswahl übernehmen" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
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

  it("shows every rough-cut scene once in received order with decision metadata", () => {
    const choices = [
      sceneChoice(2),
      sceneChoice(0, { candidateCount: 4, startFrame: 1766 }),
      sceneChoice(1),
      sceneChoice(3),
      sceneChoice(4),
      sceneChoice(5),
      sceneChoice(6),
      sceneChoice(7),
    ];
    const assetFrameUrl = vi.fn().mockReturnValue(new Promise<string>(() => undefined));

    render(
      <VisualSelectionCard
        gate={sceneGate({ scene_choices: choices, voice_total_frames: 2400 })}
        assetId="asset-1"
        sessionId="s1"
        client={client({ assetFrameUrl })}
        onConfirmed={() => undefined}
      />,
    );

    const rows = screen.getAllByRole("group", { name: /Szene/ });
    expect(rows).toHaveLength(8);
    expect(rows.map((row) => row.getAttribute("aria-label"))).toEqual([
      "Szene 3 · Rough Cut 3",
      "Szene 1 · Rough Cut 1",
      "Szene 2 · Rough Cut 2",
      "Szene 4 · Rough Cut 4",
      "Szene 5 · Rough Cut 5",
      "Szene 6 · Rough Cut 6",
      "Szene 7 · Rough Cut 7",
      "Szene 8 · Rough Cut 8",
    ]);
    expect(screen.getByText("Rowboat dashboard and file organizer")).toBeTruthy();
    expect(screen.getByText("recognized UI: Draft an email")).toBeTruthy();
    expect(screen.getByText("Frames 1766–2066")).toBeTruthy();
    expect(assetFrameUrl).toHaveBeenCalledTimes(11);
    expect(screen.getAllByRole("radio")).toHaveLength(11);
  });

  it("uses recommendations, supports skip and duration presets, and submits every ordered decision", async () => {
    const confirmVisualSelection = vi.fn().mockResolvedValue({ session_id: "s1", job_id: "j2" });
    const choices = [
      sceneChoice(0, { duration: 5, candidateCount: 2 }),
      sceneChoice(1, { duration: 5 }),
      sceneChoice(2, { duration: 5 }),
      sceneChoice(3, { included: false, duration: 5 }),
    ];
    render(
      <VisualSelectionCard
        gate={sceneGate({ scene_choices: choices, voice_total_frames: 600 })}
        assetId="asset-1"
        sessionId="s1"
        client={client({ confirmVisualSelection })}
        onConfirmed={() => undefined}
      />,
    );

    expect((screen.getByTestId("visual-scene-use-0") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByTestId("visual-scene-use-3") as HTMLInputElement).checked).toBe(false);
    fireEvent.click(screen.getByTestId("visual-scene-candidate-scene-0-candidate-1"));
    fireEvent.click(screen.getByRole("button", { name: "Szene 1: 6 Sekunden" }));
    fireEvent.click(screen.getByTestId("visual-scene-use-3"));
    await screen.findByText("Gespeichert");
    fireEvent.click(screen.getByRole("button", { name: "Bildauswahl übernehmen" }));

    await waitFor(() => expect(confirmVisualSelection).toHaveBeenCalledTimes(1));
    expect(confirmVisualSelection).toHaveBeenCalledWith("s1", "a".repeat(64), [
      { rough_cut_order: 0, candidate_id: "scene-0-candidate-1", included: true, requested_duration_s: 6 },
      { rough_cut_order: 1, candidate_id: "scene-1-candidate-0", included: true, requested_duration_s: 5 },
      { rough_cut_order: 2, candidate_id: "scene-2-candidate-0", included: true, requested_duration_s: 5 },
      { rough_cut_order: 3, candidate_id: "scene-3-candidate-0", included: true, requested_duration_s: 5 },
    ]);
  });

  it("blocks undercoverage and previews the frame-exact final trim", async () => {
    const choices = Array.from({ length: 5 }, (_, index) =>
      sceneChoice(index, { included: index < 4, duration: 10 }),
    );
    render(
      <VisualSelectionCard
        gate={sceneGate({ scene_choices: choices })}
        assetId="asset-1"
        sessionId="s1"
        client={client()}
        onConfirmed={() => undefined}
      />,
    );

    const confirm = screen.getByRole("button", { name: "Bildauswahl übernehmen" }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    expect(screen.getByText("Gewählt: 40,0 s · Voice: 45,0 s")).toBeTruthy();
    fireEvent.click(screen.getByTestId("visual-scene-use-4"));
    await screen.findByText("Gespeichert");
    expect(confirm.disabled).toBe(false);
    expect(screen.getByText("Letzte Szene final: 5,0 s")).toBeTruthy();
  });

  it("blocks a final trim below one second with a concrete conflict", () => {
    render(
      <VisualSelectionCard
        gate={sceneGate({
          scene_choices: [sceneChoice(0), sceneChoice(1), sceneChoice(2)],
          voice_total_frames: 450,
        })}
        assetId="asset-1"
        sessionId="s1"
        client={client()}
        onConfirmed={() => undefined}
      />,
    );

    expect(
      (screen.getByRole("button", { name: "Bildauswahl übernehmen" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(screen.getByText("Die früheren Szenen lassen weniger als 1,0 s für die letzte Szene.")).toBeTruthy();
  });

  it("bounds duration buttons by the selected candidate capacity", async () => {
    const choice = sceneChoice(0, { maxDuration: 4, candidateCount: 2 });
    choice.candidates[1] = { ...choice.candidates[1], max_duration_s: 7 };
    render(
      <VisualSelectionCard
        gate={sceneGate({ scene_choices: [choice, sceneChoice(1), sceneChoice(2)] })}
        assetId="asset-1"
        sessionId="s1"
        client={client()}
        onConfirmed={() => undefined}
      />,
    );

    expect(screen.getByRole("button", { name: "Szene 1: 4 Sekunden" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Szene 1: 5 Sekunden" })).toBeNull();
    fireEvent.click(screen.getByTestId("visual-scene-candidate-scene-0-candidate-1"));
    await screen.findByText("Gespeichert");
    expect(screen.getByRole("button", { name: "Szene 1: 7 Sekunden" })).toBeTruthy();
  });

  it("keeps stale confirmation errors visible and does not refresh", async () => {
    const onConfirmed = vi.fn();
    const confirmVisualSelection = vi.fn().mockRejectedValue(new Error("409: proposal stale"));
    render(
      <VisualSelectionCard
        gate={sceneGate()}
        assetId="asset-1"
        sessionId="s1"
        client={client({ confirmVisualSelection })}
        onConfirmed={onConfirmed}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Bildauswahl übernehmen" }));
    expect((await screen.findByRole("alert")).textContent).toContain("409: proposal stale");
    expect(onConfirmed).not.toHaveBeenCalled();
  });
});
