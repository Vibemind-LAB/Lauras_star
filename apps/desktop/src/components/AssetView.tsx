import { type ReactElement, useEffect, useState } from "react";

import { type Asset, hasFile, type LauraClient, type Timeline } from "../api";
import { AnalysisPanel } from "./AnalysisPanel";
import { Player } from "./Player";
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

export function AssetView({
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
          // ignore — waveform may still be processing
        }
      }
      if (posterReady) {
        try {
          const url = await client.fileObjectUrl(asset.id, "poster");
          if (cancelled) {
            URL.revokeObjectURL(url);
          } else {
            created.push(url);
            setPosterUrl(url);
          }
        } catch {
          // ignore
        }
      }
    })();

    return () => {
      cancelled = true;
      created.forEach((u) => URL.revokeObjectURL(u));
    };
  }, [client, asset.id, waveformReady, posterReady]);

  return (
    <div className="space-y-4">
      <Player client={client} asset={asset} />

      <div className="flex items-center gap-4">
        {posterUrl ? (
          <img
            src={posterUrl}
            alt="poster"
            className="h-20 w-32 rounded-md border border-edge object-cover"
          />
        ) : (
          <div className="flex h-20 w-32 items-center justify-center rounded-md border border-edge bg-panel text-xs text-slate-600">
            {asset.type === "video" ? "kein Poster" : "Audio"}
          </div>
        )}
        <div className="min-w-0">
          <div className="truncate font-medium text-slate-100">{asset.display_name}</div>
          <div className="text-xs text-slate-500">{asset.id}</div>
        </div>
      </div>

      <div className="rounded-lg border border-edge bg-panel px-4 py-2">
        <Row label="Typ" value={asset.type} />
        <Row
          label="Auflösung"
          value={asset.width && asset.height ? `${asset.width}×${asset.height}` : "—"}
        />
        <Row label="Frame Rate" value={fmtFps(asset)} />
        <Row label="Dauer" value={fmtDuration(asset)} />
        <Row
          label="Audio"
          value={asset.audio_sample_rate ? `${asset.audio_sample_rate} Hz` : "—"}
        />
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

      <div className="border-t border-edge pt-4">
        <AnalysisPanel
          client={client}
          asset={asset}
          roughCut={roughCut}
          onTimelineChange={onTimelineChange}
        />
      </div>
    </div>
  );
}
