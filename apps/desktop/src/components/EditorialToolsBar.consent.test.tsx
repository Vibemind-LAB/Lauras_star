import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EditorialToolsBar } from "./EditorialToolsBar";

// vi.mock is hoisted before imports, so we must not reference variables declared
// after the mock factory. All vi.fn() calls must live inside the factory.
vi.mock("../hooks/useConsent", () => ({
  useConsent: () => ({
    active: [{ id: "c1", subject_label: "Laura", revoked_at: null, project_id: "p1", confirmed_at: null, confirmed_by: null, source_asset_id: null, note: null }],
    create: vi.fn(),
    revoke: vi.fn(),
    records: [],
    loading: false,
    error: null,
    reload: vi.fn(),
  }),
  partitionConsent: () => ({ active: [], revoked: [] }),
}));

const voices = [{ name: "Hedda", culture: "de-DE", gender: "Female" as const }];

function makeToolsBarProps(overrides: { syntheticEffects: string[] }) {
  return {
    voices,
    voiceId: null as string | null,
    onVoiceChange: () => {},
    pendingEdge: null as null,
    onSmooth: () => {},
    onReenact: () => {},
    projectId: "p1",
    ...overrides,
  };
}

describe("EditorialToolsBar compliance", () => {
  it("always shows the synthetic-content disclosure with effects + subjects", () => {
    render(<EditorialToolsBar {...makeToolsBarProps({ syntheticEffects: ["VO", "Lippensync"] })} />);
    const line = screen.getByText(/Enthält synthetische Inhalte/i);
    expect(line.textContent).toMatch(/VO/);
    expect(line.textContent).toMatch(/Lippensync/);
    expect(line.textContent).toMatch(/Laura/);
  });

  it("renders no control to hide the disclosure", () => {
    render(<EditorialToolsBar {...makeToolsBarProps({ syntheticEffects: [] })} />);
    expect(screen.queryByLabelText(/Kennzeichnung.*ausblenden/i)).toBeNull();
  });

  it("opens the consent inspector and lists active consents", () => {
    render(<EditorialToolsBar {...makeToolsBarProps({ syntheticEffects: ["VO"] })} />);
    const summary = screen.getByText(/Das bin ich/i);
    fireEvent.click(summary);
    expect(screen.getByText("Laura")).toBeDefined();
    expect(screen.getByRole("button", { name: /widerrufen/i })).toBeDefined();
  });

  it("revoke button is present and clickable without throwing", () => {
    render(<EditorialToolsBar {...makeToolsBarProps({ syntheticEffects: [] })} />);
    const summary = screen.getByText(/Das bin ich/i);
    fireEvent.click(summary);
    const revokeBtn = screen.getByRole("button", { name: /widerrufen/i });
    // Should not throw — useConsent.revoke is a vi.fn()
    fireEvent.click(revokeBtn);
    expect(revokeBtn).toBeDefined();
  });
});
