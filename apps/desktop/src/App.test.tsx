import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EXPECTED_SCHEMA_VERSION, HealthBadge } from "./App";

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
});
