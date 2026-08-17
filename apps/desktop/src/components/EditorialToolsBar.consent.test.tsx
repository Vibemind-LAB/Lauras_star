import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EditorialToolsBar } from "./EditorialToolsBar";

// vi.mock is hoisted before imports, so we must not reference variables declared
// after the mock factory. All vi.fn() calls must live inside the factory.
// Default mock — can be overridden per test with vi.mocked / vi.doMock.
const mockUseConsent = vi.fn(() => ({
  active: [{ id: "c1", subject_label: "Laura", revoked_at: null, project_id: "p1", confirmed_at: null, confirmed_by: null, source_asset_id: null, note: null }],
  create: vi.fn(),
  revoke: vi.fn(),
  records: [],
  loading: false,
  error: null as string | null,
  reload: vi.fn(),
}));

vi.mock("../hooks/useConsent", () => ({
  useConsent: () => mockUseConsent(),
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

afterEach(() => {
  mockUseConsent.mockImplementation(() => ({
    active: [{ id: "c1", subject_label: "Laura", revoked_at: null, project_id: "p1", confirmed_at: null, confirmed_by: null, source_asset_id: null, note: null }],
    create: vi.fn(),
    revoke: vi.fn(),
    records: [],
    loading: false,
    error: null,
    reload: vi.fn(),
  }));
});

describe("EditorialToolsBar compliance", () => {
  it("always shows the synthetic-content disclosure with effects + subjects", () => {
    render(<EditorialToolsBar {...makeToolsBarProps({ syntheticEffects: ["VO", "Lippensync"] })} />);
    const line = screen.getByText(/Contains synthetic content/i);
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
    const summary = screen.getByText(/That's me/i);
    fireEvent.click(summary);
    expect(screen.getByText("Laura")).toBeDefined();
    expect(screen.getByRole("button", { name: /widerrufen/i })).toBeDefined();
  });

  it("revoke button is present and clickable without throwing", () => {
    render(<EditorialToolsBar {...makeToolsBarProps({ syntheticEffects: [] })} />);
    const summary = screen.getByText(/That's me/i);
    fireEvent.click(summary);
    const revokeBtn = screen.getByRole("button", { name: /widerrufen/i });
    // Should not throw — useConsent.revoke is a vi.fn()
    fireEvent.click(revokeBtn);
    expect(revokeBtn).toBeDefined();
  });

  it("Fix 4 — renders inline error when useConsent reports an error", () => {
    // Simulate a backend failure on consent create/revoke.
    mockUseConsent.mockImplementation(() => ({
      active: [],
      create: vi.fn(),
      revoke: vi.fn(),
      records: [],
      loading: false,
      error: "Consent service unavailable",
      reload: vi.fn(),
    }));

    render(<EditorialToolsBar {...makeToolsBarProps({ syntheticEffects: [] })} />);
    // Open the details to make the error visible.
    fireEvent.click(screen.getByText(/That's me/i));
    const errorEl = screen.getByRole("alert");
    expect(errorEl.textContent).toContain("Consent service unavailable");
  });

  it("Fix 4 — no error element rendered when consent has no error", () => {
    render(<EditorialToolsBar {...makeToolsBarProps({ syntheticEffects: [] })} />);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
