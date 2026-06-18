import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { type AiPersona, type AiRuntime, type LauraClient } from "../api";
import { PersonaKitPanel } from "./PersonaKitPanel";

function runtime(overrides: Partial<AiRuntime>): AiRuntime {
  return {
    id: "rt-voice",
    effect: "voice",
    kind: "stub",
    display_name: "Stub Voice",
    status: {},
    capabilities: {},
    base_url: null,
    container_image: null,
    container_name: null,
    port: null,
    workspace_mount: null,
    model_mount: null,
    requires_gpu: false,
    enabled: true,
    license_status: "not_required",
    last_health_at: null,
    created_at: "",
    updated_at: "",
    ...overrides,
  };
}

function persona(overrides: Partial<AiPersona>): AiPersona {
  return {
    id: "persona-1",
    project_id: "project-1",
    name: "Existing Persona",
    consent_id: "consent-existing",
    face_reference_asset_id: null,
    voice_reference_asset_id: null,
    style: {},
    allowed_effects: ["voice"],
    preferred_runtimes: { voice: "rt-voice" },
    created_at: "",
    updated_at: "",
    ...overrides,
  };
}

function client(overrides: Partial<LauraClient> = {}): LauraClient {
  return {
    listAiPersonas: vi.fn().mockResolvedValue([]),
    listAiRuntimes: vi.fn().mockResolvedValue([
      runtime({ id: "rt-voice", effect: "voice", display_name: "Stub Voice" }),
      runtime({ id: "rt-face", effect: "faceswap", display_name: "Stub Face" }),
    ]),
    createConsent: vi.fn().mockResolvedValue({
      id: "consent-1",
      project_id: "project-1",
      subject_label: "Persona",
      confirmed_at: "",
      confirmed_by: null,
      source_asset_id: null,
      note: null,
      revoked_at: null,
    }),
    createAiPersona: vi.fn().mockResolvedValue(persona({ id: "persona-1", name: "Persona" })),
    ...overrides,
  } as unknown as LauraClient;
}

describe("PersonaKitPanel", () => {
  it("creates consent and persona", async () => {
    const c = client();

    render(<PersonaKitPanel client={c} projectId="project-1" />);

    fireEvent.change(await screen.findByLabelText("Persona-Name"), {
      target: { value: "Persona" },
    });
    fireEvent.click(screen.getByRole("checkbox", { name: /voice/i }));
    fireEvent.click(screen.getByRole("button", { name: /persona erstellen/i }));

    await waitFor(() =>
      expect(c.createConsent).toHaveBeenCalledWith("project-1", {
        subjectLabel: "Persona",
      }),
    );
    expect(c.createAiPersona).toHaveBeenCalledWith(
      expect.objectContaining({
        projectId: "project-1",
        name: "Persona",
        consentId: "consent-1",
        allowedEffects: ["voice"],
      }),
    );
  });

  it("passes optional reference assets and preferred runtimes into persona creation", async () => {
    const createAiPersona = vi.fn().mockResolvedValue(persona({ id: "persona-2", name: "Lead" }));
    const c = client({
      createAiPersona,
      listAiRuntimes: vi.fn().mockResolvedValue([
        runtime({ id: "rt-voice", effect: "voice", display_name: "Stub Voice" }),
        runtime({ id: "rt-lipsync", effect: "lipsync", display_name: "Stub Lipsync" }),
      ]),
    });

    render(<PersonaKitPanel client={c} projectId="project-1" />);

    fireEvent.change(await screen.findByLabelText("Persona-Name"), {
      target: { value: "Lead" },
    });
    fireEvent.change(screen.getByLabelText("Face-Reference-Asset"), {
      target: { value: "face-asset-1" },
    });
    fireEvent.change(screen.getByLabelText("Voice-Reference-Asset"), {
      target: { value: "voice-asset-1" },
    });
    fireEvent.click(screen.getByRole("checkbox", { name: /voice/i }));
    fireEvent.click(screen.getByRole("checkbox", { name: /lipsync/i }));
    fireEvent.change(screen.getByLabelText("Bevorzugte Runtime für voice"), {
      target: { value: "rt-voice" },
    });
    fireEvent.change(screen.getByLabelText("Bevorzugte Runtime für lipsync"), {
      target: { value: "rt-lipsync" },
    });
    fireEvent.click(screen.getByRole("button", { name: /persona erstellen/i }));

    await waitFor(() =>
      expect(createAiPersona).toHaveBeenCalledWith({
        projectId: "project-1",
        name: "Lead",
        consentId: "consent-1",
        faceReferenceAssetId: "face-asset-1",
        voiceReferenceAssetId: "voice-asset-1",
        allowedEffects: ["voice", "lipsync"],
        preferredRuntimes: {
          voice: "rt-voice",
          lipsync: "rt-lipsync",
        },
      }),
    );
  });

  it("lists existing personas with consent, references, effects, and preferred runtimes", async () => {
    const c = client({
      listAiPersonas: vi.fn().mockResolvedValue([
        persona({
          name: "Presenter",
          consent_id: "consent-42",
          face_reference_asset_id: "face-1",
          voice_reference_asset_id: "voice-2",
          allowed_effects: ["voice", "faceswap"],
          preferred_runtimes: { voice: "rt-voice", faceswap: "rt-face" },
        }),
      ]),
    });

    render(<PersonaKitPanel client={c} projectId="project-1" />);

    expect(await screen.findByText("Presenter")).toBeTruthy();
    expect(screen.getByText("consent-42")).toBeTruthy();
    expect(screen.getByText("face-1")).toBeTruthy();
    expect(screen.getByText("voice-2")).toBeTruthy();
    expect(screen.getByText("voice, faceswap")).toBeTruthy();
    expect(screen.getByText("voice -> rt-voice, faceswap -> rt-face")).toBeTruthy();
  });
});
