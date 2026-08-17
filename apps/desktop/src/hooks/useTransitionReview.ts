import { useCallback, useState } from "react";

import type {
  ApplyFixResult,
  BoundaryIdentity,
  LauraClient,
  SuggestedFix,
  TransitionVerdict,
} from "../api";

export interface UseTransitionReview {
  verdicts: TransitionVerdict[];
  loading: boolean;
  error: string | null;
  /** Enqueue a review and poll until verdicts arrive (or the model is absent → stays empty). */
  run: () => Promise<void>;
  /** Re-read cached verdicts without re-running the model. */
  refresh: () => Promise<void>;
  /** Apply the verdict's suggested fix (or an override) at its boundary. */
  apply: (verdict: TransitionVerdict, fix?: SuggestedFix) => Promise<ApplyFixResult>;
}

const POLL_INTERVAL_MS = 1500;
// Cover a cold model load plus many per-boundary inferences (~18s each): the review job can run
// for minutes on a long scene. We poll the JOB STATUS rather than stopping at the first verdict,
// so the panel keeps refreshing as each boundary finishes and finalises when the job is done.
const POLL_ATTEMPTS = 240;
const TERMINAL_STATUS = new Set(["succeeded", "failed", "cancelled"]);

function identityOf(verdict: TransitionVerdict): BoundaryIdentity {
  return {
    asset_a: verdict.asset_a,
    asset_b: verdict.asset_b,
    src_out_a: verdict.src_out_a,
    src_in_b: verdict.src_in_b,
  };
}

export function useTransitionReview(
  client: LauraClient,
  timelineId: string | null,
): UseTransitionReview {
  const [verdicts, setVerdicts] = useState<TransitionVerdict[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async (): Promise<void> => {
    if (timelineId == null) {
      setVerdicts([]);
      return;
    }
    const res = await client.getTransitionReview(timelineId);
    setVerdicts(res.verdicts);
  }, [client, timelineId]);

  const run = useCallback(async (): Promise<void> => {
    if (timelineId == null) return;
    setLoading(true);
    setError(null);
    try {
      const { job_id } = await client.reviewTransitions(timelineId);
      for (let i = 0; i < POLL_ATTEMPTS; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
        // Refresh verdicts every cycle so they surface as the model finishes each boundary.
        const res = await client.getTransitionReview(timelineId);
        setVerdicts(res.verdicts);
        // Stop when the review JOB is actually done — not at the first verdict — so every
        // boundary's verdict lands in the panel and a long scene finalises correctly.
        let done = false;
        try {
          const job = await client.getJob(job_id);
          done = TERMINAL_STATUS.has(job.status);
        } catch {
          // Transient job-status hiccup: keep polling on verdicts rather than aborting.
        }
        if (done) {
          const final = await client.getTransitionReview(timelineId);
          setVerdicts(final.verdicts);
          break;
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Transition review failed");
    } finally {
      setLoading(false);
    }
  }, [client, timelineId]);

  const apply = useCallback(
    async (verdict: TransitionVerdict, fix?: SuggestedFix): Promise<ApplyFixResult> => {
      if (timelineId == null) throw new Error("no timeline");
      const result = await client.applyTransitionFix(
        timelineId,
        identityOf(verdict),
        fix ?? verdict.suggested_fix,
      );
      await refresh();
      return result;
    },
    [client, timelineId, refresh],
  );

  return { verdicts, loading, error, run, refresh, apply };
}
