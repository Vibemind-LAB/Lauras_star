import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { LauraClient } from "../api";
import { renderWithQuery } from "../test-utils";
import { GenerateVideoPanel } from "./GenerateVideoPanel";

function mockClient(overrides: Partial<LauraClient> = {}): LauraClient {
  return {
    generateVideo: vi.fn().mockResolvedValue({ job_id: "job-1" }),
    getJob: vi.fn().mockResolvedValue({ status: "running" }),
    ...overrides,
  } as unknown as LauraClient;
}

describe("GenerateVideoPanel", () => {
  it("enqueues a generate job with the prompt and frame count on submit", () => {
    const client = mockClient();
    renderWithQuery(<GenerateVideoPanel client={client} projectId="proj-1" />);

    fireEvent.change(screen.getByLabelText("Prompt"), { target: { value: "calm ocean" } });
    fireEvent.click(screen.getByRole("button", { name: "Generieren" }));

    // default length 3 s → 90 frames at 30 fps
    expect(client.generateVideo).toHaveBeenCalledWith("proj-1", "calm ocean", 90);
  });

  it("disables the button until a prompt is entered", () => {
    renderWithQuery(<GenerateVideoPanel client={mockClient()} projectId="proj-1" />);
    const btn = () => screen.getByRole("button", { name: "Generieren" }) as HTMLButtonElement;

    expect(btn().disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Prompt"), { target: { value: "x" } });
    expect(btn().disabled).toBe(false);
  });
});
