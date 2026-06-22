import { useCallback, useEffect, useMemo, useState } from "react";

import { type LauraClient, type Scene, type Segment, type TimelineClip } from "../api";
import { type CutWord, projectCutWords } from "../shared/transcriptProjection";

export interface RoughCutTranscriptController {
  words: CutWord[];
  scenes: Scene[];
  clips: TimelineClip[];
  selection: { startWordId: string; endWordId: string } | null;
  setSelection: (sel: { startWordId: string; endWordId: string } | null) => void;
  deleteRange: (startWordId: string, endWordId: string) => Promise<void>;
  cutAt: (seqFrame: number) => Promise<void>;
  replaceSpanText: (
    startWordId: string,
    endWordId: string,
    newText: string,
    voiceId: string,
  ) => Promise<void>;
  error: string | null;
  reload: () => Promise<void>;
}

/**
 * Drive the Feinschnitt against the CONTINUOUS rough-cut timeline (not isolated scene copies).
 * Loads the rough-cut clips + its scene markers and projects every surviving transcript word onto
 * continuous sequence frames. Edits run on the rough-cut directly: deleteRange ripple-deletes words
 * (the backend reconciles scene markers, §4.4), cutAt is the composite split_clip+split_scene.
 *
 * replaceSpanText is a Phase A seam: it records intent and reloads. The auto-VO/lipsync pipeline
 * (§5) is wired in Phase C — voiceId is plumbed through now so the signature is stable.
 */
export function useRoughCutTranscript(
  client: LauraClient | null,
  roughCutId: string | null,
  segments: Segment[],
): RoughCutTranscriptController {
  const [clips, setClips] = useState<TimelineClip[]>([]);
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [selection, setSelection] = useState<
    { startWordId: string; endWordId: string } | null
  >(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!client || !roughCutId) {
      setClips([]);
      setScenes([]);
      return;
    }
    try {
      setError(null);
      const [tl, sc] = await Promise.all([
        client.getTimeline(roughCutId),
        client.listScenes(roughCutId),
      ]);
      setClips(tl.clips);
      setScenes(sc);
    } catch (e) {
      setError(String(e));
    }
  }, [client, roughCutId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const words = useMemo(() => projectCutWords(segments, clips), [segments, clips]);

  const deleteRange = useCallback(
    async (startWordId: string, endWordId: string) => {
      if (!client || !roughCutId) return;
      try {
        await client.deleteWords(roughCutId, startWordId, endWordId);
        setSelection(null);
        await reload(); // backend already reconciled scene markers; re-read clips + scenes
      } catch (e) {
        setError(String(e));
      }
    },
    [client, roughCutId, reload],
  );

  const cutAt = useCallback(
    async (seqFrame: number) => {
      if (!client || !roughCutId) return;
      try {
        const out = await client.cutAtFrame(roughCutId, seqFrame);
        setClips(out.clips);
        setScenes(out.scenes);
      } catch (e) {
        setError(String(e));
      }
    },
    [client, roughCutId],
  );

  const replaceSpanText = useCallback(
    async (
      _startWordId: string,
      _endWordId: string,
      _newText: string,
      _voiceId: string,
    ) => {
      // Phase A seam: the auto-VO/lipsync pipeline (§5) lands in Phase C. Reload to keep the
      // projection fresh; arguments are plumbed so the contract is stable across phases.
      await reload();
    },
    [reload],
  );

  return {
    words,
    scenes,
    clips,
    selection,
    setSelection,
    deleteRange,
    cutAt,
    replaceSpanText,
    error,
    reload,
  };
}
