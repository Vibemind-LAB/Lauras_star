// apps/desktop/src/hooks/useSequence.ts
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";

import { type LauraClient, type Sequence } from "../api";
import { qk } from "../cache/queryKeys";

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
  const queryClient = useQueryClient();
  const [mutationError, setMutationError] = useState<string | null>(null);

  const enabled = client !== null && projectId !== null;
  const query = useQuery({
    queryKey: qk.sequence(projectId ?? "none"),
    queryFn: () => client!.getProjectSequence(projectId!),
    enabled,
  });

  const reload = useCallback(async () => {
    if (projectId) {
      await queryClient.invalidateQueries({ queryKey: qk.sequence(projectId) });
    }
  }, [queryClient, projectId]);

  const sequence = query.data ?? null;

  const setScenes = useCallback(
    async (sceneIds: string[]) => {
      if (!client || !sequence || !projectId) return;
      try {
        // Cancel any in-flight sequence fetch up front so it resolves before — never after —
        // our authoritative write below.
        await queryClient.cancelQueries({ queryKey: qk.sequence(projectId) });
        const next = await client.setSequenceScenes(sequence.timeline_id, sceneIds);
        queryClient.setQueryData(qk.sequence(projectId), next);
        // The flattened sequence (resolved clips) depends on the scene set.
        await queryClient.invalidateQueries({ queryKey: ["sequenceFlattened"] });
      } catch (e) {
        setMutationError(String(e));
      }
    },
    [client, sequence, projectId, queryClient],
  );

  return {
    sequence,
    error: mutationError ?? (query.error ? String(query.error) : null),
    setScenes,
    reload,
  };
}
