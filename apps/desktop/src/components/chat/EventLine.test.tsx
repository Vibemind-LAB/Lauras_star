import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { AgentEvent } from "../../api";
import { EventLine, pickHighlights } from "./EventLine";

describe("EventLine", () => {
  it("renders a resume-path done event that carries no team/summary", () => {
    // Real run logs written by the restore path omit team and summary entirely
    // (seen live 2026-08-04: the DoneCard crashed on summary.trim and white-screened
    // the whole app — no error boundary above the thread).
    const resumeDone: AgentEvent = {
      type: "done",
      ok: true,
      stage: "A",
      weak: true,
      escalated: false,
    };
    render(<EventLine event={resumeDone} />);
    expect(screen.getByText(/Fertig — QA meldet Schwächen/)).not.toBeNull();
    expect(screen.getByText(/Stufe A/)).not.toBeNull();
    expect(screen.queryByText("Verlauf")).toBeNull();
  });
});

describe("pickHighlights", () => {
  it("extracts known keys from a python-dict summary", () => {
    const facts = pickHighlights(
      "{'ok': True, 'export_id': 'e9', 'count': 575, 'reason': 'no candidates'}",
    );
    expect(facts).toContain("export_id=e9");
    expect(facts).toContain("count=575");
    expect(facts).toContain("reason=no candidates");
  });

  it("returns an empty string for unknown shapes and skips None values", () => {
    expect(pickHighlights("plain text")).toBe("");
    expect(pickHighlights("{'error': None}")).toBe("");
  });
});
