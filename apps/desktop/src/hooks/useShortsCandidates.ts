import { useCallback, useEffect, useState } from "react";

import { type LauraClient, type ShortsCandidate } from "../api";

export interface ShortsCandidatesController {
  candidates: ShortsCandidate[];
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
}

export function useShortsCandidates(
  client: LauraClient | null,
  assetId: string | null,
): ShortsCandidatesController {
  const [candidates, setCandidates] = useState<ShortsCandidate[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!client || !assetId) {
      setCandidates([]);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setCandidates(await client.listShortsCandidates(assetId));
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [client, assetId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { candidates, loading, error, reload };
}
