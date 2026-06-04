import { type ReactElement, useEffect, useState } from "react";

import { type Asset, type LauraClient, type TimelineClip, type Waveform as WaveformData } from "../api";
import { Waveform } from "./Waveform";

/** How many frames either side of the cut the filmstrip shows (7 thumbnails total). */
const STRIP_RADIUS = 3;

/** A single source-frame thumbnail. Mirrors ClipThumb's object-URL lifecycle in
 *  TimelineBar.tsx: fetch into a blob URL, revoke on cleanup / asset change. */
function Frame({
  client,
  assetId,
  frame,
  isCut,
  onSeek,
}: {
  client: LauraClient;
  assetId: string;
  frame: number;
  isCut: boolean;
  onSeek: (frame: number) => void;
}): ReactElement {
  const [url, setUrl] = useState<string | null>(null);
  const safeFrame = Math.max(0, frame);

  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    client
      .assetFrameUrl(assetId, safeFrame)
      .then((u) => {
        if (!active) {
          URL.revokeObjectURL(u);
          return;
        }
        objectUrl = u;
        setUrl(u);
      })
      .catch(() => {
        /* fall back to the colour block */
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [client, assetId, safeFrame]);

  return (
    <button
      type="button"
      onClick={() => onSeek(safeFrame)}
      title={`Frame ${safeFrame}`}
      className={`relative flex aspect-video min-w-0 flex-1 items-end justify-center overflow-hidden rounded-sm ${
        url ? "bg-ink" : "bg-edge"
      } ${isCut ? "z-10 ring-2 ring-inset ring-amber-400" : "hover:brightness-125"}`}
    >
      {url && <img src={url} alt="" className="absolute inset-0 h-full w-full object-cover" />}
      <span className="relative mb-0.5 rounded bg-ink/70 px-1 text-[9px] leading-tight tabular-nums text-slate-200">
        {safeFrame}
      </span>
    </button>
  );
}

/** A 7-thumbnail row centered on `center`, with the center frame ring-highlighted. */
function Filmstrip({
  client,
  assetId,
  center,
  onSeek,
}: {
  client: LauraClient;
  assetId: string;
  center: number;
  onSeek: (frame: number) => void;
}): ReactElement {
  const offsets: number[] = [];
  for (let k = -STRIP_RADIUS; k <= STRIP_RADIUS; k++) offsets.push(k);
  return (
    <div className="flex gap-px">
      {offsets.map((k) => (
        <Frame
          key={k}
          client={client}
          assetId={assetId}
          frame={center + k}
          isCut={k === 0}
          onSeek={onSeek}
        />
      ))}
    </div>
  );
}

/** IN/OUT nudge buttons: −1, −fps, +fps, +1. */
function Nudges({
  fps,
  onNudge,
  label,
}: {
  fps: number;
  onNudge: (delta: number) => void;
  /** "IN" or "OUT" — used for stable button titles the test relies on. */
  label: "IN" | "OUT";
}): ReactElement {
  const steps: { delta: number; text: string }[] = [
    { delta: -1, text: "◀1" },
    { delta: -fps, text: `◀${fps}` },
    { delta: fps, text: `▶${fps}` },
    { delta: 1, text: "▶1" },
  ];
  return (
    <div className="flex gap-1">
      {steps.map((s) => (
        <button
          key={s.delta}
          type="button"
          onClick={() => onNudge(s.delta)}
          title={`${label} ${s.delta > 0 ? "+" : ""}${s.delta} Frame${Math.abs(s.delta) === 1 ? "" : "s"}`}
          className="flex-1 rounded bg-ink px-2 py-1 text-xs tabular-nums text-slate-200 transition hover:bg-edge"
        >
          {s.text}
        </button>
      ))}
    </div>
  );
}

/**
 * Frame-accurate cut editor for the selected rough-cut clip. Two filmstrips around the
 * IN and OUT cut, frame-precise nudge→trim, and a mini waveform of the clip's SOURCE
 * range. Replaces the InspectorPanel in the right column while a clip is selected (P5).
 */
export function SceneInspector({
  client,
  clip,
  asset,
  timelineId,
  onChange,
  onSeek,
}: {
  client: LauraClient;
  clip: TimelineClip;
  asset: Asset;
  timelineId: string;
  onChange: () => void;
  onSeek: (frame: number) => void;
}): ReactElement {
  const [wf, setWf] = useState<WaveformData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fps = Math.round((asset.rate_num ?? 25) / (asset.rate_den ?? 1));
  const durFrames = clip.src_out_frame_exclusive - clip.src_in_frame;
  const maxOut = asset.duration_frames ?? Number.MAX_SAFE_INTEGER;

  useEffect(() => {
    let active = true;
    setWf(null);
    client
      .getWaveform(asset.id)
      .then((w) => {
        if (active) setWf(w);
      })
      .catch(() => {
        /* no waveform yet — placeholders stay */
      });
    return () => {
      active = false;
    };
  }, [client, asset.id]);

  async function nudge(
    newIn: number,
    newOut: number,
    editedFrame: number,
  ): Promise<void> {
    setError(null);
    try {
      await client.applyOperation(timelineId, {
        op: "trim",
        at_seq_frame: clip.seq_in_frame,
        new_src_in_frame: newIn,
        new_src_out_frame_exclusive: newOut,
      });
      onChange();
      onSeek(editedFrame);
    } catch (e) {
      setError(String(e));
    }
  }

  function nudgeIn(delta: number): void {
    const newIn = Math.min(
      clip.src_out_frame_exclusive - 1,
      Math.max(0, clip.src_in_frame + delta),
    );
    if (newIn === clip.src_in_frame) return;
    void nudge(newIn, clip.src_out_frame_exclusive, newIn);
  }

  function nudgeOut(delta: number): void {
    const newOut = Math.min(
      maxOut,
      Math.max(clip.src_in_frame + 1, clip.src_out_frame_exclusive + delta),
    );
    if (newOut === clip.src_out_frame_exclusive) return;
    void nudge(clip.src_in_frame, newOut, newOut - 1);
  }

  // Slice the waveform to the clip's source range using the WAVEFORM's own rate
  // (sample_rate / samples_per_pixel), projected via the asset's frame rate.
  let slice: number[] | null = null;
  if (wf) {
    const secOf = (f: number): number => (f * (asset.rate_den ?? 1)) / (asset.rate_num ?? 1);
    const peakIdx = (f: number): number =>
      Math.floor((secOf(f) * wf.sample_rate) / wf.samples_per_pixel);
    const a = peakIdx(clip.src_in_frame);
    slice = wf.peaks.slice(a, Math.max(a + 1, peakIdx(clip.src_out_frame_exclusive)));
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto p-4">
      <div>
        <h2 className="text-xs font-medium uppercase tracking-wide text-slate-500">Szene</h2>
        <div className="mt-1 text-sm text-slate-200">
          Quelle{" "}
          <span className="tabular-nums">
            {clip.src_in_frame}–{clip.src_out_frame_exclusive}
          </span>
        </div>
        <div className="text-xs text-slate-500">
          {durFrames} frame{durFrames === 1 ? "" : "s"} · {fps} fps
        </div>
      </div>

      {error && <div className="text-xs text-red-400">{error}</div>}

      <section className="space-y-1.5">
        <h3 className="text-[11px] font-medium uppercase tracking-wide text-slate-500">
          IN-Schnitt
        </h3>
        <Filmstrip client={client} assetId={asset.id} center={clip.src_in_frame} onSeek={onSeek} />
        <Nudges fps={fps} label="IN" onNudge={nudgeIn} />
      </section>

      <section className="space-y-1.5">
        <h3 className="text-[11px] font-medium uppercase tracking-wide text-slate-500">
          OUT-Schnitt
        </h3>
        <Filmstrip
          client={client}
          assetId={asset.id}
          center={clip.src_out_frame_exclusive}
          onSeek={onSeek}
        />
        <Nudges fps={fps} label="OUT" onNudge={nudgeOut} />
      </section>

      <section className="space-y-1.5">
        <h3 className="text-[11px] font-medium uppercase tracking-wide text-slate-500">
          Audio-Fenster
        </h3>
        {slice ? (
          <Waveform peaks={slice} />
        ) : (
          <div className="flex h-24 items-center justify-center rounded-md bg-ink text-xs text-slate-600">
            Lade Waveform…
          </div>
        )}
      </section>
    </div>
  );
}
