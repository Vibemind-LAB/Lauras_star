import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App, EXPECTED_SCHEMA_VERSION, HealthBadge } from "./App";

describe("HealthBadge", () => {
  it("shows a backend schema mismatch clearly", () => {
    render(
      <HealthBadge
        offline={false}
        health={{ status: "ok", version: "0.1.0", pipeline_version: "p", schema_version: 1 }}
      />,
    );

    expect(screen.getByText(`Backend veraltet · schema 1/${EXPECTED_SCHEMA_VERSION}`)).toBeTruthy();
  });

  it("a fetched health beats a stale offline flag", () => {
    // Live 2026-08-03 (Drive-Test renderer): the badge read "Service offline" while the same
    // panel listed projects and assets from the very service it declared dead. `offline` is
    // set once when the preload bridge is missing at mount and nothing ever clears it —
    // under HMR the stale flag survives into a session that has long since connected. A
    // present `health` IS the proof of a connection, so it must win over the flag.
    render(
      <HealthBadge
        offline={true}
        health={{
          status: "ok",
          version: "0.1.0",
          pipeline_version: "p",
          schema_version: EXPECTED_SCHEMA_VERSION,
        }}
      />,
    );

    expect(screen.queryByText("Service offline")).toBeNull();
    expect(screen.getByText(/API v0\.1\.0/)).toBeTruthy();
  });

  it("offline with no health still reads offline", () => {
    render(<HealthBadge offline={true} health={null} />);
    expect(screen.getByText("Service offline")).toBeTruthy();
  });
});

describe("App default stage", () => {
  it("renders the chat nav item as active by default (chat-first, spec 2026-08-03)", () => {
    // No `window.laura` bridge in jsdom — App falls back to its offline state, which is fine
    // here: the assertion is purely about which NavRail item starts active, not about the
    // (client-gated) ChatStage content itself.
    render(<App />);
    expect(screen.getByRole("button", { name: "💬 Chat" }).getAttribute("aria-current")).toBe(
      "page",
    );
  });
});
