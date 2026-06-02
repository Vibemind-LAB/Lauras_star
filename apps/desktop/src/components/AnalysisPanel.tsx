import { type ReactElement, useCallback, useEffect, useState } from "react";

import { type Asset, type LauraClient, type Segment, type Shot, type Timeline } from "../api";

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

type Status = "idle" | "running" | "done" | "error";

function ShotStrip({
  shots,
  totalFrames,
  onAppend,
}: {
  shots: Shot[];
  totalFrames: number;
  onAppend?: (shot: Shot) => void;
}): ReactElement {
  if (totalFrames <= 0 || shots.length === 0) {
    return <div className="text-xs text-slate-600">keine Shots</div>;
  }
  return (
    <div className="flex h-8 w-full overflow-hidden rounded-md border border-edge">
      {shots.map((s, i) => {
        const pct = ((s.src_out_frame_exclusive - s.src_in_frame) / totalFrames) * 100;
        return (
          <button
            key={s.id}
            type="button"
            disabled={!onAppend}
            onClick={() => onAppend?.(s)}
            title={
              `Shot ${i + 1}: ${s.src_in_frame}–${s.src_out_frame_exclusive}` +
              (onAppend ? " (Klick = an Rough Cut anhängen)" : "")
            }
            style={{ width: `${pct}%` }}
            className={`${i % 2 === 0 ? "bg-sky-700/40" : "bg-sky-500/30"} ${
              onAppend ? "hover:bg-emerald-600/50" : ""
            }`}
          />
        );
      })}
    </div>
  );
}

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

export function AnalysisPanel({
  client,
  asset,
  roughCut,
  onTimelineChange,
}: {
  client: LauraClient;
  asset: Asset;
  roughCut: Timeline | null;
  onTimelineChange: () => void;
}): ReactElement {
  const [status, setStatus] = useState<Status>("idle");
  const [shots, setShots] = useState<Shot[]>([]);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadResults = useCallback(async () => {
    const [sh, tr] = await Promise.all([client.getShots(asset.id), client.getTranscript(asset.id)]);
    setShots(sh);
    setSegments(tr);
  }, [client, asset.id]);

  useEffect(() => {
    let cancelled = false;
    setStatus("idle");
    setShots([]);
    setSegments([]);
    setNote(null);
    setError(null);
    void (async () => {
      const run = await client.getLatestAnalysis(asset.id);
      if (cancelled || !run) return;
      setNote(asrNote(run.diagnostics));
      if (run.status === "succeeded") {
        setStatus("done");
        await loadResults();
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client, asset.id, loadResults]);

  async function runAnalysis(): Promise<void> {
    setStatus("running");
    setError(null);
    try {
      await client.startAnalysis(asset.id, { scene: true, asr: true });
      for (let i = 0; i < 180; i++) {
        const run = await client.getLatestAnalysis(asset.id);
        if (run && (run.status === "succeeded" || run.status === "failed")) {
          setNote(asrNote(run.diagnostics));
          break;
        }
        await sleep(700);
      }
      await loadResults();
      setStatus("done");
    } catch (e) {
      setError(String(e));
      setStatus("error");
    }
  }

  async function exportCaptions(fmt: "srt" | "vtt"): Promise<void> {
    try {
      const text = await client.getCaptions(asset.id, fmt);
      await window.laura.saveTextFile(`${asset.display_name}.${fmt}`, text);
    } catch (e) {
      setError(String(e));
    }
  }

  async function appendShot(shot: Shot): Promise<void> {
    if (!roughCut) return;
    try {
      await client.applyOperation(roughCut.id, {
        op: "append_clip",
        asset_id: asset.id,
        src_in_frame: shot.src_in_frame,
        src_out_frame_exclusive: shot.src_out_frame_exclusive,
      });
      onTimelineChange();
    } catch (e) {
      setError(String(e));
    }
  }

  async function appendSegment(seg: Segment): Promise<void> {
    if (!roughCut || seg.words.length === 0) return;
    try {
      await client.applyOperation(roughCut.id, {
        op: "append_from_words",
        word_start_id: seg.words[0].id,
        word_end_id: seg.words[seg.words.length - 1].id,
      });
      onTimelineChange();
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs uppercase tracking-wide text-slate-500">Analyse</span>
        <button
          type="button"
          onClick={() => void runAnalysis()}
          disabled={status === "running"}
          className="rounded-md bg-panel px-2 py-1 text-xs text-slate-200 transition hover:bg-edge disabled:opacity-40"
        >
          {status === "running" ? "Analysiere…" : "Analyse starten"}
        </button>
      </div>

      {error && <div className="text-xs text-red-400">{error}</div>}

      <div>
        <div className="mb-1 text-xs text-slate-500">Shots ({shots.length})</div>
        <ShotStrip
          shots={shots}
          totalFrames={asset.duration_frames ?? 0}
          onAppend={roughCut ? appendShot : undefined}
        />
      </div>

      <div>
        <div className="mb-1 flex items-center justify-between">
          <span className="text-xs text-slate-500">Transkript</span>
          {segments.length > 0 && (
            <span className="flex gap-1">
              <button
                type="button"
                onClick={() => void exportCaptions("srt")}
                className="rounded bg-panel px-2 py-0.5 text-xs text-slate-300 hover:bg-edge"
              >
                SRT
              </button>
              <button
                type="button"
                onClick={() => void exportCaptions("vtt")}
                className="rounded bg-panel px-2 py-0.5 text-xs text-slate-300 hover:bg-edge"
              >
                VTT
              </button>
            </span>
          )}
        </div>
        {segments.length === 0 ? (
          <div className="text-xs text-slate-600">{note ?? "noch kein Transkript"}</div>
        ) : (
          <ul className="max-h-48 space-y-1 overflow-auto">
            {segments.map((seg) => (
              <li
                key={seg.id}
                className="flex items-start justify-between gap-2 rounded-md bg-panel px-3 py-2 text-sm text-slate-200"
              >
                <span>
                  {seg.speaker_label && (
                    <span className="mr-2 rounded bg-ink px-1.5 py-0.5 text-xs text-sky-300">
                      {seg.speaker_label}
                    </span>
                  )}
                  {seg.text}
                </span>
                {roughCut && seg.words.length > 0 && (
                  <button
                    type="button"
                    onClick={() => void appendSegment(seg)}
                    title="an Rough Cut anhängen"
                    className="shrink-0 rounded bg-ink px-2 text-xs text-emerald-300 hover:bg-edge"
                  >
                    →
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
