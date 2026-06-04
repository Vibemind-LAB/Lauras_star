import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ImportStatus } from "../api";
import { ImportProgress } from "./ImportProgress";

const st = (over: Partial<ImportStatus>): ImportStatus => ({
  phase: "downloading", downloaded_bytes: null, total_bytes: null,
  speed_bps: null, eta_seconds: null, error: null, ...over,
});

describe("ImportProgress", () => {
  it("shows percent + speed while downloading", () => {
    render(
      <ImportProgress
        status={st({
          downloaded_bytes: 50 * 1024 ** 2,
          total_bytes: 100 * 1024 ** 2,
          speed_bps: 5 * 1024 ** 2,
          eta_seconds: 10,
        })}
        onRetry={vi.fn()}
      />,
    );
    expect(screen.getByText(/50%/)).toBeTruthy();
    expect(screen.getByText(/5\.0 MiB\/s/)).toBeTruthy();
  });

  it("shows error + retry button on error", () => {
    const onRetry = vi.fn();
    render(<ImportProgress status={st({ phase: "error", error: "boom" })} onRetry={onRetry} />);
    expect(screen.getByText(/boom/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /erneut/i }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("renders nothing when ready", () => {
    const { container } = render(<ImportProgress status={st({ phase: "ready" })} onRetry={vi.fn()} />);
    expect(container.firstChild).toBeNull();
  });
});
