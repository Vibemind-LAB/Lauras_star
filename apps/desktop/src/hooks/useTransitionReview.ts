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

const POLL_INTERVAL_MS = 1000;
const POLL_ATTEMPTS = 30;

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
      await client.reviewTransitions(timelineId);
      for (let i = 0; i < POLL_ATTEMPTS; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
        const res = await client.getTransitionReview(timelineId);
        setVerdicts(res.verdicts);
        if (res.verdicts.length > 0) break;
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Übergangs-Prüfung fehlgeschlagen");
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
