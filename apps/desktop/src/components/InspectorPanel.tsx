import { type ReactElement, useEffect, useState } from "react";

import { type Asset, hasFile, type LauraClient, type Shot } from "../api";
import { type AnalysisController } from "../hooks/useAnalysis";
import { ShotStrip } from "./ShotStrip";
import { Waveform } from "./Waveform";

function fmtFps(asset: Asset): string {
  if (!asset.rate_num || !asset.rate_den) return "—";
  const fps = Math.round((asset.rate_num / asset.rate_den) * 1000) / 1000;
  return `${fps}${asset.is_vfr ? " (VFR)" : ""}`;
}

function fmtDuration(asset: Asset): string {
  if (asset.duration_frames == null) return "—";
  if (asset.rate_num && asset.rate_den) {
    const secs = (asset.duration_frames * asset.rate_den) / asset.rate_num;
    return `${asset.duration_frames} frames · ${secs.toFixed(2)} s`;
  }
  return `${asset.duration_frames} frames`;
}

function Row({ label, value }: { label: string; value: string }): ReactElement {
  return (
    <div className="flex justify-between gap-4 py-1 text-sm">
      <span className="text-slate-500">{label}</span>
      <span className="truncate text-right text-slate-200">{value}</span>
    </div>
  );
}

/**
 * The inspector's default content (no clip selected): analysis controls + shots, then the
 * asset's poster, metadata and waveform. When a clip is selected the SceneInspector (P5)
 * takes this slot instead.
 */
export function InspectorPanel({
  client,
  asset,
  analysis,
  canAppend,
  onAppendShot,
  onBuildFromShots,
  buildResult,
}: {
  client: LauraClient;
  asset: Asset;
  analysis: AnalysisController;
  canAppend: boolean;
  onAppendShot: (shot: Shot) => void;
  onBuildFromShots: () => void;
  buildResult: { kept: number; dropped: number } | null;
}): ReactElement {
  const [peaks, setPeaks] = useState<number[] | null>(null);
  const [posterUrl, setPosterUrl] = useState<string | null>(null);

  const waveformReady = hasFile(asset, "waveform");
  const posterReady = hasFile(asset, "poster");

  useEffect(() => {
    let cancelled = false;
    const created: string[] = [];
    setPeaks(null);
    setPosterUrl(null);
    void (async () => {
      if (waveformReady) {
        try {
          const wf = await client.getWaveform(asset.id);
          if (!cancelled) setPeaks(wf.peaks);
        } catch {
          /* waveform may still be processing */
        }
      }
      if (posterReady) {
        try {
          const url = await client.fileObjectUrl(asset.id, "poster");
          if (cancelled) URL.revokeObjectURL(url);
          else {
            created.push(url);
            setPosterUrl(url);
          }
        } catch {
          /* ignore */
        }
      }
    })();
    return () => {
      cancelled = true;
      created.forEach((u) => URL.revokeObjectURL(u));
    };
  }, [client, asset.id, waveformReady, posterReady]);

  const running = analysis.status === "running";

  return (
    <div className="space-y-4 overflow-auto p-4">
      <div className="flex items-center justify-between">
        <span className="text-xs uppercase tracking-wide text-slate-500">Analyse</span>
        <div className="flex items-center gap-2">
          <label
            className="flex items-center gap-1 text-[10px] text-slate-400"
            title="Sprecher-Diarisierung (pyannote)"
          >
            <input
              type="checkbox"
              checked={analysis.diarize}
              onChange={(e) => analysis.setDiarize(e.target.checked)}
              disabled={running}
              className="accent-sky-500"
            />
            Diarize
          </label>
          <label
            className="flex items-center gap-1 text-[10px] text-slate-400"
            title="Wort-Alignment (WhisperX)"
          >
            <input
              type="checkbox"
              checked={analysis.align}
              onChange={(e) => analysis.setAlign(e.target.checked)}
              disabled={running}
              className="accent-sky-500"
            />
            Align
          </label>
          <button
            type="button"
            onClick={() => void analysis.runAnalysis()}
            disabled={running}
            className="rounded-md bg-ink px-2 py-1 text-xs text-slate-200 transition hover:bg-edge disabled:opacity-40"
          >
            {running ? "Analysiere…" : "Analyse starten"}
          </button>
        </div>
      </div>

      {analysis.error && <div className="text-xs text-red-400">{analysis.error}</div>}

      <div>
        <div className="mb-1 text-xs text-slate-500">Shots ({analysis.shots.length})</div>
        <ShotStrip
          client={client}
          shots={analysis.shots}
          onAppend={canAppend ? onAppendShot : undefined}
        />
        <button
          type="button"
          onClick={onBuildFromShots}
          disabled={analysis.shots.length === 0 || !canAppend}
          className="mt-2 w-full rounded-md bg-sky-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-sky-500 disabled:opacity-40"
          title="Eine Rough-Cut-Sequenz aus den erkannten Szenen bauen (schwache automatisch verworfen)"
        >
          Rough Cut aus Szenen bauen
        </button>
        {buildResult && (
          <div className="mt-1 text-xs text-slate-400">
            {buildResult.kept} Szenen übernommen · {buildResult.dropped} verworfen
          </div>
        )}
      </div>

      <div className="flex items-center gap-4">
        {posterUrl ? (
          <img
            src={posterUrl}
            alt="poster"
            className="h-16 w-28 rounded-md border border-edge object-cover"
          />
        ) : (
          <div className="flex h-16 w-28 items-center justify-center rounded-md border border-edge bg-ink text-xs text-slate-600">
            {asset.type === "video" ? "kein Poster" : "Audio"}
          </div>
        )}
        <div className="min-w-0">
          <div className="truncate font-medium text-slate-100">{asset.display_name}</div>
          <div className="truncate text-xs text-slate-500">{asset.id}</div>
        </div>
      </div>

      <div className="rounded-lg border border-edge bg-ink px-4 py-2">
        <Row label="Typ" value={asset.type} />
        <Row
          label="Auflösung"
          value={asset.width && asset.height ? `${asset.width}×${asset.height}` : "—"}
        />
        <Row label="Frame Rate" value={fmtFps(asset)} />
        <Row label="Dauer" value={fmtDuration(asset)} />
        <Row label="Audio" value={asset.audio_sample_rate ? `${asset.audio_sample_rate} Hz` : "—"} />
        <Row
          label="Codec"
          value={[asset.codec_video, asset.codec_audio].filter(Boolean).join(" / ") || "—"}
        />
        <Row label="Timecode" value={asset.start_timecode ?? "—"} />
      </div>

      <div>
        <div className="mb-1 text-xs uppercase tracking-wide text-slate-500">Waveform</div>
        {peaks ? (
          <Waveform peaks={peaks} />
        ) : (
          <div className="flex h-24 items-center justify-center rounded-md border border-dashed border-edge text-xs text-slate-600">
            {waveformReady ? "lade…" : "wird analysiert…"}
          </div>
        )}
      </div>
    </div>
  );
}
