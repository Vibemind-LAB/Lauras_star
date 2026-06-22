import { type ReactElement, useMemo } from "react";

import { type Asset, type LauraClient, type Segment, type Timeline } from "../api";
import { useScenes } from "../hooks/useScenes";
import { useRoughCutTranscript } from "../hooks/useRoughCutTranscript";
import { ContinuousTranscript } from "./ContinuousTranscript";
import { SequencePlayer } from "./SequencePlayer";
import { TimelineBar } from "./TimelineBar";

/**
 * Feinschnitt: edit the CONTINUOUS rough-cut directly (spec §3/§4.1). Scenes are jump markers,
 * not isolated copies — clicking one navigates (seeks) the single continuous transcript+timeline.
 * Three transcript gestures (delete-selection / caret-cut / [Phase C] text-replace) are the only
 * editing affordances; there is no scene materialization (openScene) on this path.
 *
 * Features deliberately deferred to Phase B/C (per spec §10):
 *   - SceneInspector (right panel, clip-level In/Out trim)
 *   - SceneMusicControls (per-scene music overlay)
 *   - TransitionReviewPanel (VLM transition check)
 * These are removed from the default FineCutView screen. They will return in a follow-up task
 * once the contextual-inspector pattern is defined. See CONCERNS in report-A8.md.
 */
export function FineCutView({
  client,
  asset,
  roughCutId,
  segments,
  currentFrame,
  onSeek,
  onFrame,
}: {
  client: LauraClient;
  asset: Asset | null;
  roughCutId: string | null;
  segments: Segment[];
  currentFrame: number;
  /** Kept for App.tsx prop-type compatibility; unused — seek is driven by ContinuousTranscript. */
  seek: { frame: number } | null;
  onSeek: (f: number) => void;
  onFrame: (f: number) => void;
}): ReactElement {
  const rc = useRoughCutTranscript(client, roughCutId, segments);

  // Fallback scene list from useScenes if the hook hasn't populated rc.scenes yet.
  const { scenes: scenesFromHook } = useScenes(client, roughCutId);
  const jumpScenes = rc.scenes.length > 0 ? rc.scenes : scenesFromHook;

  const clips = useMemo(() => rc.clips, [rc.clips]);

  // Build a minimal Timeline shape so TimelineBar (which expects Timeline | null) gets a typed
  // value instead of an unsafe cast. The fields TimelineBar actually reads are id + clips;
  // project_id / name / kind / created_at are display-only and safe to stub.
  const syntheticTimeline = useMemo<Timeline | null>(() => {
    if (!roughCutId) return null;
    return {
      id: roughCutId,
      project_id: asset?.project_id ?? "",
      name: "Rough Cut",
      kind: "rough_cut",
      created_at: "",
      clips,
    };
  }, [roughCutId, asset?.project_id, clips]);

  if (!roughCutId) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-content-faint">
        Noch keine Szenen — erst Rough Cut ausführen.
      </div>
    );
  }

  return (
    <div className="grid min-h-0 flex-1 grid-cols-[200px_1fr] gap-px bg-bezel">
      {/* Left: scene jump navigation (read-only — no openScene materialization) */}
      <aside className="flex flex-col gap-1 overflow-auto bg-surface-0 p-2">
        {jumpScenes.map((s) => (
          <button
            key={s.id}
            type="button"
            onClick={() => onSeek(s.seq_in_frame)}
            className="truncate rounded px-2 py-1 text-left text-xs text-content-muted hover:bg-surface-2"
          >
            {s.name}
          </button>
        ))}
      </aside>

      {/* Center: continuous rough-cut player + timeline + transcript */}
      <section className="flex min-h-0 flex-col">
        <div className="flex min-h-0 flex-1 items-center justify-center bg-black/40 p-4">
          <SequencePlayer
            client={client}
            projectId={asset?.project_id ?? null}
            sequenceId={roughCutId}
            reloadKey={`${roughCutId}:${clips.length}`}
            clipsOverride={clips}
            seekTo={null}
            onFrame={onFrame}
          />
        </div>

        <TimelineBar
          client={client}
          timeline={syntheticTimeline}
          onChange={() => void rc.reload()}
          onScrub={(_assetId, frame) => onSeek(frame)}
          onSelect={() => undefined}
          segments={segments}
          currentFrame={currentFrame}
        />

        <ContinuousTranscript
          words={rc.words}
          scenes={jumpScenes}
          selection={rc.selection}
          onSelectionChange={rc.setSelection}
          onDeleteSelection={(a, b) => void rc.deleteRange(a, b)}
          onCutAt={(f) => void rc.cutAt(f)}
          onSeek={onSeek}
        />

        {rc.error && (
          <div className="px-3 py-1 text-xs text-status-err">{rc.error}</div>
        )}
      </section>
    </div>
  );
}
