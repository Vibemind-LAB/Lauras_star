import {
  type ReactElement,
  useEffect,
  useRef,
  useState,
} from "react";

import { type Asset, type TimelineAudioClip, type TimelineClip, hasFile } from "../api";
import type { LauraClient } from "../api";
import { AudioMixer } from "../shared/AudioMixer";
import { videoDuckGainAt } from "../shared/audioMix";

/** Frame-rate of an asset — matches the same helper in Player.tsx. */
function fps(asset: Asset): number {
  return asset.rate_num && asset.rate_den ? asset.rate_num / asset.rate_den : 25;
}

// ---------------------------------------------------------------------------
// Pure helpers — exported so they can be unit-tested without mounting React.
// ---------------------------------------------------------------------------

/**
 * Total sequence length in frames: the largest `seq_out_frame_exclusive`
 * across all clips, or 0 when the clip list is empty.
 */
export function totalFrames(clips: TimelineClip[]): number {
  if (clips.length === 0) return 0;
  let max = 0;
  for (const c of clips) {
    if (c.seq_out_frame_exclusive > max) max = c.seq_out_frame_exclusive;
  }
  return max;
}

/**
 * Index of the clip that contains `frame` in the sequence timeline.
 * Returns the index where `seq_in_frame <= frame < seq_out_frame_exclusive`.
 * When no clip matches (before the first or past the last) the result is
 * clamped to the last valid index (or 0 for an empty list).
 */
export function clipIndexAtSeqFrame(clips: TimelineClip[], frame: number): number {
  if (clips.length === 0) return 0;
  for (let i = 0; i < clips.length; i++) {
    if (frame >= clips[i].seq_in_frame && frame < clips[i].seq_out_frame_exclusive) {
      return i;
    }
  }
  // Clamp to last clip.
  return clips.length - 1;
}

/**
 * Video-track volume (0..1) at `frame` given the A2 overlay clips. Pure wrapper over
 * videoDuckGainAt so the ducking rule is unit-testable without mounting the player.
 */
