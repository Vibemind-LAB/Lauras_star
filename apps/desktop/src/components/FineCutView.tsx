import { type ReactElement, useEffect, useState } from "react";

import { type Asset, type LauraClient, type Segment } from "../api";
import { useSceneTimeline } from "../hooks/useSceneTimeline";
import { useScenes } from "../hooks/useScenes";
import { Player } from "./Player";
import { SceneInspector } from "./SceneInspector";
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

  // Auto-select the first scene once the list is loaded.
  useEffect(() => {
    if (!selectedSceneId && scenes[0]) {
      setSelectedSceneId(scenes[0].id);
    }
  }, [scenes, selectedSceneId]);

  const scene = useSceneTimeline(client, selectedSceneId);

  // The clip currently being fine-trimmed (defaults to the scene's first clip).
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);
  const clips = scene.timeline?.clips ?? [];
  const selectedClip = clips.find((c) => c.id === selectedClipId) ?? clips[0] ?? null;

  if (scenes.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-slate-600">
        Noch keine Szenen — erst Rough Cut ausführen.
      </div>
    );
  }

  return (
    <div className="grid min-h-0 flex-1 grid-cols-[200px_1fr_340px] gap-px bg-edge">
      {/* Left: scene list */}
      <aside className="flex flex-col gap-1 overflow-auto bg-ink p-2">
        {scenes.map((s) => (
          <button
            key={s.id}
            type="button"
            onClick={() => setSelectedSceneId(s.id)}
            className={`truncate rounded px-2 py-1 text-left text-xs ${
              s.id === selectedSceneId
                ? "bg-sky-700 text-white"
                : "text-slate-300 hover:bg-slate-700"
            }`}
          >
            {s.name}
          </button>
        ))}
      </aside>

      {/* Center: player + timeline + transcript */}
      <section className="flex min-h-0 flex-col">
        <div className="border-b border-edge bg-panel px-3 py-1 text-[11px] text-slate-400">
          Feinschnitt: Szene links wählen · Clip in der Timeline anklicken → rechts In/Out
          frame-genau trimmen · ✂ am Transkript schneidet Wörter (Ripple).
        </div>
        <div className="flex min-h-0 flex-1 items-center justify-center bg-black/40 p-4">
          {asset ? (
            <Player asset={asset} seekTo={seek} onFrame={onFrame} />
          ) : (
            <span className="text-xs text-slate-600">Kein Medium gewählt.</span>
          )}
        </div>

        <TimelineBar
          client={client}
          timeline={scene.timeline}
          onChange={() => void scene.reload()}
          onScrub={(_assetId, frame) => onSeek(frame)}
          onSelect={setSelectedClipId}
        />

        <TranscriptBar
          client={client}
          assetId={asset?.id ?? null}
          assetName={asset?.display_name ?? null}
          segments={segments}
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

        {scene.error && (
          <div className="px-3 py-1 text-xs text-red-400">{scene.error}</div>
        )}
      </section>

      {/* Right: frame-accurate In/Out cut editor for the selected clip */}
      <aside className="flex min-h-0 flex-col overflow-auto bg-ink p-2">
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
          <p className="p-2 text-[11px] text-slate-500">
            Clip in der Timeline wählen, um In/Out frame-genau zu schneiden.
          </p>
        )}
      </aside>
    </div>
  );
}
