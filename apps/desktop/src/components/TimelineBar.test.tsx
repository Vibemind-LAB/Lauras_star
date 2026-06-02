import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { type LauraClient } from "../api";
import { TimelineBar } from "./TimelineBar";

describe("TimelineBar", () => {
  it("prompts to pick a project when there is no timeline", () => {
    const client = {} as unknown as LauraClient;
    const { container } = render(
      <TimelineBar client={client} timeline={null} onChange={() => undefined} />,
    );
    expect(container.textContent ?? "").toContain("wähle ein Projekt");
  });
});