export function videoVolumeForFrame(
  audioClips: TimelineAudioClip[] | undefined,
  frame: number,
): number {
  return videoDuckGainAt(audioClips ?? [], frame);
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface SequencePlayerProps {
  client: LauraClient;
  projectId: string | null;
  sequenceId: string | null;
  reloadKey?: unknown;
  /**
   * Optional external seek in SEQUENCE frames. When this prop changes to a
   * non-null value the player scrubs to that sequence frame. Mirrors the
   * `seekTo` pattern in Player.tsx (object identity change triggers the effect;
   * pass a new `{ frame }` object each time to re-seek). AssembleView does not
   * pass this prop — default behaviour is unchanged.
   */
  seekTo?: { frame: number } | null;
  onFrame?: (seqFrame: number) => void;
  /**
   * Already-resolved clips to play, bypassing `getSequenceFlattened`. Required for non-sequence
   * timelines (e.g. a materialized SCENE timeline in Feinschnitt): `/sequences/{id}/flattened`
   * only resolves kind="sequence" timelines and returns [] for a scene, so without this the player
   * would show no video. AssembleView omits it and keeps fetching the flattened sequence.
   */
  clipsOverride?: TimelineClip[];
  /**
   * VO + music timeline audio clips to play synced to the video playhead (Phase B).
   * When omitted the player is video-only (unchanged). Mirrors the export mix:
   * gain/fades per clip, the video track ducks under overlapping VO spans.
   */
  audioClips?: TimelineAudioClip[];
  /** Sequence frame rate for frame->time mapping (defaults 30/1, matching AssembleView). */
  rateNum?: number;
  rateDen?: number;
}

export function SequencePlayer({
  client,
  projectId,
  sequenceId,
  reloadKey,
  seekTo,
  onFrame,
  clipsOverride,
  audioClips,
  rateNum = 30,
  rateDen = 1,
}: SequencePlayerProps): ReactElement {
  const videoRef = useRef<HTMLVideoElement | null>(null);

  const [clips, setClips] = useState<TimelineClip[]>([]);
  const [assetsById, setAssetsById] = useState<Map<string, Asset>>(new Map());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Playback state.
  const [playing, setPlaying] = useState(false);
  const [seqFrame, setSeqFrame] = useState(0);

  // ---------------------------------------------------------------------------
  // AudioMixer — created exactly once on mount; disposed on unmount.
  // React 18 Strict Mode runs the render body twice, so we must NOT
  // instantiate inside the render body (the first instance would be orphaned).
  // The empty-dep effect runs once; initial rateNum/rateDen are captured at
  // mount time (project rate is fixed for the lifetime of this mount).
  // ---------------------------------------------------------------------------
  const mixerRef = useRef<AudioMixer | null>(null);
  useEffect(() => {
    mixerRef.current = new AudioMixer({ rateNum, rateDen });
    mixerRef.current.setClips(audioClips ?? []);
    return () => {
      mixerRef.current?.dispose();
      mixerRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // Keep the mixer's clip set in sync with the prop on subsequent changes.
  useEffect(() => {
    mixerRef.current?.setClips(audioClips ?? []);
  }, [audioClips]);

  // The index of the clip currently loaded in the <video>.
  const clipIndexRef = useRef<number>(0);
  // Intra-clip offset (in sequence frames) to seek to when the next loadeddata fires.
  // Stores the target src-file time in seconds, not a seq-frame.
  const pendingSrcTime = useRef<number | null>(null);
  // Whether playback should resume after the upcoming loadeddata seek.
  const shouldPlayAfterLoad = useRef<boolean>(false);

  // ---------------------------------------------------------------------------
  // Fetch clips + assets when (sequenceId, reloadKey) changes.
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!sequenceId || !projectId) {
      setClips([]);
      setAssetsById(new Map());
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    void (async () => {
      try {
        // clipsOverride (e.g. a materialized scene timeline) plays its clips directly; the
        // flatten endpoint only resolves kind="sequence" timelines and would return [].
        const [fetchedClips, fetchedAssets] = await Promise.all([
          clipsOverride ?? client.getSequenceFlattened(sequenceId),
          client.listAssets(projectId),
        ]);
        if (cancelled) return;
        setClips(fetchedClips);
        const map = new Map<string, Asset>();
        for (const a of fetchedAssets) map.set(a.id, a);
        setAssetsById(map);
        setSeqFrame(0);
        clipIndexRef.current = 0;
        // Reset video element.
        const v = videoRef.current;
        if (v) {
          v.pause();
          v.src = "";
        }
        setPlaying(false);
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sequenceId, reloadKey]);

  // ---------------------------------------------------------------------------
  // Helper: load clip at index `i` into the video element.
  // `srcTime` = where in the source file to seek (seconds).
  // `startPlaying` = resume playback after loadeddata.
  // ---------------------------------------------------------------------------
  function loadClip(i: number, srcTime: number, startPlaying: boolean): void {
    const v = videoRef.current;
    if (!v || i < 0 || i >= clips.length) return;
    clipIndexRef.current = i;
    pendingSrcTime.current = srcTime;
    shouldPlayAfterLoad.current = startPlaying;
    // best-effort: brief gap on source switch
    v.pause();
    v.src = `laura-media://media/${clips[i].asset_id}/proxy`;
    v.load();
  }

  // ---------------------------------------------------------------------------
  // Audio sync helper
  // ---------------------------------------------------------------------------
  function applyAudio(frame: number, isPlaying: boolean): void {
    const v = videoRef.current;
    if (v) v.volume = videoVolumeForFrame(audioClips, frame);
    mixerRef.current?.syncTo(frame, isPlaying);
  }

  // ---------------------------------------------------------------------------
  // Controls
  // ---------------------------------------------------------------------------
  function toggle(): void {
    const v = videoRef.current;
    if (!v || clips.length === 0) return;
    if (v.paused) {
      // If nothing loaded yet, load the first clip.
      if (!v.src || v.src === window.location.href) {
        loadClip(0, clips[0].src_in_frame / assetFps(0), true);
      } else {
        void v.play();
      }
    } else {
      v.pause();
    }
  }

  function assetFps(clipIndex: number): number {
    const clip = clips[clipIndex];
    if (!clip) return 25;
    const asset = assetsById.get(clip.asset_id);
    return asset ? fps(asset) : 25;
  }

  /** Scrub to a sequence frame (slider or external seek). */
  function seekToSeqFrame(target: number): void {
    if (clips.length === 0) return;
    const i = clipIndexAtSeqFrame(clips, target);
    const clip = clips[i];
    const f = assetFps(i);
    // Intra-clip offset in source frames.
    const intraSrcFrame = clip.src_in_frame + (target - clip.seq_in_frame);
    const srcTime = intraSrcFrame / f;

    const v = videoRef.current;
    if (!v) return;

    const currentSrc = `laura-media://media/${clip.asset_id}/proxy`;
    if (v.src === currentSrc && v.readyState >= 1) {
      // Same asset is already loaded — seek directly.
      clipIndexRef.current = i;
      pendingSrcTime.current = null;
      v.currentTime = srcTime;
      setSeqFrame(target);
      applyAudio(target, false);
    } else {
      // Different asset — load and seek.
      loadClip(i, srcTime, false);
      setSeqFrame(target);
      applyAudio(target, false);
    }
  }

  // ---------------------------------------------------------------------------
  // External seek (sequence frames) — mirrors Player.tsx's seekTo-effect.
  // Only fires when the seekTo object reference changes and is non-null.
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!seekTo) return;
    // Depend on `clips` too: a seekTo that arrives before the async flatten
    // completes would otherwise be dropped (clips still []). Re-running once
    // clips populate re-applies the pending seek. seekToSeqFrame no-ops on empty clips.
    seekToSeqFrame(seekTo.frame);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seekTo, clips]);

  const total = totalFrames(clips);
  const proxyReady = (i: number): boolean => {
    const clip = clips[i];
    if (!clip) return false;
    const asset = assetsById.get(clip.asset_id);
    return asset ? hasFile(asset, "proxy") : false;
  };

  // ---------------------------------------------------------------------------
  // Video event handlers
  // ---------------------------------------------------------------------------
  function handleLoadedData(): void {
    const v = videoRef.current;
    if (!v) return;
    const pending = pendingSrcTime.current;
    if (pending != null) {
      v.currentTime = pending;
      pendingSrcTime.current = null;
    }
    if (shouldPlayAfterLoad.current) {
      void v.play();
    }
  }

  function handleTimeUpdate(): void {
    const v = videoRef.current;
    if (!v || clips.length === 0) return;
    const i = clipIndexRef.current;
    const clip = clips[i];
    if (!clip) return;
    const f = assetFps(i);
    const currentSrcTime = v.currentTime;
    const clipEndSrcTime = clip.src_out_frame_exclusive / f;

    if (currentSrcTime >= clipEndSrcTime) {
      // Clip boundary reached — advance to the next clip.
      const nextI = i + 1;
      if (nextI >= clips.length) {
        // End of sequence.
        v.pause();
        setPlaying(false);
        mixerRef.current?.pauseAll();
        const finalFrame = total > 0 ? total - 1 : 0;
        setSeqFrame(finalFrame);
        onFrame?.(finalFrame);
        return;
      }
      const nextClip = clips[nextI];
      const nextFps = assetFps(nextI);
      loadClip(nextI, nextClip.src_in_frame / nextFps, true);
      return;
    }

    // Still inside current clip — report seq frame.
    const srcFrame = Math.round(currentSrcTime * f);
    const intraSrc = srcFrame - clip.src_in_frame;
    const sf = clip.seq_in_frame + Math.max(0, intraSrc);
    setSeqFrame(sf);
    onFrame?.(sf);
    applyAudio(sf, !v.paused);
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------
  const firstClipHasProxy = clips.length > 0 && proxyReady(0);

  return (
    <div className="space-y-2 rounded-md">
      <div className="overflow-hidden rounded-md border border-bezel bg-black">
        {loading ? (
          <div className="flex aspect-video w-full items-center justify-center text-xs text-content-faint">
            Lade Sequenz…
          </div>
        ) : error ? (
          <div className="flex aspect-video w-full items-center justify-center px-6 text-center text-xs text-red-400">
            {error}
          </div>
        ) : clips.length === 0 ? (
          <div className="flex aspect-video w-full items-center justify-center text-xs text-content-faint">
            Noch keine Sequenz — Szenen hinzufügen
          </div>
        ) : !firstClipHasProxy ? (
          <div className="flex aspect-video w-full items-center justify-center text-xs text-content-faint">
            Proxy wird erstellt…
          </div>
        ) : (
          <video
            ref={videoRef}
            className="aspect-video w-full"
            onPlay={() => { setPlaying(true); applyAudio(seqFrame, true); }}
            onPause={() => { setPlaying(false); mixerRef.current?.pauseAll(); }}
            onLoadedData={handleLoadedData}
            onTimeUpdate={handleTimeUpdate}
            onError={() => {
              const err = videoRef.current?.error;
              setError(`Video-Fehler ${err?.code ?? "?"}: ${err?.message ?? "unbekannt"}`);
            }}
          />
        )}
      </div>

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={toggle}
          disabled={clips.length === 0 || !firstClipHasProxy}
          className="rounded bg-sky-600 px-3 py-1 text-xs font-medium text-white hover:bg-sky-500 disabled:opacity-40"
        >
          {playing ? "❚❚" : "▶"}
        </button>
        <input
          type="range"
          min={0}
          max={Math.max(0, total - 1)}
          value={Math.min(seqFrame, Math.max(0, total - 1))}
          onChange={(e) => seekToSeqFrame(Number(e.target.value))}
          disabled={clips.length === 0 || total === 0}
          className="flex-1 accent-sky-500"
        />
        <span className="w-36 shrink-0 text-right text-xs tabular-nums text-content-muted">
          {seqFrame} / {total} f
        </span>
      </div>
    </div>
  );
}


