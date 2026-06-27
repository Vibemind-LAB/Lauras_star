import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";

import { type LauraClient, type Scene } from "../api";
import { qk } from "../cache/queryKeys";

export interface ScenesController {
  scenes: Scene[];
  loading: boolean;
  error: string | null;
  generate: (assetId: string) => Promise<void>;
  split: (sceneId: string, atSeqFrame: number) => Promise<void>;
  merge: (sceneId: string) => Promise<void>;
  rename: (sceneId: string, name: string) => Promise<void>;
  reload: () => Promise<void>;
}

export function useScenes(
  client: LauraClient | null,
  timelineId: string | null,
): ScenesController {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [mutationError, setMutationError] = useState<string | null>(null);

  const enabled = client !== null && timelineId !== null;
  const query = useQuery({
    queryKey: qk.scenes(timelineId ?? "none"),
    queryFn: () => client!.listScenes(timelineId!),
    enabled,
  });

  const reload = useCallback(async () => {
    if (timelineId) {
      await queryClient.invalidateQueries({ queryKey: qk.scenes(timelineId) });
    }
  }, [queryClient, timelineId]);

  // Scene ids are unstable across (re)generate / split / merge. The sequence and the
  // project-wide scene list reference scene ids, so they go stale the instant scenes change —
  // invalidate them (prefix match) so any view showing them refetches fresh on next read.
  const invalidateDependents = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["sequence"] }),
      queryClient.invalidateQueries({ queryKey: ["sequenceFlattened"] }),
      queryClient.invalidateQueries({ queryKey: ["projectScenes"] }),
    ]);
  }, [queryClient]);

  const writeScenes = useCallback(
    async (next: Scene[]) => {
      if (!timelineId) return;
      // Cancel any in-flight scenes fetch first, so a late-resolving list can't clobber this
      // fresh mutation result (the classic optimistic-update race).
      await queryClient.cancelQueries({ queryKey: qk.scenes(timelineId) });
      queryClient.setQueryData(qk.scenes(timelineId), next);
    },
    [queryClient, timelineId],
  );

  const generate = useCallback(
    async (assetId: string) => {
      if (!client || !timelineId) return;
      setBusy(true);
      setMutationError(null);
      try {
        await writeScenes(await client.generateScenes(timelineId, assetId));
        await invalidateDependents();
      } catch (e) {
        setMutationError(String(e));
        throw e;
      } finally {
        setBusy(false);
      }
    },
    [client, timelineId, writeScenes, invalidateDependents],
  );

  const split = useCallback(
    async (sceneId: string, atSeqFrame: number) => {
      if (!client || !timelineId) return;
      try {
        await writeScenes(await client.splitScene(timelineId, sceneId, atSeqFrame));
        await invalidateDependents();
      } catch (e) {
        setMutationError(String(e));
      }
    },
    [client, timelineId, writeScenes, invalidateDependents],
  );

  const merge = useCallback(
    async (sceneId: string) => {
      if (!client || !timelineId) return;
      try {
        await writeScenes(await client.mergeScenes(timelineId, sceneId));
        await invalidateDependents();
      } catch (e) {
        setMutationError(String(e));
      }
    },
    [client, timelineId, writeScenes, invalidateDependents],
  );

  const rename = useCallback(
    async (sceneId: string, name: string) => {
      if (!client || !timelineId) return;
      try {
        await client.renameScene(sceneId, name);
        await reload();
      } catch (e) {
        setMutationError(String(e));
      }
    },
    [client, timelineId, reload],
  );

  return {
    scenes: query.data ?? [],
    loading: query.isLoading || busy,
    error: mutationError ?? (query.error ? String(query.error) : null),
    generate,
    split,
    merge,
    rename,
    reload,
  };
}
