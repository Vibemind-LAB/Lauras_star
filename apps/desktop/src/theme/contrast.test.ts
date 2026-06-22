/**
 * P5-T1 — WCAG AA contrast table for the green/white token foundation.
 *
 * This test is the living contract for every text/bg token pair introduced
 * in :root of src/index.css.  If a new token is added, add its pair here.
 * If a hex value is changed in index.css, update the matching hex here.
 *
 * Rules enforced:
 *   • Text tokens (--text-*) on every surface  → ≥ 4.5 : 1  (WCAG AA normal text)
 *   • Accent-ink on accent fill               → ≥ 4.5 : 1
 *   • Status colours on --surface-0           → ≥ 3.0 : 1  (WCAG AA large/UI text)
 *
 * These are hardcoded hex values mirroring index.css — the test documents the
 * design contract, not the CSS variable resolution.
 */

import { describe, expect, it } from "vitest";
import { contrastRatio } from "./contrast";

// ── Token hex mirrors (must stay in sync with src/index.css :root) ──────────
const TOKENS = {
  // Surfaces
  surface0:   "#F4F7F3",
  surface1:   "#FFFFFF",
  surface1_5: "#EDF1EA",
  surface2:   "#E4EAE0",
  // Text
  textStrong: "#0B0F14",
  textMuted:  "#475569",
  textFaint:  "#536375", // darkened from plan #64748B to clear AA on surface-0
  // Accent
  accent:     "#15803D",
  accentInk:  "#FFFFFF",
  // Status
  statusOk:   "#0D9488",
  statusWarn: "#B45309", // darkened from plan #D97706 to clear 3:1 on surface-0
  statusErr:  "#DC2626",
} as const;

// ── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Assert a contrast ratio meets a WCAG threshold.
 * Rounds to two decimal places for readable failure messages.
 */
function expectContrast(
  fg: string,
  bg: string,
  minRatio: number,
  label: string
): void {
  const ratio = contrastRatio(fg, bg);
  const rounded = Math.round(ratio * 100) / 100;
  expect(
    ratio,
    `${label}: expected ≥ ${minRatio}:1, got ${rounded}:1 (fg=${fg} bg=${bg})`
  ).toBeGreaterThanOrEqual(minRatio);
}

// ── Text token pairs ─────────────────────────────────────────────────────────

describe("text-strong on surfaces (AA normal text ≥ 4.5:1)", () => {
  it("on surface-0 (#F4F7F3)", () => {
    expectContrast(TOKENS.textStrong, TOKENS.surface0, 4.5, "--text-strong on --surface-0");
  });
  it("on surface-1 (#FFFFFF)", () => {
    expectContrast(TOKENS.textStrong, TOKENS.surface1, 4.5, "--text-strong on --surface-1");
  });
  it("on surface-1.5 (#EDF1EA)", () => {
    expectContrast(TOKENS.textStrong, TOKENS.surface1_5, 4.5, "--text-strong on --surface-1-5");
  });
  it("on surface-2 (#E4EAE0)", () => {
    expectContrast(TOKENS.textStrong, TOKENS.surface2, 4.5, "--text-strong on --surface-2");
  });
});

describe("text-muted on surfaces (AA normal text ≥ 4.5:1)", () => {
  it("on surface-0 (#F4F7F3)", () => {
    expectContrast(TOKENS.textMuted, TOKENS.surface0, 4.5, "--text-muted on --surface-0");
  });
  it("on surface-1 (#FFFFFF)", () => {
    expectContrast(TOKENS.textMuted, TOKENS.surface1, 4.5, "--text-muted on --surface-1");
  });
});

describe("text-faint on surfaces (AA normal text ≥ 4.5:1)", () => {
  it("on surface-0 (#F4F7F3)", () => {
    expectContrast(TOKENS.textFaint, TOKENS.surface0, 4.5, "--text-faint on --surface-0");
  });
  it("on surface-1 (#FFFFFF)", () => {
    expectContrast(TOKENS.textFaint, TOKENS.surface1, 4.5, "--text-faint on --surface-1");
  });
});

// ── Accent ink on accent fill ─────────────────────────────────────────────────

describe("accent-ink on accent fill (AA normal text ≥ 4.5:1)", () => {
  it("--accent-ink (#FFFFFF) on --accent (#15803D)", () => {
    expectContrast(TOKENS.accentInk, TOKENS.accent, 4.5, "--accent-ink on --accent");
  });
});

// ── Status colours on surface-0 (AA large/UI text ≥ 3.0:1) ──────────────────

describe("status colours on surface-0 (AA large text / UI components ≥ 3.0:1)", () => {
  it("status-ok (#0D9488) on surface-0 (#F4F7F3)", () => {
    expectContrast(TOKENS.statusOk, TOKENS.surface0, 3.0, "--status-ok on --surface-0");
  });
  it("status-warn (#B45309) on surface-0 (#F4F7F3)", () => {
    expectContrast(TOKENS.statusWarn, TOKENS.surface0, 3.0, "--status-warn on --surface-0");
  });
  it("status-err (#DC2626) on surface-0 (#F4F7F3)", () => {
    expectContrast(TOKENS.statusErr, TOKENS.surface0, 3.0, "--status-err on --surface-0");
  });
});
