import { type ReactElement, useCallback, useEffect, useMemo, useState } from "react";

import { type Asset, type BoundaryIdentity, type LauraClient, type Segment, type Timeline, type TimelineAudioClip, type VoiceoverVoice } from "../api";
import { useScenes } from "../hooks/useScenes";
import { useRoughCutTranscript } from "../hooks/useRoughCutTranscript";
import { useJobStatus } from "../hooks/useJobStatus";
import { crossfadeFix, findFirstSameSourceEdge } from "../shared/smoothEdge";
import { groupCutWordsByScene } from "../shared/sceneTranscript";
import { ContinuousTranscript } from "./ContinuousTranscript";
import { EditorialToolsBar } from "./EditorialToolsBar";
import { SequencePlayer } from "./SequencePlayer";
import { TimelineBar } from "./TimelineBar";
import { TranscriptStatusBanner } from "./TranscriptStatusBanner";

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
  transcriptNote = null,
  transcriptBusy = false,
  onGenerateTranscript = () => undefined,
  currentFrame,
  seek,
  onSeek,
  onFrame,
}: {
  client: LauraClient;
  asset: Asset | null;
  roughCutId: string | null;
  segments: Segment[];
  transcriptNote?: string | null;
  transcriptBusy?: boolean;
  onGenerateTranscript?: () => void;
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

  // Keyboard shortcut: Ctrl+Z → undo, Ctrl+Shift+Z / Ctrl+Y → redo.
  // Focus-guarded: no-op when a text input or textarea is focused.
  useEffect(() => {
    function onKey(e: KeyboardEvent): void {
      const t = e.target;
      if (t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement) return;
      const mod = e.ctrlKey || e.metaKey;
      if (!mod) return;
      const k = e.key.toLowerCase();
      if (k === "z" && !e.shiftKey) { e.preventDefault(); void rc.undo(); }
      else if ((k === "z" && e.shiftKey) || k === "y") { e.preventDefault(); void rc.redo(); }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rc]);

  // Voice picker state — selection is the only explicit editorial choice (spec §10).
  const [voices, setVoices] = useState<VoiceoverVoice[]>([]);
  const [voiceId, setVoiceId] = useState<string | null>(null);
  // Collapse the scene-jump rail to widen the editor (panels hideable via click).
  const [scenesCollapsed, setScenesCollapsed] = useState(false);

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

  // Pending same-source edge: scan ALL lane-0 boundaries for a contiguous
  // same-source jump-cut, independent of currentFrame. This ensures the
  // "Übergang glätten" button lights up immediately after any delete that
  // creates such an edge, without requiring the user to seek to the cut
  // (spec §8 — Fix 3).
  const pendingEdge = useMemo<BoundaryIdentity | null>(
    () => findFirstSameSourceEdge(rc.clips),
    [rc.clips],
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
  // Extracted into a useCallback so it can be re-triggered on VO job completion
  // without changing [client, roughCutId] (Fix 1).
  const [audioClips, setAudioClips] = useState<TimelineAudioClip[]>([]);
  const reloadAudioClips = useCallback(() => {
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

  useEffect(() => {
    return reloadAudioClips();
  }, [reloadAudioClips]);

  // Track the most recently dispatched VO job and re-fetch audio clips when it
  // succeeds so the new VO becomes audible in preview and the disclosure strip
  // updates without a manual remount (Fix 1).
  const { jobStatus: voJobStatus, isRunning: voJobRunning } = useJobStatus(
    client,
    rc.lastVoJobId,
  );
  useEffect(() => {
    if (voJobStatus?.status === "succeeded") {
      reloadAudioClips();
    }
  }, [voJobStatus, reloadAudioClips]);

  // Fallback scene list from useScenes if the hook hasn't populated rc.scenes yet.
  const { scenes: scenesFromHook } = useScenes(client, roughCutId);
  const jumpScenes = rc.scenes.length > 0 ? rc.scenes : scenesFromHook;

  // First few transcript words per scene, so the scene chips are scannable ("Szene 3 · also hier…")
  // instead of generic labels. Empty until the transcript exists.
  const sceneFirstWords = useMemo(() => {
    const map = new Map<string, string>();
    for (const g of groupCutWordsByScene(rc.words, jumpScenes)) {
      const text = g.words.slice(0, 6).map((w) => w.text).join(" ");
      if (text) map.set(g.scene.id, text);
    }
    return map;
  }, [rc.words, jumpScenes]);

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
    <div className="flex min-h-0 flex-1 flex-col bg-bezel">
      {/* Scene navigation as a horizontal, scrollable slider (hideable via the toggle).
          Replaces the tall left column so scenes don't dominate the window height and you can
          slide through them. Click a scene chip to seek the continuous rough-cut. */}
      <div className="flex shrink-0 items-center gap-1 border-b border-bezel bg-surface-0 px-2 py-1.5">
        <button
          type="button"
          onClick={() => setScenesCollapsed((v) => !v)}
          title={scenesCollapsed ? "Szenen einblenden" : "Szenen ausblenden"}
          aria-label={scenesCollapsed ? "Szenen einblenden" : "Szenen ausblenden"}
          className="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-content-faint hover:bg-surface-2 hover:text-content-strong"
        >
          {scenesCollapsed ? "Szenen ▸" : "Szenen ◂"}
        </button>
        {!scenesCollapsed && (
          <div className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
            {jumpScenes.length === 0 ? (
              <span className="px-1 text-[11px] text-content-faint">Noch keine Szenen</span>
            ) : (
              jumpScenes.map((s) => {
                const preview = sceneFirstWords.get(s.id);
                return (
                  <button
                    key={s.id}
                    type="button"
                    onClick={() => onSeek(s.seq_in_frame)}
                    title={preview ? `${s.name} · ${preview}` : s.name}
                    className="flex shrink-0 items-baseline gap-1 rounded bg-surface-2 px-2 py-1 text-[11px] hover:bg-accent/30"
                  >
                    <span className="font-medium text-content-muted">{s.name}</span>
                    {preview && (
                      <span className="max-w-[9rem] truncate text-content-faint">{preview}</span>
                    )}
                  </button>
                );
              })
            )}
          </div>
        )}
      </div>

      {/* Continuous rough-cut player + timeline + transcript (now full width). */}
      <section className="flex min-h-0 flex-1 flex-col">
        <div className="flex h-[42vh] min-h-[240px] shrink-0 items-center justify-center overflow-hidden bg-black/40 p-3">
          <SequencePlayer
            client={client}
            projectId={asset?.project_id ?? null}
            sequenceId={roughCutId}
            reloadKey={`${roughCutId}:${clips.length}`}
            clipsOverride={clips}
            seekTo={seek}
            onFrame={onFrame}
            audioClips={audioClips}
            rateNum={asset?.rate_num ?? 30}
            rateDen={asset?.rate_den ?? 1}
          />
        </div>
        {/* Inline VO progress indicator — visible while VO job is running (Fix 1). */}
        {voJobRunning && (
          <div className="px-3 py-1 text-xs text-accent" aria-live="polite">
            Voiceover wird generiert…
          </div>
        )}

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
          canUndo={rc.canUndo}
          canRedo={rc.canRedo}
          undoLabel={rc.undoLabel}
          redoLabel={rc.redoLabel}
          onUndo={() => void rc.undo()}
          onRedo={() => void rc.redo()}
        />

        <TimelineBar
          client={client}
          timeline={syntheticTimeline}
          onChange={() => {
            void rc.reload();
            reloadAudioClips();
          }}
          onScrub={(_assetId, frame) => onSeek(frame)}
          onSelect={() => undefined}
          segments={segments}
          currentFrame={currentFrame}
          currentFrameDomain="sequence"
        />

        {rc.words.length === 0 && (
          <TranscriptStatusBanner
            note={transcriptNote}
            busy={transcriptBusy}
            onGenerate={onGenerateTranscript}
          />
        )}

        <div className="min-h-0 flex-1">
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
        </div>

        {rc.error && (
          <div className="px-3 py-1 text-xs text-status-err">{rc.error}</div>
        )}
      </section>
    </div>
  );
}
