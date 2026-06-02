import { type ReactElement, useEffect, useRef, useState } from "react";

import { type Asset, hasFile, type LauraClient } from "../api";

/**
 * Frame-accurate proxy player (the supplemental WebCodecs/<video> path, ADR-0002).
 * Plays the CFR all-intra proxy so frame stepping and seeking are exact. libmpv
 * remains the planned primary engine for native pro playback.
 */
function fps(asset: Asset): number {
  return asset.rate_num && asset.rate_den ? asset.rate_num / asset.rate_den : 25;
}

export function Player({ client, asset }: { client: LauraClient; asset: Asset }): ReactElement {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [frame, setFrame] = useState(0);

  const f = fps(asset);
  const total = asset.duration_frames ?? 0;
  const proxyReady = hasFile(asset, "proxy");

  useEffect(() => {
    let cancelled = false;
    let created: string | null = null;
    setUrl(null);
    setFrame(0);
    setPlaying(false);
    if (proxyReady) {
      void client
        .fileObjectUrl(asset.id, "proxy")
        .then((u) => {
          if (cancelled) {
            URL.revokeObjectURL(u);
          } else {
            created = u;
            setUrl(u);
          }
        })
        .catch(() => undefined);
    }
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [client, asset.id, proxyReady]);

  function seekToFrame(target: number): void {
    const v = videoRef.current;
    if (!v) return;
    v.currentTime = Math.max(0, target) / f;
  }

  function step(delta: number): void {
    if (videoRef.current && !videoRef.current.paused) videoRef.current.pause();
    seekToFrame(frame + delta);
  }

  function toggle(): void {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) {
      void v.play();
    } else {
      v.pause();
    }
  }

  return (
    <div className="space-y-2">
      <div className="overflow-hidden rounded-md border border-edge bg-black">
        {url ? (
          <video
            ref={videoRef}
            src={url}
            className="aspect-video w-full"
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onTimeUpdate={(e) => setFrame(Math.round(e.currentTarget.currentTime * f))}
          />
        ) : (
          <div className="flex aspect-video w-full items-center justify-center text-xs text-slate-600">
            {proxyReady ? "lade Proxy…" : "Proxy wird erstellt…"}
          </div>
        )}
      </div>

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => step(-1)}
          disabled={!url}
          className="rounded bg-panel px-2 py-1 text-xs text-slate-200 hover:bg-edge disabled:opacity-40"
        >
          ◀ Frame
        </button>
        <button
          type="button"
          onClick={toggle}
          disabled={!url}
          className="rounded bg-sky-600 px-3 py-1 text-xs font-medium text-white hover:bg-sky-500 disabled:opacity-40"
        >
          {playing ? "❚❚" : "▶"}
        </button>
        <button
          type="button"
          onClick={() => step(1)}
          disabled={!url}
          className="rounded bg-panel px-2 py-1 text-xs text-slate-200 hover:bg-edge disabled:opacity-40"
        >
          Frame ▶
        </button>
        <input
          type="range"
          min={0}
          max={Math.max(0, total - 1)}
          value={Math.min(frame, Math.max(0, total - 1))}
          onChange={(e) => seekToFrame(Number(e.target.value))}
          disabled={!url || total === 0}
          className="flex-1 accent-sky-500"
        />
        <span className="w-28 shrink-0 text-right text-xs tabular-nums text-slate-400">
          {frame}
          {total ? ` / ${total}` : ""} f
        </span>
      </div>
    </div>
  );
}
