import { type ReactElement, useEffect, useState } from "react";

import { type Asset, type LauraClient, type Scene, type Segment, type TimelineClip } from "../api";

function clipsInScene(scene: Scene, clips: TimelineClip[]): TimelineClip[] {
  return clips.filter(
    (c) =>
      c.seq_in_frame >= scene.seq_in_frame &&
      c.seq_in_frame < scene.seq_out_frame_exclusive,
  );
}

/** Clip boundary nearest the middle of the scene, or null if it has <2 clips. */
function midBoundary(inScene: TimelineClip[]): number | null {
  if (inScene.length < 2) return null;
  return inScene[Math.floor(inScene.length / 2)].seq_in_frame;
}

function excerpt(_scene: Scene, inScene: TimelineClip[], segments: Segment[]): string {
  const lo = Math.min(...inScene.map((c) => c.src_in_frame), Number.MAX_SAFE_INTEGER);
  const hi = Math.max(...inScene.map((c) => c.src_out_frame_exclusive), 0);
  const text = segments
    .filter((s) => s.start_frame < hi && s.end_frame > lo)
    .map((s) => s.text)
    .join(" ")
    .trim();
  return text.length > 90 ? `${text.slice(0, 90)}…` : text;
}

function Thumb({
  client,
  assetId,
  frame,
}: {
  client: LauraClient;
  assetId: string;
  frame: number;
}): ReactElement {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    client
      .assetFrameUrl(assetId, Math.max(0, frame))
      .then((u) => {
        if (!active) {
          URL.revokeObjectURL(u);
          return;
        }
        objectUrl = u;
        setUrl(u);
      })
      .catch(() => {
        /* colour fallback */
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [client, assetId, frame]);
  return (
    <span className="h-9 w-16 shrink-0 overflow-hidden rounded border border-edge">
      {url ? (
        <img src={url} alt="" className="h-full w-full object-cover" />
      ) : (
        <span className="block h-full w-full bg-sky-700/40" />
      )}
    </span>
  );
}

function SceneCard({
  client,
  asset,
  scene,
  inScene,
  excerptText,
  canMerge,
  onSplit,
  onMerge,
  onRename,
  onSeek,
}: {
  client: LauraClient;
  asset: Asset;
  scene: Scene;
  inScene: TimelineClip[];
  excerptText: string;
  canMerge: boolean;
  onSplit: (sceneId: string, atSeqFrame: number) => void;
  onMerge: (sceneId: string) => void;
  onRename: (sceneId: string, name: string) => void;
  onSeek: (frame: number) => void;
}): ReactElement {
  const splitAt = midBoundary(inScene);
  return (
    <div className="flex w-56 shrink-0 flex-col gap-1 rounded border border-edge p-2">
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={() => onSeek(scene.seq_in_frame)}
          title="Zur Szene springen"
          className="truncate text-left text-xs font-medium text-slate-200 hover:underline"
        >
          {scene.name}
        </button>
        <span className="ml-auto flex gap-1">
          {splitAt !== null && (
            <button
              type="button"
              title="Szene teilen"
              onClick={() => onSplit(scene.id, splitAt)}
              className="rounded px-1 text-xs text-slate-300 hover:bg-slate-700"
            >
              ✂
            </button>
          )}
          {canMerge && (
            <button
              type="button"
              title="Mit nächster Szene zusammenführen"
              onClick={() => onMerge(scene.id)}
              className="rounded px-1 text-xs text-slate-300 hover:bg-slate-700"
            >
              ⇄
            </button>
          )}
        </span>
      </div>
      <div className="flex gap-1 overflow-x-auto">
        {inScene.slice(0, 4).map((c) => (
          <Thumb key={c.id} client={client} assetId={asset.id} frame={c.src_in_frame} />
        ))}
      </div>
      <input
        defaultValue={scene.name}
        onBlur={(e) => {
          if (e.target.value && e.target.value !== scene.name) onRename(scene.id, e.target.value);
        }}
        className="w-full rounded bg-slate-800 px-1 py-0.5 text-[11px] text-slate-200"
        aria-label="Szenenname"
      />
      <p className="text-[11px] text-slate-500">{excerptText || "—"}</p>
    </div>
  );
}

export function SceneStrip({
  client,
  asset,
  scenes,
  clips,
  segments,
  onSplit,
  onMerge,
  onRename,
  onSeek,
}: {
  client: LauraClient;
  asset: Asset;
  scenes: Scene[];
  clips: TimelineClip[];
  segments: Segment[];
  onSplit: (sceneId: string, atSeqFrame: number) => void;
  onMerge: (sceneId: string) => void;
  onRename: (sceneId: string, name: string) => void;
  onSeek: (frame: number) => void;
}): ReactElement {
  if (scenes.length === 0) {
    return (
      <div className="flex h-24 items-center justify-center text-xs text-slate-600">
        Noch keine Szenen — wähle ein Asset und erzeuge Szenen.
      </div>
    );
  }
  return (
    <div className="flex w-full gap-2 overflow-x-auto p-2">
      {scenes.map((scene, i) => {
        const inScene = clipsInScene(scene, clips);
        return (
          <SceneCard
            key={scene.id}
            client={client}
            asset={asset}
            scene={scene}
            inScene={inScene}
            excerptText={excerpt(scene, inScene, segments)}
            canMerge={i < scenes.length - 1}
            onSplit={onSplit}
            onMerge={onMerge}
            onRename={onRename}
            onSeek={onSeek}
          />
        );
      })}
    </div>
  );
}
