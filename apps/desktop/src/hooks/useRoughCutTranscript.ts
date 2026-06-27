import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { type HistoryState, type LauraClient, type Scene, type Segment, type Timeline, type TimelineClip } from "../api";
import { qk } from "../cache/queryKeys";
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
  undo: () => Promise<void>;
  redo: () => Promise<void>;
  canUndo: boolean;
  canRedo: boolean;
  undoLabel: string | null;
  redoLabel: string | null;
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
  const queryClient = useQueryClient();

  const enabled = client !== null && roughCutId !== null;

  // Rough-cut timeline (clips). Key: ["timeline", roughCutId] — shared with any other view that
  // reads this same timeline, so a mutation in FineCutView instantly refreshes RoughCutView.
  const timelineQuery = useQuery<Timeline>({
    queryKey: qk.timeline(roughCutId ?? "none"),
    queryFn: () => client!.getTimeline(roughCutId!),
    enabled,
  });

  // Scene markers for this rough-cut. Key: ["scenes", roughCutId] — same key as useScenes uses,
  // so the two hooks share one cache entry and never drift.
  const scenesQuery = useQuery<Scene[]>({
    queryKey: qk.scenes(roughCutId ?? "none"),
    queryFn: () => client!.listScenes(roughCutId!),
    enabled,
  });

  const clips: TimelineClip[] = timelineQuery.data?.clips ?? [];
  const scenes: Scene[] = scenesQuery.data ?? [];

  const [selection, setSelection] = useState<
    { startWordId: string; endWordId: string } | null
  >(null);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [lastVoJobId, setLastVoJobId] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryState>({
    can_undo: false,
    can_redo: false,
    undo_label: null,
    redo_label: null,
  });
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

  const refreshHistory = useCallback(async () => {
    if (!client || !roughCutId) return;
    try {
      setHistory(await client.getHistory(roughCutId));
    } catch {
      /* non-fatal */
    }
  }, [client, roughCutId]);

  // reload() invalidates both timeline + scenes, causing useQuery to refetch.
  const reload = useCallback(async () => {
    if (!roughCutId) return;
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: qk.timeline(roughCutId) }),
      queryClient.invalidateQueries({ queryKey: qk.scenes(roughCutId) }),
    ]);
    void refreshHistory();
  }, [queryClient, roughCutId, refreshHistory]);

  const undo = useCallback(async () => {
    if (!client || !roughCutId) return;
    try {
      await client.undo(roughCutId);
      await reload();
      await refreshHistory();
    } catch (e) {
      setMutationError(String(e));
    }
  }, [client, roughCutId, reload, refreshHistory]);

  const redo = useCallback(async () => {
    if (!client || !roughCutId) return;
    try {
      await client.redo(roughCutId);
      await reload();
      await refreshHistory();
    } catch (e) {
      setMutationError(String(e));
    }
  }, [client, roughCutId, reload, refreshHistory]);

  // Initial history load (runs once per roughCutId).
  useEffect(() => {
    void refreshHistory();
  }, [refreshHistory]);

  const words = useMemo(
    () => projectCutWords(segments, clips, assetId),
    [segments, clips, assetId],
  );

  const deleteRange = useCallback(
    async (startWordId: string, endWordId: string) => {
      if (!client || !roughCutId) return;
      try {
        // Cancel in-flight fetches so the stale response can't clobber our fresh write.
        await queryClient.cancelQueries({ queryKey: qk.timeline(roughCutId) });
        await queryClient.cancelQueries({ queryKey: qk.scenes(roughCutId) });
        const newTimeline = await client.deleteWords(roughCutId, startWordId, endWordId);
        // deleteWords returns the updated Timeline (clips reconciled). Push it into cache.
        queryClient.setQueryData(qk.timeline(roughCutId), newTimeline);
        // Scene markers were also reconciled by the backend; refetch them + transcript.
        // Audio clips can change if deleteWords removes a word that was overlaid with VO.
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: qk.scenes(roughCutId) }),
          queryClient.invalidateQueries({ queryKey: qk.audioClips(roughCutId) }),
          assetId
            ? queryClient.invalidateQueries({ queryKey: qk.transcript(assetId) })
            : Promise.resolve(),
        ]);
        setSelection(null);
      } catch (e) {
        setMutationError(String(e));
      }
    },
    [client, roughCutId, assetId, queryClient],
  );

  const cutAt = useCallback(
    async (seqFrame: number) => {
      if (!client || !roughCutId) return;
      try {
        // Cancel in-flight fetches before writing both entries.
        await queryClient.cancelQueries({ queryKey: qk.timeline(roughCutId) });
        await queryClient.cancelQueries({ queryKey: qk.scenes(roughCutId) });
        const out = await client.cutAtFrame(roughCutId, seqFrame);
        // Push the fresh clips+scenes directly into cache — no extra round-trip needed.
        queryClient.setQueryData(qk.timeline(roughCutId), (prev: Timeline | undefined) =>
          prev ? { ...prev, clips: out.clips } : prev,
        );
        queryClient.setQueryData(qk.scenes(roughCutId), out.scenes);
        // cutAt changes word-to-clip mapping; invalidate transcript and audio clips (split can
        // divide a VO-overlaid segment, changing the A-track composition).
        await queryClient.invalidateQueries({ queryKey: qk.audioClips(roughCutId) });
        if (assetId) {
          await queryClient.invalidateQueries({ queryKey: qk.transcript(assetId) });
        }
      } catch (e) {
        setMutationError(String(e));
      }
    },
    [client, roughCutId, assetId, queryClient],
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
                setMutationError(String(e));
              }
            })
            .then(resolve);
        }, 400);
      });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [client, roughCutId, reload, words],
  );

  const queryError =
    timelineQuery.error != null
      ? String(timelineQuery.error)
      : scenesQuery.error != null
        ? String(scenesQuery.error)
        : null;

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
    error: mutationError ?? queryError,
    reload,
    undo,
    redo,
    canUndo: history.can_undo,
    canRedo: history.can_redo,
    undoLabel: history.undo_label,
    redoLabel: history.redo_label,
  };
}
