import type { TimelineAudioClip } from "../api";
import { clipActiveAt, clipGainAt, clipSourceTimeSeconds } from "./audioMix";

/** The slice of HTMLAudioElement the mixer needs; injectable so sync logic is testable. */
export interface MixerAudioEl {
  currentTime: number;
  volume: number;
  paused: boolean;
  src: string;
  play(): Promise<void>;
  pause(): void;
  load(): void;
}

export type AudioElementFactory = (src: string) => MixerAudioEl;

function mediaUrl(assetId: string): string {
  // Audio assets (VO + imported music) expose an `original` file row, not `proxy`.
  // Video assets use `proxy`; the <video> src is set separately in SequencePlayer.
  return `laura-media://media/${assetId}/original`;
}

function defaultFactory(src: string): MixerAudioEl {
  const el = new Audio();
  el.src = src;
  el.preload = "auto";
  el.crossOrigin = "anonymous";
  return el as unknown as MixerAudioEl;
}

/**
 * Plays the timeline A2 clips (VO + music) synced to the video currentFrame, applying
 * gain + fades per clip (clipGainAt) — mirrors the export mix. Ducking of the *video*
 * track is computed by the caller (videoDuckGainAt) and applied to the <video>.volume.
 */
export class AudioMixer {
  static readonly SYNC_DRIFT_SECONDS = 0.08;

  private readonly rateNum: number;
  private readonly rateDen: number;
  private readonly makeEl: AudioElementFactory;
  private clips: TimelineAudioClip[] = [];
  private els = new Map<string, MixerAudioEl>(); // clip.id -> element

  constructor(opts: { rateNum: number; rateDen: number; makeEl?: AudioElementFactory }) {
    this.rateNum = opts.rateNum;
    this.rateDen = opts.rateDen;
    this.makeEl = opts.makeEl ?? defaultFactory;
  }

  setClips(clips: TimelineAudioClip[]): void {
    // Drop elements for clips that are gone.
    const keep = new Set(clips.map((c) => c.id));
    for (const [id, el] of this.els) {
      if (!keep.has(id)) {
        el.pause();
        this.els.delete(id);
      }
    }
    // Create elements for new clips.
    for (const c of clips) {
      if (!this.els.has(c.id)) {
        this.els.set(c.id, this.makeEl(mediaUrl(c.asset_id)));
      }
    }
    this.clips = clips;
  }

  syncTo(seqFrame: number, playing: boolean): void {
    for (const c of this.clips) {
      const el = this.els.get(c.id);
      if (!el) continue;
      const active = clipActiveAt(c, seqFrame);
      if (!active || !playing) {
        if (!el.paused) el.pause();
        continue;
      }
      el.volume = Math.min(1, clipGainAt(c, seqFrame, this.rateNum, this.rateDen));
      const target = clipSourceTimeSeconds(c, seqFrame, this.rateNum, this.rateDen);
      if (Math.abs(el.currentTime - target) > AudioMixer.SYNC_DRIFT_SECONDS) {
        el.currentTime = target;
      }
      if (el.paused) void el.play();
    }
  }

  pauseAll(): void {
    for (const el of this.els.values()) if (!el.paused) el.pause();
  }

  dispose(): void {
    this.pauseAll();
    this.els.clear();
    this.clips = [];
  }
}
