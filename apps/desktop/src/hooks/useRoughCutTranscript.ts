import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { type LauraClient, type Scene, type Segment, type TimelineClip } from "../api";
import { type CutWord, projectCutWords } from "../shared/transcriptProjection";
import { buildVoiceoverCommit } from "../shared/spanReplaceCommit";

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
  /** The job_id of the most recently dispatched VO job, or null if none. */
  lastVoJobId: string | null;
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
 *
 * @param assetId - When provided, restricts word-to-clip matching to clips from that asset only
 *                  (prevents cross-asset mis-projection on multi-asset rough-cuts). See
 *                  `projectCutWords` for details.
 */
export function useRoughCutTranscript(
  client: LauraClient | null,
  roughCutId: string | null,
  segments: Segment[],
  assetId?: string,
): RoughCutTranscriptController {
  const [clips, setClips] = useState<TimelineClip[]>([]);
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [selection, setSelection] = useState<
    { startWordId: string; endWordId: string } | null
  >(null);
  const [error, setError] = useState<string | null>(null);
  const [lastVoJobId, setLastVoJobId] = useState<string | null>(null);
  /** Debounce timer ref for replaceSpanText — cancelled on unmount to prevent setState-after-unmount. */
  const voDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (voDebounceRef.current !== null) {
        clearTimeout(voDebounceRef.current);
        voDebounceRef.current = null;
      }
    };
  }, []);

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

  const words = useMemo(
    () => projectCutWords(segments, clips, assetId),
    [segments, clips, assetId],
  );

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
    (
      startWordId: string,
      endWordId: string,
      newText: string,
      voiceId: string,
    ): Promise<void> => {
      // Debounce: cancel any pending timer so rapid edits coalesce into one VO call.
      if (voDebounceRef.current !== null) {
        clearTimeout(voDebounceRef.current);
        voDebounceRef.current = null;
      }

      return new Promise<void>((resolve) => {
        voDebounceRef.current = setTimeout(() => {
          voDebounceRef.current = null;

          // Snapshot words at call time (closure over `words` from the memo).
          const commit = buildVoiceoverCommit({
            startWordId,
            endWordId,
            newText,
            voiceId: voiceId || null,
            words: words.map((w) => ({
              id: w.id,
              seq_in_frame: w.seqStart,
              seq_out_frame_exclusive: w.seqEnd,
              text: w.text,
            })),
          });

          if (!client || !roughCutId || commit === null) {
            // Nothing to enqueue (blank text, missing span, or no client). Reload anyway
            // so the projection stays fresh.
            void reload().then(resolve);
            return;
          }

          void client
            .createVoiceover(roughCutId, {
              text: commit.text,
              seqIn: commit.seqIn,
              seqOut: commit.seqOut,
              mixMode: commit.mixMode,
              duckingPercent: commit.duckingPercent,
              ...(commit.voiceId !== undefined ? { voiceId: commit.voiceId } : {}),
            })
            .then((accepted) => {
              if (!mountedRef.current) {
                resolve();
                return;
              }
              // Expose the new job id so FineCutView can track completion and
              // re-fetch audio clips once the VO is ready (Fix 1).
              setLastVoJobId(accepted.job_id);
              return reload();
            })
            .catch((e: unknown) => {
              if (mountedRef.current) {
                setError(String(e));
              }
            })
            .then(resolve);
        }, 400);
      });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [client, roughCutId, reload, words],
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
    lastVoJobId,
    error,
    reload,
  };
}
