import { useQuery } from "@tanstack/react-query";

import type { JobStatus, LauraClient } from "../api";
import { qk } from "../cache/queryKeys";

/** Status values that indicate a job has reached a terminal state. */
const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);

export interface JobStatusResult {
  /** The latest known status string from the backend, or null while loading. */
  jobStatus: JobStatus | null;
  /** Parsed error string if status is "failed", otherwise null. */
  error: string | null;
  /** True while the job is non-terminal (queued/leased/running). */
  isRunning: boolean;
}

/** A job's `error_json` is `{"error": "..."}` from the runner's own JSON dump when available,
 * else the raw string, else `null` (no error recorded). Exported so other job-status readers
 * (e.g. `ActionCard.tsx`'s production-job backstop) render the same failure text this hook's
 * own `error` field does, instead of re-deriving it. */
export function parseJobError(job: JobStatus): string | null {
  if (!job.error_json) return null;
  try {
    const parsed = JSON.parse(job.error_json) as unknown;
    if (typeof parsed === "object" && parsed !== null) {
      const errorField = (parsed as { error?: unknown }).error;
      if (typeof errorField === "string") return errorField;
    }
  } catch {
    return job.error_json;
  }
  return job.error_json;
}

/**
 * Polls `client.getJob(jobId)` every 1500 ms while the job is non-terminal.
 * Uses useQuery with a function-form refetchInterval that returns false once
 * the job reaches a terminal state (succeeded/failed/cancelled), stopping the poll.
 * Cleans up automatically on unmount or when jobId changes.
 */
export function useJobStatus(
  client: LauraClient,
  jobId: string | null,
): JobStatusResult {
  const query = useQuery({
    queryKey: qk.job(jobId ?? ""),
    queryFn: () => client.getJob(jobId!),
    enabled: jobId !== null,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (data !== undefined && TERMINAL.has(data.status)) return false;
      return 1500;
    },
    // Keep polling while the window is unfocused: react-query pauses interval
    // refetches for backgrounded windows by default, which froze the chat
    // card's job backstop mid-production (seen live 2026-08-04 — the job
    // failed but the card kept showing "running" until refocus).
    refetchIntervalInBackground: true,
    // Do not use stale cached data from a previous job — always start fresh
    // when a new jobId is provided. gcTime 0 would remove it too eagerly;
    // staleTime 0 ensures the first fetch fires immediately.
    staleTime: 0,
  });

  const jobStatus: JobStatus | null = jobId !== null ? (query.data ?? null) : null;

  const error = jobStatus !== null && jobStatus.status === "failed"
    ? parseJobError(jobStatus)
    : null;

  const isRunning =
    jobStatus === null
      ? jobId !== null
      : !TERMINAL.has(jobStatus.status);

  return { jobStatus, error, isRunning };
}
