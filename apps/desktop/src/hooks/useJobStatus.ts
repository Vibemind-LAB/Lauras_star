import { useEffect, useRef, useState } from "react";

import type { JobStatus, LauraClient } from "../api";
import { log } from "../shared/log";

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

function parseJobError(job: JobStatus): string | null {
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
 * Cleans up the interval on unmount or when jobId changes.
 */
export function useJobStatus(
  client: LauraClient,
  jobId: string | null,
): JobStatusResult {
  const [jobStatus, setJobStatus] = useState<JobStatus | null>(null);
  const activeRef = useRef(true);

  useEffect(() => {
    if (jobId === null) {
      setJobStatus(null);
      return;
    }

    activeRef.current = true;
    setJobStatus(null);

    let intervalId: ReturnType<typeof setInterval> | null = null;

    const poll = (): void => {
      client
        .getJob(jobId)
        .then((job) => {
          if (!activeRef.current) return;
          setJobStatus(job);
          if (TERMINAL.has(job.status) && intervalId !== null) {
            clearInterval(intervalId);
            intervalId = null;
          }
        })
        .catch((err: unknown) => {
          if (activeRef.current) {
            log.error("useJobStatus poll failed for job", jobId, err instanceof Error ? err.message : String(err));
          }
        });
    };

    // Fire immediately, then repeat.
    poll();
    intervalId = setInterval(poll, 1500);

    return () => {
      activeRef.current = false;
      if (intervalId !== null) {
        clearInterval(intervalId);
      }
    };
  }, [client, jobId]);

  const error = jobStatus !== null && jobStatus.status === "failed"
    ? parseJobError(jobStatus)
    : null;

  const isRunning =
    jobStatus === null
      ? jobId !== null
      : !TERMINAL.has(jobStatus.status);

  return { jobStatus, error, isRunning };
}
