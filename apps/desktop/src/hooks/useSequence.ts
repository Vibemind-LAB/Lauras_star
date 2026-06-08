// apps/desktop/src/hooks/useSequence.ts
import { useCallback, useEffect, useState } from "react";

import { type LauraClient, type Sequence } from "../api";

export interface SequenceController {
  sequence: Sequence | null;
  error: string | null;
  setScenes: (sceneIds: string[]) => Promise<void>;
  reload: () => Promise<void>;
}

export function useSequence(
  client: LauraClient | null,
  projectId: string | null,
): SequenceController {
  const [sequence, setSequence] = useState<Sequence | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!client || !projectId) {
      setSequence(null);
      return;
    }
    try {
      setError(null);
      setSequence(await client.getProjectSequence(projectId));
    } catch (e) {
      setError(String(e));
    }
  }, [client, projectId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const setScenes = useCallback(
    async (sceneIds: string[]) => {
      if (!client || !sequence) return;
      try {
        setSequence(await client.setSequenceScenes(sequence.timeline_id, sceneIds));
      } catch (e) {
        setError(String(e));
      }
    },
    [client, sequence],
  );

  return { sequence, error, setScenes, reload };
}
