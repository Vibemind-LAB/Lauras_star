import { useCallback, useEffect, useState } from "react";

import { type LauraClient, type Timeline } from "../api";

export interface SceneTimelineController {
  timeline: Timeline | null;
  loading: boolean;
  error: string | null;
  deleteWords: (wordStartId: string, wordEndId: string) => Promise<void>;
  reload: () => Promise<void>;
}

export function useSceneTimeline(
  client: LauraClient | null,
  sceneId: string | null,
): SceneTimelineController {
  const [timeline, setTimeline] = useState<Timeline | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!client || !sceneId) {
      setTimeline(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setTimeline(await client.openScene(sceneId));
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [client, sceneId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const deleteWords = useCallback(
    async (wordStartId: string, wordEndId: string) => {
      if (!client || !timeline) return;
      try {
        setTimeline(await client.deleteWords(timeline.id, wordStartId, wordEndId));
      } catch (e) {
        setError(String(e));
      }
    },
    [client, timeline],
  );

  return { timeline, loading, error, deleteWords, reload };
}
