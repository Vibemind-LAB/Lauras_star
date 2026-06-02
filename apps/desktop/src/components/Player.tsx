import {
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
  useEffect,
  useRef,
  useState,
} from "react";

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
  const reverseTimer = useRef<number | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [frame, setFrame] = useState(0);
  const [shuttle, setShuttle] = useState(0); // signed playback rate: + forward, - reverse

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
      if (reverseTimer.current !== null) {
        window.clearInterval(reverseTimer.current);
        reverseTimer.current = null;
      }
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

  function clearReverse(): void {
    if (reverseTimer.current !== null) {
      window.clearInterval(reverseTimer.current);
      reverseTimer.current = null;
    }
  }

  function nextRate(rate: number): number {
    return rate >= 2 ? 4 : 2; // 1x -> 2x -> 4x, capped at 4x
  }

  function stopShuttle(): void {
    clearReverse();
    const v = videoRef.current;
    if (v) {
      v.pause();
      v.playbackRate = 1;
    }
    setShuttle(0);
  }

  function shuttleForward(): void {
    clearReverse();
    const v = videoRef.current;
    if (!v) return;
    const rate = shuttle > 0 ? nextRate(shuttle) : 1;
    v.playbackRate = rate;
    void v.play();
    setShuttle(rate);
  }

  function shuttleReverse(): void {
    const v = videoRef.current;
    if (!v) return;
    v.pause();
    v.playbackRate = 1;
    const rate = shuttle < 0 ? nextRate(-shuttle) : 1;
    clearReverse();
    // <video> has no native reverse: step currentTime back once per frame interval.
    reverseTimer.current = window.setInterval(() => {
      const vid = videoRef.current;
      if (!vid) return;
      if (vid.currentTime <= 0) {
        clearReverse();
        setShuttle(0);
        return;
      }
      vid.currentTime = Math.max(0, vid.currentTime - rate / f);
    }, Math.max(16, 1000 / f));
    setShuttle(-rate);
  }

  function onKeyDown(e: ReactKeyboardEvent<HTMLDivElement>): void {
    switch (e.key) {
      case "j":
      case "J":
        e.preventDefault();
        shuttleReverse();
        break;
      case "k":
      case "K":
        e.preventDefault();
        stopShuttle();
        break;
      case "l":
      case "L":
        e.preventDefault();
        shuttleForward();
        break;
      case " ":
        e.preventDefault();
        stopShuttle();
        toggle();
        break;
      case "ArrowLeft":
        e.preventDefault();
        stopShuttle();
        step(e.shiftKey ? -Math.round(f) : -1);
        break;
      case "ArrowRight":
        e.preventDefault();
        stopShuttle();
        step(e.shiftKey ? Math.round(f) : 1);
        break;
      case "Home":
        e.preventDefault();
        stopShuttle();
        seekToFrame(0);
        break;
      case "End":
        e.preventDefault();
        stopShuttle();
        seekToFrame(Math.max(0, total - 1));
        break;
      default:
        break;
    }
  }

  return (
    <div
      tabIndex={0}
      onKeyDown={onKeyDown}
      className="space-y-2 rounded-md outline-none focus:ring-1 focus:ring-sky-600/50"
    >
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
        <span className="w-36 shrink-0 text-right text-xs tabular-nums text-slate-400">
          {shuttle !== 0 && (
            <span className="mr-1 text-amber-400">
              {shuttle > 0 ? `▶▶${shuttle}×` : `◀◀${-shuttle}×`}
            </span>
          )}
          {frame}
          {total ? ` / ${total}` : ""} f
        </span>
      </div>
      <div className="text-[10px] text-slate-600">
        J/K/L Shuttle · ←/→ Frame · Shift+←/→ Sekunde · Home/End · Leertaste
      </div>
    </div>
  );
}
