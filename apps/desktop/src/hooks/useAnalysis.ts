import { useCallback, useEffect, useRef, useState } from "react";

import { type AnalysisRun, type Asset, type LauraClient, type Segment, type Shot } from "../api";

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

/** How often to poll the backend analysis run while it is still running. */
const POLL_INTERVAL_MS = 1000;
/**
 * Tolerate transient poll failures (a momentary local-API restart, a 503) without
 * aborting a multi-minute analysis: only give up after this many *consecutive* errors.
 * Mirrors the resilience of useImportStatus, which retries on every poll error.
 */
const MAX_CONSECUTIVE_POLL_ERRORS = 5;

export type AnalysisStatus = "idle" | "running" | "done" | "error";

/** A human note when ASR was skipped (e.g. the optional extra is absent). */
function asrNote(diagnostics: Record<string, unknown>): string | null {
  const asr = diagnostics["asr"];
  if (asr && typeof asr === "object") {
    const rec = asr as Record<string, unknown>;
    if (rec["status"] === "skipped" && typeof rec["reason"] === "string") {
      return `Transcript skipped: ${rec["reason"]}`;
    }
    // ASR can fail *within* a run whose scene stage succeeded (status "succeeded" overall),
    // so surface the sub-stage failure explicitly — otherwise the transcript is silently
    // empty with no explanation. The most common local cause is memory exhaustion.
    if (rec["status"] === "failed") {
      const reason = typeof rec["error"] === "string" ? rec["error"] : "unknown error";
      if (/malloc|memory|allocate|1455|paging/i.test(reason)) {
        return "Transcript failed: out of memory. Close other programs and generate it again.";
      }
      return `Transkript fehlgeschlagen: ${reason.slice(0, 140)}`;
    }
  }
  return null;
}

export interface AnalysisController {
  status: AnalysisStatus;
  shots: Shot[];
  segments: Segment[];
  note: string | null;
  error: string | null;
  diarize: boolean;
  align: boolean;
  detector: string;
  setDiarize: (v: boolean) => void;
  setAlign: (v: boolean) => void;
  setDetector: (v: string) => void;
  runAnalysis: () => Promise<void>;
  reload: () => Promise<void>;
}

/**
 * Owns an asset's analysis state — shots + transcript + the run trigger — lifted out of
 * the old AnalysisPanel so the inspector (shots/controls) and the transcript bar can share
 * one source of truth in the 4-zone layout.
 */
export function useAnalysis(client: LauraClient | null, asset: Asset | null): AnalysisController {
  const [status, setStatus] = useState<AnalysisStatus>("idle");
  const [shots, setShots] = useState<Shot[]>([]);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [diarize, setDiarize] = useState(false);
  const [align, setAlign] = useState(false);
  const [detector, setDetector] = useState<string>("adaptive");

  const assetId = asset?.id ?? null;

  // Generation token for the in-flight poll. Bumped when the asset changes / the hook
  // unmounts, and at the start of each runAnalysis, so a long-running poll (analysis of
  // a 30-min video can take many minutes) never writes stale state for a different run.
  const pollGen = useRef(0);
  useEffect(() => () => {
    pollGen.current += 1;
  }, [assetId]);

  const reload = useCallback(async () => {
    if (!client || !assetId) return;
    const [sh, tr] = await Promise.all([client.getShots(assetId), client.getTranscript(assetId)]);
    setShots(sh);
    setSegments(tr);
  }, [client, assetId]);

  // Polls getLatestAnalysis until terminal, then loads shots+segments — all writes guarded
  // by `gen` so a fast asset switch can't land stale results. Sets status done|error + note.
  const drainRun = useCallback(
    async (gen: number): Promise<void> => {
      if (!client || !assetId) return;
      try {
        // Poll until the backend run reaches a terminal status. Analysis time scales with
        // media duration (transcribing a 30-min video runs for many minutes), so we wait
        // for the job to actually finish instead of giving up after a fixed deadline — the
        // old fixed cap reported "done" with an empty transcript on long videos.
        let terminal: AnalysisRun | null = null;
        let consecutiveErrors = 0;
        while (pollGen.current === gen) {
          try {
            const run = await client.getLatestAnalysis(assetId);
            if (pollGen.current !== gen) return; // asset changed / unmounted mid-poll
            consecutiveErrors = 0;
            if (run && (run.status === "succeeded" || run.status === "failed")) {
              terminal = run;
              break;
            }
          } catch (e) {
            if (pollGen.current !== gen) return;
            if (++consecutiveErrors >= MAX_CONSECUTIVE_POLL_ERRORS) throw e;
          }
          await sleep(POLL_INTERVAL_MS);
        }
        if (pollGen.current !== gen) return;
        // Inline the reload so each state write is guarded by the same generation token:
        // reload() writes shots/segments unconditionally and could otherwise land an old
        // asset's results after a fast asset switch (its cleanup fires only after paint).
        const [sh, tr] = await Promise.all([
          client.getShots(assetId),
          client.getTranscript(assetId),
        ]);
        if (pollGen.current !== gen) return;
        setShots(sh);
        setSegments(tr);
        if (terminal) setNote(asrNote(terminal.diagnostics));
        if (terminal?.status === "failed") {
          setError("Analyse fehlgeschlagen — Details im Job-Log.");
          setStatus("error");
        } else {
          setStatus("done");
        }
      } catch (e) {
        if (pollGen.current !== gen) return;
        setError(String(e));
        setStatus("error");
      }
    },
    [client, assetId],
  );

  useEffect(() => {
    setStatus("idle");
    setShots([]);
    setSegments([]);
    setNote(null);
    setError(null);
    if (!client || !assetId) return;
    // Capture a fresh generation BEFORE the await so a fast asset switch can't land this
    // asset's results: the cleanup effect bumps pollGen, making the post-await check fail.
    const gen = ++pollGen.current;
    void (async () => {
      const run = await client.getLatestAnalysis(assetId);
      if (pollGen.current !== gen) return;
      if (!run) return; // no run yet — stay idle
      setNote(asrNote(run.diagnostics));
      if (run.status === "succeeded") {
        setStatus("done");
        // Guarded reload (same generation token) so a stale asset can't win.
        const [sh, tr] = await Promise.all([
          client.getShots(assetId),
          client.getTranscript(assetId),
        ]);
        if (pollGen.current !== gen) return;
        setShots(sh);
        setSegments(tr);
      } else if (run.status === "failed") {
        setError("Analyse fehlgeschlagen — Details im Job-Log.");
        setStatus("error");
      } else {
        // Non-terminal background run (queued/running): show progress and poll to terminal,
        // so an auto-analysis finishing while the user watches refreshes shots/transcript.
        setStatus("running");
        await drainRun(gen);
      }
    })();
  }, [client, assetId, drainRun]);

  const runAnalysis = useCallback(async () => {
    if (!client || !assetId) return;
    const gen = ++pollGen.current; // supersede any prior in-flight poll
    setStatus("running");
    setError(null);
    try {
      await client.startAnalysis(assetId, { scene: true, asr: true, diarize, align, detector });
    } catch (e) {
      if (pollGen.current !== gen) return;
      setError(String(e));
      setStatus("error");
      return;
    }
    await drainRun(gen);
  }, [client, assetId, diarize, align, detector, drainRun]);

  return {
    status, shots, segments, note, error,
    diarize, align, detector, setDiarize, setAlign, setDetector, runAnalysis, reload,
  };
}
