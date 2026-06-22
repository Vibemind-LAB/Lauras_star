import { useCallback, useEffect, useState } from "react";

import { type LauraClient, type Scene } from "../api";

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
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!client || !timelineId) {
      setScenes([]);
      return;
    }
    try {
      setError(null);
      setScenes(await client.listScenes(timelineId));
    } catch (e) {
      setError(String(e));
    }
  }, [client, timelineId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const generate = useCallback(
    async (assetId: string) => {
      if (!client || !timelineId) return;
      setLoading(true);
      setError(null);
      try {
        setScenes(await client.generateScenes(timelineId, assetId));
      } catch (e) {
        setError(String(e));
        throw e;
      } finally {
        setLoading(false);
      }
    },
    [client, timelineId],
  );

  const split = useCallback(
    async (sceneId: string, atSeqFrame: number) => {
      if (!client || !timelineId) return;
      try {
        setScenes(await client.splitScene(timelineId, sceneId, atSeqFrame));
      } catch (e) {
        setError(String(e));
      }
    },
    [client, timelineId],
  );

  const merge = useCallback(
    async (sceneId: string) => {
      if (!client || !timelineId) return;
      try {
        setScenes(await client.mergeScenes(timelineId, sceneId));
      } catch (e) {
        setError(String(e));
      }
    },
    [client, timelineId],
  );

  const rename = useCallback(
    async (sceneId: string, name: string) => {
      if (!client || !timelineId) return;
      try {
        await client.renameScene(sceneId, name);
        await reload();
      } catch (e) {
        setError(String(e));
      }
    },
    [client, timelineId, reload],
  );

  return { scenes, loading, error, generate, split, merge, rename, reload };
}
