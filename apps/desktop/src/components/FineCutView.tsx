import { type ReactElement, useCallback, useEffect, useMemo, useState } from "react";

import { type Asset, type BoundaryIdentity, type LauraClient, type Segment, type Timeline, type TimelineAudioClip, type VoiceoverVoice } from "../api";
import { useScenes } from "../hooks/useScenes";
import { useRoughCutTranscript } from "../hooks/useRoughCutTranscript";
import { crossfadeFix, findSameSourceEdge } from "../shared/smoothEdge";
import { ContinuousTranscript } from "./ContinuousTranscript";
import { EditorialToolsBar } from "./EditorialToolsBar";
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
  seek,
  onSeek,
  onFrame,
}: {
  client: LauraClient;
  asset: Asset | null;
  roughCutId: string | null;
  segments: Segment[];
  currentFrame: number;
  /**
   * External seek in SEQUENCE frames. A new `{ frame }` object is passed by App.tsx each time the
   * user clicks a transcript word or scene jump button (via onSeek → App.setSeek). Object-identity
   * change triggers SequencePlayer's seekTo effect — the player scrubs to that frame.
   * Flow: onSeek → App.setSeek({frame}) → new `seek` object → SequencePlayer re-seeks.
   */
  seek: { frame: number } | null;
  onSeek: (f: number) => void;
  onFrame: (f: number) => void;
}): ReactElement {
  const rc = useRoughCutTranscript(client, roughCutId, segments, asset?.id);

  // Voice picker state — selection is the only explicit editorial choice (spec §10).
  const [voices, setVoices] = useState<VoiceoverVoice[]>([]);
  const [voiceId, setVoiceId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    client
      .listVoiceoverVoices()
      .then((vs) => {
        if (!cancelled) setVoices(vs);
      })
      .catch(() => {
        if (!cancelled) setVoices([]);
      });
    return () => {
      cancelled = true;
    };
  }, [client]);

  // Pending same-source edge: recomputed after each clips update so the smooth
  // button lights up automatically after a delete that creates a jump-cut (spec §8).
  const pendingEdge = useMemo<BoundaryIdentity | null>(
    () => findSameSourceEdge(rc.clips, currentFrame),
    [rc.clips, currentFrame],
  );

  // One-tap smooth: apply a 6-frame crossfade then reload.
  const { reload: reloadRc } = rc;
  const handleSmooth = useCallback(() => {
    if (!roughCutId || !pendingEdge) return;
    void client
      .applyTransitionFix(roughCutId, pendingEdge, crossfadeFix())
      .then(() => reloadRc())
      .catch(() => undefined);
  }, [client, roughCutId, pendingEdge, reloadRc]);

  // Load audio clips from the rough-cut timeline so VO + music play in preview.
  const [audioClips, setAudioClips] = useState<TimelineAudioClip[]>([]);
  useEffect(() => {
    if (!roughCutId) {
      setAudioClips([]);
      return;
    }
    let cancelled = false;
    client
      .listTimelineAudioClips(roughCutId)
      .then((cs) => {
        if (!cancelled) setAudioClips(cs);
      })
      .catch(() => {
        if (!cancelled) setAudioClips([]);
      });
    return () => {
      cancelled = true;
    };
  }, [client, roughCutId]);

  // Fallback scene list from useScenes if the hook hasn't populated rc.scenes yet.
  const { scenes: scenesFromHook } = useScenes(client, roughCutId);
  const jumpScenes = rc.scenes.length > 0 ? rc.scenes : scenesFromHook;

  const clips = useMemo(() => rc.clips, [rc.clips]);

  // Derive synthetic-effect labels from audio clips in the timeline.
  // replace_original → a VO/lipsync clip replaced the original audio; mute_original → VO on top.
  // These labels appear in the always-on disclosure strip (spec §7).
  const syntheticEffects = useMemo<string[]>(() => {
    const effects: string[] = [];
    if (audioClips.some((c) => c.mix_mode === "replace_original")) effects.push("VO");
    if (audioClips.some((c) => c.mix_mode === "mute_original")) effects.push("Lipsync");
    return effects;
  }, [audioClips]);

  // Asset list for the embedded ReenactPanel: unique asset ids from lane-0 clips.
  const toolbarAssets = useMemo<{ id: string; display_name: string }[]>(() => {
    const seen = new Set<string>();
    const result: { id: string; display_name: string }[] = [];
    for (const cl of clips) {
      if (!seen.has(cl.asset_id)) {
        seen.add(cl.asset_id);
        result.push({
          id: cl.asset_id,
          display_name: asset?.display_name ?? cl.asset_id,
        });
      }
    }
    return result;
  }, [clips, asset?.display_name]);

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
            seekTo={seek}
            onFrame={onFrame}
            audioClips={audioClips}
          />
        </div>

        <EditorialToolsBar
          client={client}
          projectId={asset?.project_id ?? null}
          timelineId={roughCutId}
          assets={toolbarAssets}
          voices={voices}
          voiceId={voiceId}
          onVoiceChange={setVoiceId}
          pendingEdge={pendingEdge}
          onSmooth={handleSmooth}
          syntheticEffects={syntheticEffects}
          currentSeqFrame={currentFrame}
          rateNum={asset?.rate_num ?? 30}
          rateDen={asset?.rate_den ?? 1}
          onChange={() => void rc.reload()}
        />

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
          onReplaceText={(s, e, t) => void rc.replaceSpanText(s, e, t, voiceId ?? "")}
        />

        {rc.error && (
          <div className="px-3 py-1 text-xs text-status-err">{rc.error}</div>
        )}
      </section>
    </div>
  );
}
