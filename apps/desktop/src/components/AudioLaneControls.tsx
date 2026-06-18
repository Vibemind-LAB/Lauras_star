import { type ReactElement, useEffect, useMemo, useState } from "react";

import { type Asset, type AudioMixMode, type LauraClient, type TimelineAudioClip } from "../api";
import { log } from "../shared/log";

export function AudioLaneControls({
  client,
  timelineId,
  assets,
  onChange,
}: {
  client: LauraClient;
  timelineId: string | null;
  assets: Asset[];
  onChange: () => void;
}): ReactElement {
  const audioAssets = useMemo(
    () => assets.filter((asset) => asset.type === "audio" || asset.codec_audio !== null),
    [assets],
  );
  const [clips, setClips] = useState<TimelineAudioClip[]>([]);
  const [assetId, setAssetId] = useState(audioAssets[0]?.id ?? "");
  const [seqIn, setSeqIn] = useState(0);
  const [seqOut, setSeqOut] = useState(30);
  const [assetIn, setAssetIn] = useState(0);
  const [gain, setGain] = useState(100);
  const [fadeIn, setFadeIn] = useState(0);
  const [fadeOut, setFadeOut] = useState(0);
  const [mixMode, setMixMode] = useState<AudioMixMode>("mix");
  const [ducking, setDucking] = useState(100);
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!assetId && audioAssets.length > 0) setAssetId(audioAssets[0].id);
  }, [assetId, audioAssets]);

  async function reload(): Promise<void> {
    if (!timelineId) {
      setClips([]);
      return;
    }
    try {
      setClips(await client.listTimelineAudioClips(timelineId));
      setError(null);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      log.error("listTimelineAudioClips failed:", msg);
      setError(msg);
    }
  }

  useEffect(() => {
    void reload();
  }, [client, timelineId]);

  async function submit(): Promise<void> {
    if (!timelineId) {
      setError("Keine Timeline ausgewählt.");
      return;
    }
    if (!assetId) {
      setError("Kein Audio-Asset verfügbar.");
      return;
    }
    if (seqOut <= seqIn) {
      setError("seq out muss größer als seq in sein.");
      return;
    }
    if (fadeIn + fadeOut > seqOut - seqIn) {
      setError("Fades dürfen nicht länger als der Clip sein.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await client.createTimelineAudioClip(timelineId, {
        assetId,
        seqIn,
        seqOut,
        assetIn,
        gainPercent: gain,
        fadeInFrames: fadeIn,
        fadeOutFrames: fadeOut,
        mixMode,
        duckingPercent: ducking,
        label: label.trim() === "" ? null : label.trim(),
      });
      await reload();
      onChange();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      log.error("createTimelineAudioClip failed:", msg);
      setError(msg);
    } finally {
      setBusy(false);
    }
  }

  async function remove(clipId: string): Promise<void> {
    if (!timelineId) return;
    setBusy(true);
    setError(null);
    try {
      await client.deleteTimelineAudioClip(timelineId, clipId);
      await reload();
      onChange();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      log.error("deleteTimelineAudioClip failed:", msg);
      setError(msg);
    } finally {
      setBusy(false);
    }
  }

  const assetName = (id: string): string =>
    audioAssets.find((asset) => asset.id === id)?.display_name ?? "Audio";
  const mixModeLabel = (mode: AudioMixMode): string => {
    if (mode === "replace_original") return "Original ersetzen";
    if (mode === "mute_original") return "Original muten";
    return "Mix";
  };

  return (
    <section className="flex flex-col gap-3 rounded border border-edge bg-panel/50 p-3">
      <div>
        <div className="text-xs font-semibold text-slate-200">Audio-Lane</div>
        <div className="text-[11px] text-slate-600">Musik oder Voiceover auf A2 platzieren</div>
      </div>
      {error !== null && (
        <div className="rounded border border-red-900/70 bg-red-950/20 p-2 text-xs text-red-200">
          {error}
        </div>
      )}
      <div className="grid grid-cols-2 gap-2 text-xs">
        <label className="col-span-2 flex flex-col gap-1 text-slate-400">
          Audio
          <select
            value={assetId}
            onChange={(e) => setAssetId(e.target.value)}
            disabled={busy || audioAssets.length === 0}
            className="rounded border border-edge bg-ink px-2 py-1 text-slate-200 disabled:opacity-50"
          >
            {audioAssets.length === 0 ? (
              <option value="">Keine Audio-Assets</option>
            ) : (
              audioAssets.map((asset) => (
                <option key={asset.id} value={asset.id}>
                  {asset.display_name}
                </option>
              ))
            )}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-slate-400">
          Label
          <input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            disabled={busy}
            className="rounded border border-edge bg-ink px-2 py-1 text-slate-200 disabled:opacity-50"
          />
        </label>
        <label className="flex flex-col gap-1 text-slate-400">
          Audio gain
          <input
            aria-label="Audio gain"
            type="number"
            min={0}
            max={400}
            step={1}
            value={gain}
            onChange={(e) => setGain(Math.trunc(Number(e.target.value)) || 0)}
            disabled={busy}
            className="rounded border border-edge bg-ink px-2 py-1 tabular-nums text-slate-200 disabled:opacity-50"
          />
        </label>
        <label className="flex flex-col gap-1 text-slate-400">
          Modus
          <select
            value={mixMode}
            onChange={(e) => setMixMode(e.target.value as AudioMixMode)}
            disabled={busy}
            className="rounded border border-edge bg-ink px-2 py-1 text-slate-200 disabled:opacity-50"
          >
            <option value="mix">Mix</option>
            <option value="replace_original">Original ersetzen</option>
            <option value="mute_original">Original muten</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-slate-400">
          Original ducking %
          <input
            aria-label="Original ducking"
            type="number"
            min={0}
            max={100}
            step={1}
            value={ducking}
            onChange={(e) => setDucking(Math.max(0, Math.min(100, Math.trunc(Number(e.target.value)) || 0)))}
            disabled={busy}
            className="rounded border border-edge bg-ink px-2 py-1 tabular-nums text-slate-200 disabled:opacity-50"
          />
        </label>
        <label className="flex flex-col gap-1 text-slate-400">
          seq in
          <input
            aria-label="Audio seq in"
            type="number"
            min={0}
            step={1}
            value={seqIn}
            onChange={(e) => setSeqIn(Math.trunc(Number(e.target.value)) || 0)}
            disabled={busy}
            className="rounded border border-edge bg-ink px-2 py-1 tabular-nums text-slate-200 disabled:opacity-50"
          />
        </label>
        <label className="flex flex-col gap-1 text-slate-400">
          seq out
          <input
            aria-label="Audio seq out"
            type="number"
            min={0}
            step={1}
            value={seqOut}
            onChange={(e) => setSeqOut(Math.trunc(Number(e.target.value)) || 0)}
            disabled={busy}
            className="rounded border border-edge bg-ink px-2 py-1 tabular-nums text-slate-200 disabled:opacity-50"
          />
        </label>
        <label className="flex flex-col gap-1 text-slate-400">
          asset in
          <input
            type="number"
            min={0}
            step={1}
            value={assetIn}
            onChange={(e) => setAssetIn(Math.trunc(Number(e.target.value)) || 0)}
            disabled={busy}
            className="rounded border border-edge bg-ink px-2 py-1 tabular-nums text-slate-200 disabled:opacity-50"
          />
        </label>
        <label className="flex flex-col gap-1 text-slate-400">
          fade in
          <input
            type="number"
            min={0}
            step={1}
            value={fadeIn}
            onChange={(e) => setFadeIn(Math.trunc(Number(e.target.value)) || 0)}
            disabled={busy}
            className="rounded border border-edge bg-ink px-2 py-1 tabular-nums text-slate-200 disabled:opacity-50"
          />
        </label>
        <label className="flex flex-col gap-1 text-slate-400">
          fade out
          <input
            type="number"
            min={0}
            step={1}
            value={fadeOut}
            onChange={(e) => setFadeOut(Math.trunc(Number(e.target.value)) || 0)}
            disabled={busy}
            className="rounded border border-edge bg-ink px-2 py-1 tabular-nums text-slate-200 disabled:opacity-50"
          />
        </label>
      </div>
      <button
        type="button"
        onClick={() => void submit()}
        disabled={busy || !timelineId || audioAssets.length === 0}
        className="self-start rounded bg-emerald-700 px-3 py-1 text-xs font-medium text-white hover:bg-emerald-600 disabled:opacity-40"
      >
        Audio einsetzen
      </button>
      <div className="flex flex-col gap-2">
        {clips.length === 0 ? (
          <div className="text-xs text-slate-600">Noch keine A2-Clips.</div>
        ) : (
          clips.map((clip) => (
            <div
              key={clip.id}
              className="grid grid-cols-[1fr_auto] gap-2 rounded border border-edge bg-ink p-2 text-xs"
            >
              <div className="min-w-0">
                <div className="truncate font-medium text-slate-200">
                  {clip.label ?? assetName(clip.asset_id)}
                </div>
                <div className="tabular-nums text-slate-500">
                  {clip.seq_in_frame}-{clip.seq_out_frame_exclusive} f · {clip.gain_percent}%
                </div>
                <div className="tabular-nums text-slate-600">
                  {mixModeLabel(clip.mix_mode)} · Original {clip.ducking_percent}%
                </div>
              </div>
              <button
                type="button"
                onClick={() => void remove(clip.id)}
                disabled={busy}
                className="self-center rounded bg-slate-700 px-2 py-1 text-[11px] text-slate-200 hover:bg-slate-600 disabled:opacity-40"
              >
                entfernen
              </button>
            </div>
          ))
        )}
      </div>
    </section>
  );
}
