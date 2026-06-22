import { type ReactElement, useEffect, useMemo, useState } from "react";

import { type Asset, type LauraClient, type Segment, type TimelineClip } from "../api";
import { useSceneTimeline } from "../hooks/useSceneTimeline";
import { TransitionReviewPanel } from "./TransitionReviewPanel";
import { useScenes } from "../hooks/useScenes";
import { projectCutWords } from "../shared/transcriptProjection";
import { SceneInspector } from "./SceneInspector";
import { SequencePlayer } from "./SequencePlayer";
import { SceneMusicControls } from "./SceneMusicControls";
import { TimelineBar } from "./TimelineBar";
import { TranscriptBar } from "./TranscriptBar";

/**
 * Feinschnitt per-scene editor.
 *
 * Composes the existing Player, TimelineBar, and TranscriptBar against the
 * materialized scene timeline opened via useSceneTimeline. SceneInspector is
 * omitted in 4a because wiring it requires a selected-clip state sourced from
 * the scene timeline's clips, which is out of scope here and will be added in a
 * follow-up (see plan note).
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
  seek: { frame: number } | null;
  onSeek: (f: number) => void;
  onFrame: (f: number) => void;
}): ReactElement {
  const { scenes, reload } = useScenes(client, roughCutId);
  const [selectedSceneId, setSelectedSceneId] = useState<string | null>(null);
  const selectedScene = scenes.find((s) => s.id === selectedSceneId);

  // Auto-select the first scene once the list is loaded, and recover when a
  // regenerated scene list invalidates the previous selection.
  useEffect(() => {
    if (selectedSceneId && !scenes.some((s) => s.id === selectedSceneId)) {
      setSelectedSceneId(scenes[0]?.id ?? null);
      setSelectedClipId(null);
      return;
    }
    if (!selectedSceneId && scenes[0]) {
      setSelectedSceneId(scenes[0].id);
    }
  }, [scenes, selectedSceneId]);

  const scene = useSceneTimeline(client, selectedSceneId);

  // The clip currently being fine-trimmed (defaults to the scene's first clip).
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);
  // Stable reference: only changes when the timeline object changes (a reload),
  // not on every render — so seekToSeq/cutWords don't recompute spuriously and
  // SequencePlayer isn't re-seeked mid-playback.
  const clips = useMemo(() => scene.timeline?.clips ?? [], [scene.timeline]);
  const selectedClip = clips.find((c) => c.id === selectedClipId) ?? clips[0] ?? null;

  // Seek to the first clip's in-point when the SCENE changes, but not on every
  // clips-array identity change (e.g. after a trim reload). Keying on
  // scene.timeline?.id (or selectedSceneId as fallback) keeps the playhead stable
  // while the user fine-tunes In/Out points in SceneInspector.
  const timelineId = scene.timeline?.id ?? null;
  useEffect(() => {
    if (!selectedSceneId || !timelineId) return;
    const firstClip = clips[0];
    if (!firstClip) return;
    onSeek(firstClip.src_in_frame);
    // Intentionally NOT including `clips` — we only want to seek on scene/timeline change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSceneId, timelineId, onSeek]);

  // ---------------------------------------------------------------------------
  // SEQ <-> SOURCE frame conversions (lane-0 base clips of the scene timeline)
  //
  // v1 limitation: the round-trip is unambiguous only when each source range
  // appears once in cut order. Reordered or duplicated clips may place the
  // playhead on the first matching clip when converting SOURCE -> SEQ.
  // ---------------------------------------------------------------------------

  /**
   * Convert a SEQUENCE frame to a SOURCE (asset) frame.
   * Returns null when there are no clips or the frame is out of range.
   */
  function seqToSrc(clips: TimelineClip[], seqFrame: number): number | null {
    for (const c of clips) {
      if (seqFrame >= c.seq_in_frame && seqFrame < c.seq_out_frame_exclusive) {
        return c.src_in_frame + (seqFrame - c.seq_in_frame);
      }
    }
    return null;
  }

  /**
   * Convert a SOURCE (asset) frame to a SEQUENCE frame.
   * Returns null when no clip contains that source frame.
   */
  function srcToSeq(clips: TimelineClip[], srcFrame: number): number | null {
    for (const c of clips) {
      if (srcFrame >= c.src_in_frame && srcFrame < c.src_out_frame_exclusive) {
        return c.seq_in_frame + (srcFrame - c.src_in_frame);
      }
    }
    return null;
  }

  /**
   * Translate the incoming SOURCE-frame seek into a SEQUENCE-frame seek for
   * SequencePlayer. Produces a new object when the result changes so that
   * SequencePlayer's seekTo effect fires correctly.
   */
  const seekToSeq = useMemo<{ frame: number } | null>(() => {
    if (!seek || clips.length === 0) return null;
    const seqFrame = srcToSeq(clips, seek.frame);
    if (seqFrame === null) return null;
    return { frame: seqFrame };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seek, clips]);

  /**
   * Called by SequencePlayer on every frame tick. Converts the SEQUENCE frame
   * back to a SOURCE frame so that TimelineBar, SceneInspector, and
   * TranscriptBar continue to receive SOURCE frames unchanged.
   */
  function handleSeqFrame(seqFrame: number): void {
    if (clips.length === 0) return;
    const srcFrame = seqToSrc(clips, seqFrame);
    if (srcFrame !== null) onFrame(srcFrame);
  }

  // Project the source transcript onto the cut: only words surviving the trim, in cut order.
  const cutWords = useMemo(() => projectCutWords(segments, clips), [segments, clips]);

  if (scenes.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-content-faint">
        Noch keine Szenen — erst Rough Cut ausführen.
      </div>
    );
  }

  return (
    <div className="grid min-h-0 flex-1 grid-cols-[200px_1fr_340px] gap-px bg-bezel">
      {/* Left: scene list */}
      <aside className="flex flex-col gap-1 overflow-auto bg-surface-0 p-2">
        {scenes.map((s) => (
          <button
            key={s.id}
            type="button"
            onClick={() => {
              setSelectedSceneId(s.id);
              setSelectedClipId(null);
            }}
            className={`truncate rounded px-2 py-1 text-left text-xs ${
              s.id === selectedSceneId
                ? "bg-sky-700 text-white"
                : "text-content-muted hover:bg-surface-2"
            }`}
          >
            {s.name}
          </button>
        ))}
      </aside>

      {/* Center: player + timeline + transcript */}
      <section className="flex min-h-0 flex-col">
        <div className="border-b border-bezel bg-surface-1 px-3 py-1 text-[11px] text-content-muted">
          Feinschnitt: Szene links wählen · Clip in der Timeline anklicken → rechts In/Out
          frame-genau trimmen · ✂ am Transkript schneidet Wörter (Ripple).
        </div>
        <div className="flex min-h-0 flex-1 items-center justify-center bg-black/40 p-4">
          {scene.timeline ? (
            <SequencePlayer
              client={client}
              projectId={asset?.project_id ?? null}
              sequenceId={scene.timeline.id}
              reloadKey={scene.timeline.id}
              clipsOverride={clips}
              seekTo={seekToSeq}
              onFrame={handleSeqFrame}
            />
          ) : (
            <span className="text-xs text-content-faint">
              {asset ? "Szene wird geladen…" : "Kein Medium gewählt."}
            </span>
          )}
        </div>

        <TimelineBar
          client={client}
          timeline={scene.timeline}
          onChange={() => void scene.reload()}
          onScrub={(_assetId, frame) => onSeek(frame)}
          onSelect={setSelectedClipId}
          segments={segments}
          currentFrame={currentFrame}
        />

        <TranscriptBar
          client={client}
          assetId={asset?.id ?? null}
          assetName={asset?.display_name ?? null}
          segments={segments}
          cutWords={cutWords}
          note={null}
          currentFrame={currentFrame}
          onSeek={onSeek}
          canAppend={false}
          onAppendSegment={() => undefined}
          onDeleteWords={(a, b) => void scene.deleteWords(a, b)}
        />

        {selectedScene && (
          <SceneMusicControls
            client={client}
            projectId={asset?.project_id ?? null}
            scene={selectedScene}
            onChange={() => void reload()}
          />
        )}

        <TransitionReviewPanel client={client} timelineId={scene.timeline?.id ?? null} />

        {scene.error && (
          <div className="px-3 py-1 text-xs text-status-err">{scene.error}</div>
        )}
      </section>

      {/* Right: frame-accurate In/Out cut editor for the selected clip */}
      <aside className="flex min-h-0 flex-col overflow-auto bg-surface-0 p-2">
        {asset && selectedClip && scene.timeline ? (
          <SceneInspector
            client={client}
            clip={selectedClip}
            asset={asset}
            timelineId={scene.timeline.id}
            onChange={() => void scene.reload()}
            onSeek={onSeek}
          />
        ) : (
          <p className="p-2 text-[11px] text-content-faint">
            Clip in der Timeline wählen, um In/Out frame-genau zu schneiden.
          </p>
        )}
      </aside>
    </div>
  );
}


