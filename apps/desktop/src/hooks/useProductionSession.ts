import { useCallback, useEffect, useRef, useState } from "react";

import type { JobStatus, LauraClient, ProductionCreated, ProductionStatus } from "../api";
import { log } from "../shared/log";

/** Lifecycle of a v2 production session, driven by the underlying job's terminal status. */
export type ProductionPhase = "idle" | "running" | "done" | "error";

export interface ProductionSessionState {
  phase: ProductionPhase;
  sessionId: string | null;
  jobId: string | null;
  /** Last known board snapshot (GET /production/{sessionId}). */
  status: ProductionStatus | null;
  /** Parsed `result_json` of the terminal job (ok/weak/export_id …) — narrow at the call site. */
  jobResult: unknown | null;
  error: string | null;
}

export interface ProductionSessionController {
  state: ProductionSessionState;
  start: (task: string, targetSeconds?: number) => Promise<void>;
  sendMessage: (text: string) => Promise<void>;
  /** Forget the session: clears its localStorage entry and resets to idle. */
  reset: () => void;
}

/** How often the job + board are polled while a production run is in flight. */
const POLL_INTERVAL_MS = 2500;

/** Job-status values that stop the poll loop (mirrors useJobStatus/useTransitionReview). */
const TERMINAL_JOB_STATUSES = new Set(["succeeded", "failed", "cancelled"]);

const IDLE_STATE: ProductionSessionState = {
  phase: "idle",
  sessionId: null,
  jobId: null,
  status: null,
  jobResult: null,
  error: null,
};

interface StoredProductionSession {
  sessionId: string;
  jobId: string;
}

function storageKey(assetId: string): string {
  return `laura.production.${assetId}`;
}

function readStoredSession(assetId: string): StoredProductionSession | null {
  try {
    const raw = window.localStorage.getItem(storageKey(assetId));
    if (raw === null) return null;
    const parsed = JSON.parse(raw) as unknown;
    if (typeof parsed !== "object" || parsed === null) return null;
    const { sessionId, jobId } = parsed as Record<string, unknown>;
    if (typeof sessionId === "string" && typeof jobId === "string") {
      return { sessionId, jobId };
    }
    return null;
  } catch (e) {
    log.warn("useProductionSession: failed to read stored session", e);
    return null;
  }
}

function writeStoredSession(assetId: string, value: StoredProductionSession): void {
  try {
    window.localStorage.setItem(storageKey(assetId), JSON.stringify(value));
  } catch (e) {
    log.warn("useProductionSession: failed to persist session", e);
  }
}

function clearStoredSession(assetId: string): void {
  try {
    window.localStorage.removeItem(storageKey(assetId));
  } catch (e) {
    log.warn("useProductionSession: failed to clear stored session", e);
  }
}

/**
 * Parsed `result_json` of a terminal job — an opaque payload (ok/weak/export_id …) the
 * caller narrows defensively. Falls back to the raw string if it isn't valid JSON.
 */
function parseJobResult(job: JobStatus): unknown {
  if (!job.result_json) return null;
  try {
    return JSON.parse(job.result_json) as unknown;
  } catch {
    return job.result_json;
  }
}

/**
 * Human-readable error text for a failed/cancelled job. Mirrors useJobStatus's private
 * parseJobError (duplicated locally — that helper isn't exported, and this task's scope is
 * limited to this file).
 */
