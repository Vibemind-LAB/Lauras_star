import { type ReactElement, useCallback, useState } from "react";

import { type Asset, type LauraClient, type Segment, type Timeline } from "../api";
import { useScenes } from "../hooks/useScenes";
import { Player } from "./Player";
import { SceneStrip } from "./SceneStrip";

export function RoughCutView({
  client,
  projectId,
  asset,
  roughCut,
  segments,
  onRoughCutChange,
  seek,
  currentFrame,
  onSeek,
  onFrame,
}: {
  client: LauraClient;
  projectId: string | null;
  asset: Asset | null;
  roughCut: Timeline | null;
  segments: Segment[];
  onRoughCutChange: () => Promise<void>;
  seek: { frame: number } | null;
  currentFrame: number;
  onSeek: (frame: number) => void;
  onFrame: (frame: number) => void;
}): ReactElement {
  const scenes = useScenes(client, roughCut?.id ?? null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onGenerate = useCallback(async () => {
    if (!asset || !roughCut || !projectId) return;
    setBusy(true);
    setError(null);
    try {
      if (roughCut.clips.length === 0) {
        await client.buildRoughCutFromShots(projectId, asset.id, roughCut.id);
        await onRoughCutChange();
      }
      await scenes.generate(asset.id);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }, [asset, roughCut, projectId, client, onRoughCutChange, scenes]);

  if (!asset || !roughCut) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-slate-600">
        Wähle ein Asset (in Import), um Szenen zu erzeugen.
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex min-h-0 flex-1 items-center justify-center bg-black/40 p-2">
        <Player asset={asset} seekTo={seek} onFrame={onFrame} />
      </div>
      <div className="flex items-center gap-2 border-t border-edge px-3 py-2">
        <button
          type="button"
          onClick={() => void onGenerate()}
          disabled={busy}
          className="rounded bg-sky-600 px-3 py-1 text-xs text-white disabled:opacity-40"
        >
          {busy ? "Erzeuge…" : "Szenen erzeugen"}
        </button>
        <span className="text-[11px] text-slate-500">Frame {currentFrame}</span>
        {(error ?? scenes.error) && (
          <span className="text-[11px] text-red-400">{error ?? scenes.error}</span>
        )}
      </div>
      <div className="border-t border-edge">
        <SceneStrip
          client={client}
          asset={asset}
          scenes={scenes.scenes}
          clips={roughCut.clips}
          segments={segments}
          onSplit={(id, at) => void scenes.split(id, at)}
          onMerge={(id) => void scenes.merge(id)}
          onRename={(id, name) => void scenes.rename(id, name)}
          onSeek={onSeek}
        />
      </div>
    </div>
  );
}
