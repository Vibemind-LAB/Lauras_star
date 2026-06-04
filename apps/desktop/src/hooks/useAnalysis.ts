import { useCallback, useEffect, useState } from "react";

import { type Asset, type LauraClient, type Segment, type Shot } from "../api";

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

export type AnalysisStatus = "idle" | "running" | "done" | "error";

/** A human note when ASR was skipped (e.g. the optional extra is absent). */
function asrNote(diagnostics: Record<string, unknown>): string | null {
  const asr = diagnostics["asr"];
  if (asr && typeof asr === "object") {
    const rec = asr as Record<string, unknown>;
    if (rec["status"] === "skipped" && typeof rec["reason"] === "string") {
      return `Transkript übersprungen: ${rec["reason"]}`;
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

  const reload = useCallback(async () => {
    if (!client || !assetId) return;
    const [sh, tr] = await Promise.all([client.getShots(assetId), client.getTranscript(assetId)]);
    setShots(sh);
    setSegments(tr);
  }, [client, assetId]);

  useEffect(() => {
    let cancelled = false;
    setStatus("idle");
    setShots([]);
    setSegments([]);
    setNote(null);
    setError(null);
    if (!client || !assetId) return;
    void (async () => {
      const run = await client.getLatestAnalysis(assetId);
      if (cancelled || !run) return;
      setNote(asrNote(run.diagnostics));
      if (run.status === "succeeded") {
        setStatus("done");
        await reload();
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client, assetId, reload]);

  const runAnalysis = useCallback(async () => {
    if (!client || !assetId) return;
    setStatus("running");
    setError(null);
    try {
      await client.startAnalysis(assetId, { scene: true, asr: true, diarize, align, detector });
      for (let i = 0; i < 180; i++) {
        const run = await client.getLatestAnalysis(assetId);
        if (run && (run.status === "succeeded" || run.status === "failed")) {
          setNote(asrNote(run.diagnostics));
          break;
        }
        await sleep(700);
      }
      await reload();
      setStatus("done");
    } catch (e) {
      setError(String(e));
      setStatus("error");
    }
  }, [client, assetId, diarize, align, detector, reload]);

  return {
    status, shots, segments, note, error,
    diarize, align, detector, setDiarize, setAlign, setDetector, runAnalysis, reload,
  };
}
