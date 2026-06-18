import { fireEvent, render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { type JobStatus, type LauraClient } from "../api";
import { JobCenter } from "./JobCenter";

function job(overrides: Partial<JobStatus>): JobStatus {
  return {
    id: "job-1",
    queue: "export",
    kind: "export.render",
    status: "running",
    attempt: 1,
    max_attempts: 3,
    result_json: null,
    error_json: null,
    created_at: "",
    updated_at: "",
    finished_at: null,
    ...overrides,
  };
}

function client(overrides: Partial<LauraClient> = {}): LauraClient {
  return {
    listJobs: vi.fn().mockResolvedValue([]),
    cancelJob: vi.fn().mockResolvedValue(job({ status: "cancelled" })),
    retryJob: vi.fn().mockResolvedValue({ job_id: "job-2" }),
    ...overrides,
  } as unknown as LauraClient;
}

describe("JobCenter", () => {
  it("opens the global job list and shows errors", async () => {
    const c = client({
      listJobs: vi.fn().mockResolvedValue([
        job({
          id: "job-failed",
          status: "failed",
          error_json: JSON.stringify({ error: "sidecar missing" }),
        }),
      ]),
    });
    const { findByText, getByRole } = render(<JobCenter client={c} />);

    fireEvent.click(getByRole("button", { name: "Jobs" }));

    expect(await findByText("Job-Zentrale")).toBeTruthy();
    expect(await findByText("sidecar missing")).toBeTruthy();
  });

  it("can cancel running jobs and retry failed jobs", async () => {
    const listJobs = vi.fn().mockResolvedValue([
      job({ id: "job-running", status: "running" }),
      job({ id: "job-failed", status: "failed" }),
    ]);
    const cancelJob = vi.fn().mockResolvedValue(job({ id: "job-running", status: "cancelled" }));
    const retryJob = vi.fn().mockResolvedValue({ job_id: "job-retry" });
    const c = client({ listJobs, cancelJob, retryJob });
    const { findAllByText, getByRole } = render(<JobCenter client={c} />);

    fireEvent.click(getByRole("button", { name: "Jobs" }));
    await findAllByText("export.render");
    fireEvent.click(getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(cancelJob).toHaveBeenCalledWith("job-running"));

    fireEvent.click(getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(retryJob).toHaveBeenCalledWith("job-failed"));
  });
});