function parseJobError(job: JobStatus): string {
  if (job.error_json) {
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
  return job.status === "cancelled" ? "Produktion abgebrochen." : "Produktion fehlgeschlagen.";
}

function errorMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/**
 * Owns a v2 production session's lifecycle for one asset: creating/resuming the session,
 * polling its job + board status every 2.5s, and posting follow-up messages. Session identity
 * (`sessionId`/`jobId`) survives an app reload via `localStorage`
 * (`laura.production.<assetId>`), so a page refresh mid-run resumes polling instead of losing
 * the session.
 */
export function useProductionSession(
  client: LauraClient,
  assetId: string | null,
): ProductionSessionController {
  const [state, setState] = useState<ProductionSessionState>(IDLE_STATE);

  // Generation token: bumped whenever the active poll target is superseded (asset switch,
  // unmount, a fresh start()/sendMessage(), or reset()). Every async continuation checks it
  // before writing state, so a stale in-flight fetch from a previous target/asset can never
  // clobber current state (mirrors useAnalysis's pollGen).
  const genRef = useRef(0);
  const timerRef = useRef<number | null>(null);

  const stopPolling = useCallback((): void => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  // One poll tick: fetch the job; while non-terminal, best-effort refresh the board status
  // (failures ignored — right after job creation the board may not exist yet). On a terminal
  // job, resolve phase done/error and fetch the final board status defensively: a job that
  // failed before ever creating a board leaves getProductionStatus 404ing forever, so that
  // fetch's failure must not crash the hook — it just leaves `status` null.
  // Resolves true once the job has reached a terminal state (the caller then stops the timer).
  const checkOnce = useCallback(
    async (gen: number, sessionId: string, jobId: string): Promise<boolean> => {
      let job: JobStatus;
      try {
        job = await client.getJob(jobId);
      } catch (e) {
        if (genRef.current === gen) {
          log.warn("useProductionSession: job poll failed, retrying next tick", e);
        }
        return false;
      }
      if (genRef.current !== gen) return false;

      if (!TERMINAL_JOB_STATUSES.has(job.status)) {
        try {
          const status = await client.getProductionStatus(sessionId);
          if (genRef.current === gen) {
            setState((prev) => ({ ...prev, status }));
          }
        } catch (e) {
          // The board may not exist yet right after job creation — ignore while running.
          log.warn("useProductionSession: status poll failed, ignoring while job runs", e);
        }
        return false;
      }

      let finalStatus: ProductionStatus | null = null;
      try {
        finalStatus = await client.getProductionStatus(sessionId);
      } catch (e) {
        log.warn("useProductionSession: final status fetch failed", e);
      }
      if (genRef.current !== gen) return true;

      if (job.status === "succeeded") {
        setState((prev) => ({
          ...prev,
          phase: "done",
          jobResult: parseJobResult(job),
          status: finalStatus,
          error: null,
        }));
      } else {
        setState((prev) => ({
          ...prev,
          phase: "error",
          error: parseJobError(job),
          jobResult: null,
          status: finalStatus,
        }));
      }
      return true;
    },
    [client],
  );

  const startPolling = useCallback(
    (gen: number, sessionId: string, jobId: string): void => {
      stopPolling();
      timerRef.current = window.setInterval(() => {
        void checkOnce(gen, sessionId, jobId).then((terminal) => {
          if (terminal && genRef.current === gen) stopPolling();
        });
      }, POLL_INTERVAL_MS);
    },
    [checkOnce, stopPolling],
  );

  // Mount / asset switch: drop any poll timer for the previous asset, then either resume a
  // stored session (checked once immediately) or reset to idle.
  useEffect(() => {
    const gen = ++genRef.current;
    stopPolling();
    setState(IDLE_STATE);

    const stored = assetId ? readStoredSession(assetId) : null;
    if (stored) {
      setState({
        phase: "running",
        sessionId: stored.sessionId,
        jobId: stored.jobId,
        status: null,
        jobResult: null,
        error: null,
      });

      void checkOnce(gen, stored.sessionId, stored.jobId).then((terminal) => {
        if (!terminal && genRef.current === gen) {
          startPolling(gen, stored.sessionId, stored.jobId);
        }
      });
    }

    // Always register cleanup, even when this run found nothing to resume: start()/
    // sendMessage() can populate timerRef *after* this effect settled (they run
    // imperatively, outside this effect), and this is the only cleanup tied to unmount /
    // asset-switch that can stop that timer. An early return here would silently skip
    // registering it and leak the interval past unmount.
    return () => {
      genRef.current += 1;
      stopPolling();
    };
  }, [client, assetId, checkOnce, startPolling, stopPolling]);

  const start = useCallback(
    async (task: string, targetSeconds?: number): Promise<void> => {
      if (!assetId) return;
      const gen = ++genRef.current;
      stopPolling();
      let created: ProductionCreated;
      try {
        created = await client.createProduction(assetId, task, targetSeconds);
      } catch (e) {
        if (genRef.current === gen) {
          setState((prev) => ({ ...prev, phase: "error", error: errorMessage(e) }));
        }
        return;
      }
      if (genRef.current !== gen) return;
      writeStoredSession(assetId, { sessionId: created.session_id, jobId: created.job_id });
      setState({
        phase: "running",
        sessionId: created.session_id,
        jobId: created.job_id,
        status: null,
        jobResult: null,
        error: null,
      });
      startPolling(gen, created.session_id, created.job_id);
    },
    [client, assetId, stopPolling, startPolling],
  );

  const sendMessage = useCallback(
    async (text: string): Promise<void> => {
      const sessionId = state.sessionId;
      if (!sessionId) return;
      const gen = ++genRef.current;
      stopPolling();
      let created: ProductionCreated;
      try {
        created = await client.sendProductionMessage(sessionId, text);
      } catch (e) {
        if (genRef.current === gen) {
          setState((prev) => ({ ...prev, phase: "error", error: errorMessage(e) }));
        }
        return;
      }
      if (genRef.current !== gen) return;
      if (assetId) writeStoredSession(assetId, { sessionId, jobId: created.job_id });
      setState((prev) => ({
        ...prev,
        phase: "running",
        jobId: created.job_id,
        jobResult: null,
        error: null,
      }));
      startPolling(gen, sessionId, created.job_id);
    },
    [client, assetId, state.sessionId, stopPolling, startPolling],
  );

  const reset = useCallback((): void => {
    genRef.current += 1;
    stopPolling();
    if (assetId) clearStoredSession(assetId);
    setState(IDLE_STATE);
  }, [assetId, stopPolling]);

  return { state, start, sendMessage, reset };
}
