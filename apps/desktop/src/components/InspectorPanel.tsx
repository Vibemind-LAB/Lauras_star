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
      <span className="text-content-faint">{label}</span>
      <span className="truncate text-right text-content-strong">{value}</span>
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
        <span className="text-xs uppercase tracking-wide text-content-faint">Analyse</span>
        <div className="flex items-center gap-2">
          <label
            className="flex items-center gap-1 text-[10px] text-content-muted"
            title="Sprecher-Diarisierung (pyannote)"
          >
            <input
              type="checkbox"
              checked={analysis.diarize}
              onChange={(e) => analysis.setDiarize(e.target.checked)}
              disabled={running}
              className="accent-accent"
            />
            Diarize
          </label>
          <label
            className="flex items-center gap-1 text-[10px] text-content-muted"
            title="Word alignment (WhisperX)"
          >
            <input
              type="checkbox"
              checked={analysis.align}
              onChange={(e) => analysis.setAlign(e.target.checked)}
              disabled={running}
              className="accent-accent"
            />
            Align
          </label>
          <select
            value={analysis.detector}
            onChange={(e) => analysis.setDetector(e.target.value)}
            disabled={running}
            title="Scene detector (Hybrid fuses Adaptive + TransNetV2 by confidence; TransNetV2 uses the optional ML model, otherwise it falls back to Adaptive)"
            className="rounded border border-bezel bg-surface-0 px-1 py-0.5 text-[10px] text-content-muted disabled:opacity-40"
          >
            <option value="hybrid">Hybrid (Adaptive + TransNetV2) — empfohlen</option>
            <option value="adaptive">Adaptive</option>
            <option value="content">Content</option>
            <option value="histogram">Histogram</option>
            <option value="transnet">TransNetV2</option>
          </select>
          <button
            type="button"
            onClick={() => void analysis.runAnalysis()}
            disabled={running}
            className="rounded-md bg-surface-0 px-2 py-1 text-xs text-content-strong transition hover:bg-surface-2 disabled:opacity-40"
          >
            {running ? "Analysiere…" : "Analyse starten"}
          </button>
        </div>
      </div>

      {analysis.error && <div className="text-xs text-status-err">{analysis.error}</div>}

      <div>
        {(() => {
          const droppedCount = analysis.shots.filter((s) => s.keep === false).length;
          return (
            <div className="mb-1 text-xs text-content-faint">`n              Shots ({analysis.shots.length}
              {droppedCount > 0 ? ` · ${droppedCount} verworfen` : ""})
            </div>
          );
        })()}
        <ShotStrip
          client={client}
          shots={analysis.shots}
          onAppend={canAppend ? onAppendShot : undefined}
        />
        <button
          type="button"
          onClick={onBuildFromShots}
          disabled={analysis.shots.length === 0 || !canAppend}
          className="mt-2 w-full rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:bg-accent-glow disabled:opacity-40"
          title="Build a rough-cut sequence from the detected scenes (weak ones dropped automatically)"
        >
          Build a rough cut from the scenes
        </button>
        {buildResult && (
          <div className="mt-1 text-xs text-content-muted">`n            {buildResult.kept} scenes kept · {buildResult.dropped} dropped
          </div>
        )}
      </div>

      <div className="flex items-center gap-4">
        {posterUrl ? (
          <img
            src={posterUrl}
            alt="poster"
            className="h-16 w-28 rounded-md border border-bezel object-cover"
          />
        ) : (
          <div className="flex h-16 w-28 items-center justify-center rounded-md border border-bezel bg-surface-0 text-xs text-content-faint">
            {asset.type === "video" ? "no poster" : "Audio"}
          </div>
        )}
        <div className="min-w-0">
          <div className="truncate font-medium text-content-strong">{asset.display_name}</div>
          <div className="truncate text-xs text-content-faint">{asset.id}</div>
        </div>
      </div>

      <div className="rounded-lg border border-bezel bg-surface-0 px-4 py-2">
        <Row label="Typ" value={asset.type} />
        <Row
          label="Resolution"
          value={asset.width && asset.height ? `${asset.width}×${asset.height}` : "—"}
        />
        <Row label="Frame Rate" value={fmtFps(asset)} />
        <Row label="Duration" value={fmtDuration(asset)} />
        <Row label="Audio" value={asset.audio_sample_rate ? `${asset.audio_sample_rate} Hz` : "—"} />
        <Row
          label="Codec"
          value={[asset.codec_video, asset.codec_audio].filter(Boolean).join(" / ") || "—"}
        />
        <Row label="Timecode" value={asset.start_timecode ?? "—"} />
      </div>

      <div>
        <div className="mb-1 text-xs uppercase tracking-wide text-content-faint">Waveform</div>
        {peaks ? (
          <Waveform peaks={peaks} />
        ) : (
          <div className="flex h-24 items-center justify-center rounded-md border border-dashed border-bezel text-xs text-content-faint">
            {waveformReady ? "lade…" : "analysing…"}
          </div>
        )}
      </div>
    </div>
  );
}

